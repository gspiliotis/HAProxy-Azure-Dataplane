# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Setup — install both providers and dev dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"

# Run all tests
python -m pytest tests/ -v

# Run a single test file or test
python -m pytest tests/test_config.py -v
python -m pytest tests/test_config.py::TestLoadConfig::test_minimal_valid_config -v

# Validate a config file
haproxy-cloud-discovery --validate -c config.yaml

# Single discovery cycle (no loop)
haproxy-cloud-discovery --once -c config.yaml
```

## Architecture

This is an external Python daemon that polls a cloud provider (Azure or AWS) for tagged instances, then reconciles HAProxy backends and servers via the Dataplane REST API.

Exactly one provider is active per deployment, selected by which config section (`azure:` or `aws:`) is populated. Both providers implement the same `CloudDiscoveryClient` Protocol and return `DiscoveredInstance` objects.

### Data Flow (one polling cycle)

```
Daemon._build_client()  →  client.discover_all()  →  TagFilter.apply()  →  group_instances()
     →  ChangeDetector.detect()  →  Reconciler.reconcile()
```

The `Daemon` class in `daemon.py` orchestrates this loop with signal handling (SIGTERM/SIGINT for shutdown, SIGHUP to reset state) and exponential backoff on failures.

### Key Design Decisions

- **Synchronous**: No async. Polling intervals are 30s+, so async adds complexity with no benefit.
- **No persistent state**: In-memory only. On restart, the first cycle reconciles everything from scratch.
- **Never auto-delete backends**: Removed services get all servers set to maintenance mode (127.0.0.1:80). This prevents accidental deletion from partial cloud API responses.
- **One transaction per cycle**: All changed services are updated in a single atomic Dataplane API transaction.
- **Version-conflict retry**: The reconciler retries up to 3 times on HTTP 409 (another process modified HAProxy config).
- **Server slot pre-allocation**: Backends always have at least `base` (default 10) server slots. Unused slots sit in maintenance mode, ready for scale-up without config changes.
- **AZ-aware server weighting**: When `haproxy.availability_zone` is configured, active servers get `weight`/`backup` based on AZ proximity. The `HAProxy:Instance:AZperc` tag (1-99) controls proportional cross-AZ weighting; without it, cross-AZ servers are marked as backup. Instances with no zone are treated as same-AZ.
- **AZ as string**: `availability_zone` is always a `str | None`. Azure uses `"1"`, `"2"`, `"3"`; AWS uses full zone names like `"us-east-1a"`. Comparison is always string equality.
- **Cookie on active servers**: All active server lines include `cookie: <server_name>` for session persistence support.
- **Per-service backend options**: `haproxy.backend_options` is a dict keyed by service name, merged into the Dataplane API create-backend payload.

### Two-Package Boundary

- `discovery/` talks to the cloud provider only (Azure SDK or boto3). Returns `DiscoveredInstance` objects.
- `haproxy/` talks to HAProxy only (REST via `requests`). Consumes `DiscoveredService` objects.

They share data through the models in `discovery/models.py` — the `Daemon` is the only thing that connects both sides.

### Provider Selection and Factory

`Daemon._build_client()` reads `config.azure` / `config.aws` and instantiates the correct client:
- `AzureClient(azure_config, tags_config)` — Azure VMs + VMSS
- `AWSClient(aws_config, tags_config)` — EC2 instances + Auto Scaling Groups

Both satisfy the `CloudDiscoveryClient` Protocol defined in `discovery/__init__.py`.

### Config Loading

`config.py` uses frozen dataclasses nested via `_build_nested()`. Supports `${ENV_VAR}` interpolation in YAML values. `_get_dataclass_type()` handles `X | None` union annotations (both PEP 604 `types.UnionType` and `typing.Union`). Validation in `_validate()` enforces exactly one provider.

### Transaction Pattern

`haproxy/transaction.py` provides a context manager: commits if `mark_changed()` was called, deletes the empty transaction otherwise, and aborts (deletes) on exception. The reconciler wraps its entire cycle in one transaction.

### Tag Convention

The same tags are used for both providers:
- `HAProxy:Service:Name` — maps to backend name
- `HAProxy:Service:Port` — maps to backend port
- `HAProxy:Instance:Port` — optional per-instance port override
- `HAProxy:Instance:AZperc` — optional AZ weight percentage (1-99) for cross-AZ traffic splitting

### Backend Naming

`DiscoveredService.backend_name(prefix, separator)` returns `{prefix}{sep}{name}{sep}{port}{sep}{region}`.

Examples: `azure-myapp-8080-eastus`, `aws-myapp-8080-us-east-2`.

The `region` field on `DiscoveredInstance` is:
- Azure: the Azure region string (e.g. `"eastus"`)
- AWS: derived from the AZ by stripping the trailing letter (`"us-east-1a"` → `"us-east-1"`)

### AZ Routing Logic (in `reconciler.py`)

The `_active_server_data` method computes per-server options when `haproxy.availability_zone` is set:
- Parses the instance's `AZperc` tag via `_parse_az_perc()` (returns `int` in 1-99 or `None`)
- `same_az` is true when the instance has no zone OR its zone string matches HAProxy's AZ string
- With `AZperc`: same-AZ gets `weight = 100 - AZperc`, cross-AZ gets `weight = AZperc`
- Without `AZperc`: cross-AZ gets `backup = "enabled"`, same-AZ has no extra options
- `_ensure_backend` merges `backend_options[service_name]` into the create-backend payload

### AWS Discovery (in `discovery/aws_client.py`)

- `AWSClient` creates a `boto3.Session` (with optional named profile) and uses the EC2 and Auto Scaling clients.
- `discover_all()` calls `_discover_ec2()` then `_discover_asg(known_ids)`.
- EC2 filter: instances with `tag-key=HAProxy:Service:Name` and `instance-state-name=running`.
- ASG: `describe_auto_scaling_groups` filtered by tag-key; resolves member IPs via `describe_instances` in batches of 100. Instances already seen via EC2 discovery are skipped (deduplication by instance ID).
- Region is derived from the AZ: `availability_zone[:-1]` (strips trailing letter).

### Testing

Tests use:
- `responses` library to mock HTTP calls to the Dataplane API
- `moto[ec2,autoscaling]` to mock AWS API calls (no live AWS needed)
- `unittest.mock` for Azure SDK and reconciler internals

No live Azure, AWS, or HAProxy connections are needed to run the test suite.
