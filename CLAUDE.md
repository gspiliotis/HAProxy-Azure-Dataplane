# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Setup (from haproxy-azure-discovery/)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -v

# Run a single test file or test
python -m pytest tests/test_config.py -v
python -m pytest tests/test_config.py::TestLoadConfig::test_minimal_valid_config -v

# Validate a config file
haproxy-azure-discovery --validate -c config.yaml

# Single discovery cycle (no loop)
haproxy-azure-discovery --once -c config.yaml
```

## Architecture

This is an external Python daemon that polls Azure for VMs/VMSS tagged for HAProxy service discovery, then reconciles HAProxy backends and servers via the Dataplane REST API. It is the Azure equivalent of the AWS EC2 service discovery built into HAProxy's Go codebase — but runs as a sidecar instead of being embedded.

### Data Flow (one polling cycle)

```
AzureClient.discover_all()  →  TagFilter.apply()  →  group_instances()
     →  ChangeDetector.detect()  →  Reconciler.reconcile()
```

The `Daemon` class in `daemon.py` orchestrates this loop with signal handling (SIGTERM/SIGINT for shutdown, SIGHUP to reset state) and exponential backoff on failures.

### Key Design Decisions

- **Synchronous**: No async. Polling intervals are 30s+, so async adds complexity with no benefit.
- **No persistent state**: In-memory only. On restart, the first cycle reconciles everything (same as AWS Go implementation).
- **Never auto-delete backends**: Removed services get all servers set to maintenance mode (127.0.0.1:80). This prevents accidental deletion from partial Azure API responses.
- **One transaction per cycle**: All changed services are updated in a single atomic Dataplane API transaction.
- **Version-conflict retry**: The reconciler retries up to 3 times on HTTP 409 (another process modified HAProxy config).
- **Server slot pre-allocation**: Backends always have at least `base` (default 10) server slots. Unused slots sit in maintenance mode, ready for scale-up without config changes.

### Two-Package Boundary

- `discovery/` talks to Azure only (SDK credentials, compute/network clients). Returns `DiscoveredInstance` objects.
- `haproxy/` talks to HAProxy only (REST via `requests`). Consumes `AzureService` objects.

They share data through the models in `discovery/models.py` — the `Daemon` is the only thing that connects both sides.

### Config Loading

`config.py` uses frozen dataclasses nested via `_build_nested()`. Supports `${ENV_VAR}` interpolation in YAML values (used for secrets like `AZURE_SUBSCRIPTION_ID`, `HAPROXY_DATAPLANE_PASSWORD`). Validation runs after loading.

### Transaction Pattern

`haproxy/transaction.py` provides a context manager: commits if `mark_changed()` was called, deletes the empty transaction otherwise, and aborts (deletes) on exception. The reconciler wraps its entire cycle in one transaction.

### Tag Convention

Instances are discovered by Azure resource tags (mirrors AWS convention):
- `HAProxy:Service:Name` — maps to backend name
- `HAProxy:Service:Port` — maps to backend port
- `HAProxy:Instance:Port` — optional per-instance port override

### Testing

Tests use `responses` library to mock HTTP calls to the Dataplane API and `unittest.mock` for Azure SDK / reconciler internals. No live Azure or HAProxy connections needed.
