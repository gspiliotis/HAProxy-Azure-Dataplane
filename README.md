# Cloud Service Discovery Daemon for HAProxy

A Python daemon that automatically discovers cloud instances (Azure VMs/VMSS or AWS EC2/ASG) and registers them as HAProxy backends via the [Dataplane API](https://www.haproxy.com/documentation/dataplaneapi/latest/).

Exactly one cloud provider is active per deployment. The daemon polls the configured provider, detects changes between cycles, and reconciles HAProxy configuration through atomic transactions — without ever reloading HAProxy.

## How It Works

```
┌───────────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  Cloud Instances  │     │  Tag Filter  │     │    Change     │     │   HAProxy    │
│  Azure VMs/VMSS   │────>│  (allow /    │────>│   Detector    │────>│  Dataplane   │
│  AWS EC2/ASG      │     │   deny)      │     │  (state diff) │     │  API (txn)   │
└───────────────────┘     └──────────────┘     └───────────────┘     └──────────────┘
          ^                                                                  │
          │                        poll loop                                 │
          └──────────────────────────────────────────────────────────────────┘
```

Each polling cycle:

1. **Discover** — Query Azure or AWS for all instances with the required tags
2. **Filter** — Apply tag-based allowlist/denylist rules
3. **Group** — Organize instances into services by `(name, port, region)`
4. **Detect changes** — Compare against the previous cycle's state
5. **Reconcile** — Update HAProxy backends and servers in a single atomic transaction

## Resource Tags

Tag your instances to make them discoverable. The same tag names are used for both Azure and AWS:

| Tag | Required | Description |
|-----|----------|-------------|
| `HAProxy:Service:Name` | Yes | Service name — becomes part of the backend name |
| `HAProxy:Service:Port` | Yes | Port the service listens on |
| `HAProxy:Instance:Port` | No | Per-instance port override (defaults to service port) |
| `HAProxy:Instance:AZperc` | No | AZ weight percentage (1–99) for AZ-aware routing (see below) |

**Backend naming:** `{prefix}-{name}-{port}-{region}`

Examples:
- Azure VM tagged `HAProxy:Service:Name=myapp`, `HAProxy:Service:Port=8080` in `eastus` → backend `azure-myapp-8080-eastus`
- AWS EC2 instance with the same tags in `us-east-2` → backend `aws-myapp-8080-us-east-2`

The prefix is set via `haproxy.backend.name_prefix` in `config.yaml`.

## Installation

Requires Python 3.11+.

Install with only the cloud provider you need:

```bash
# Azure only
pip install ".[azure]"

# AWS only
pip install ".[aws]"

# Both providers
pip install ".[all]"
```

For development (includes both providers + test dependencies):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

## Configuration

Copy the example and edit:

```bash
cp config.example.yaml config.yaml
```

Secrets use `${ENV_VAR}` interpolation — set environment variables instead of putting credentials in the file.

**Provider selection**: uncomment exactly one of the `azure:` or `aws:` sections.

### Azure configuration

```bash
export AZURE_SUBSCRIPTION_ID="your-subscription-id"
export HAPROXY_DATAPLANE_PASSWORD="your-password"
```

```yaml
azure:
  subscription_id: "${AZURE_SUBSCRIPTION_ID}"
  resource_groups: []        # Empty = scan all resource groups
  credential_type: "default" # Uses DefaultAzureCredential
```

### AWS configuration

```bash
export AWS_REGION="us-east-2"
export HAPROXY_DATAPLANE_PASSWORD="your-password"
```

```yaml
aws:
  region: "${AWS_REGION}"       # Required
  account_id: ""                # Optional, used in logs only
  credential_profile: ""        # Optional named profile; empty = default credential chain
```

### Full configuration reference

```yaml
# Uncomment exactly ONE provider block:

# azure:
#   subscription_id: "${AZURE_SUBSCRIPTION_ID}"
#   resource_groups: []
#   credential_type: "default"

# aws:
#   region: "${AWS_REGION}"
#   account_id: ""
#   credential_profile: ""

tags:
  service_name_tag: "HAProxy:Service:Name"
  service_port_tag: "HAProxy:Service:Port"
  instance_port_tag: "HAProxy:Instance:Port"
  allowlist: {}        # Instance must match ALL (AND logic)
    # environment: "production"
  denylist: {}         # Instance excluded if ANY matches (OR logic)
    # HAProxy:Exclude: "true"

haproxy:
  base_url: "http://localhost:5555"
  api_version: "v2"
  username: "admin"
  password: "${HAPROXY_DATAPLANE_PASSWORD}"
  timeout: 10
  verify_ssl: true
  backend:
    name_prefix: "azure"       # "azure" or "aws" — prefix in backend names
    name_separator: "-"
    balance: "roundrobin"      # Any HAProxy balance algorithm
    mode: "http"               # "http" or "tcp"
  server_slots:
    base: 10                   # Minimum server slots per backend
    growth_factor: 1.5
    growth_type: "linear"      # "linear" or "exponential"
  # availability_zone: "1"          # Azure: "1", "2", "3"
  # availability_zone: "us-east-1a" # AWS: full AZ name
  # az_weight_tag: "HAProxy:Instance:AZperc"
  # backend_options:
  #   MyApp:
  #     cookie: { name: "SRVID", type: "insert" }

polling:
  interval_seconds: 30
  jitter_seconds: 5            # Random jitter to prevent thundering herd
  max_backoff_seconds: 300     # Cap for exponential backoff on failures
  backoff_base_seconds: 5

logging:
  level: "INFO"
  format: "json"               # "json" (production) or "text" (development)
```

### Authentication

**Azure** uses [`DefaultAzureCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential), which tries these methods in order:

1. Environment variables (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`)
2. Managed Identity (when running on Azure VMs)
3. Azure CLI (`az login`)

The identity needs **Reader** access to the subscription (or specific resource groups) to list VMs, VMSS, NICs, and public IPs.

**AWS** uses the standard [boto3 credential chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html):

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS credentials file (`~/.aws/credentials`)
3. IAM Instance Profile / ECS task role (when running on AWS compute)
4. Named profile via `credential_profile` in config

The IAM identity needs `ec2:DescribeInstances`, `ec2:DescribeTags`, and `autoscaling:DescribeAutoScalingGroups` on the target resources.

## Usage

### Validate configuration

```bash
haproxy-cloud-discovery --validate -c config.yaml
```

### Run a single discovery cycle

```bash
haproxy-cloud-discovery --once -c config.yaml
```

### Run as a daemon

```bash
haproxy-cloud-discovery -c config.yaml
```

### Run as a Python module

```bash
python -m haproxy_cloud_discovery -c config.yaml
```

### Signal handling

| Signal | Behavior |
|--------|----------|
| `SIGTERM` / `SIGINT` | Graceful shutdown after current cycle completes |
| `SIGHUP` | Reset internal state — next cycle does a full reconciliation |

## systemd Deployment

A hardened systemd unit file is provided:

```bash
# Install the service
sudo cp systemd/haproxy-cloud-discovery.service /etc/systemd/system/
sudo mkdir -p /etc/haproxy-cloud-discovery
sudo cp config.yaml /etc/haproxy-cloud-discovery/

# Put secrets in the environment file
sudo tee /etc/haproxy-cloud-discovery/env <<EOF
# Azure:
AZURE_SUBSCRIPTION_ID=your-subscription-id
# AWS:
# AWS_REGION=us-east-2
HAPROXY_DATAPLANE_PASSWORD=your-password
EOF
sudo chmod 600 /etc/haproxy-cloud-discovery/env

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-cloud-discovery
```

## Server Slot Allocation

Backends always maintain at least `base` server slots (default: 10). This allows the infrastructure to scale without requiring HAProxy config changes for every new instance.

- Slots beyond the active instance count sit in **maintenance mode** (address `127.0.0.1:80`, checks disabled)
- When active instances exceed the base, additional slots are allocated using the configured growth strategy:
  - **Linear**: `base + ceil((count - base) * growth_factor)`
  - **Exponential**: smallest `base * factor^n` that is `>= count`

## AZ-Aware Routing

When HAProxy runs in a specific Availability Zone, you can configure the daemon to prefer backends in the same AZ. Set `haproxy.availability_zone` to the AZ where your HAProxy instance runs.

The value is always a **string**:
- Azure: `"1"`, `"2"`, or `"3"`
- AWS: full AZ name such as `"us-east-1a"`, `"eu-west-1b"`

The reconciler annotates each active server based on AZ proximity:

| Instance AZ | `AZperc` tag | Server effect |
|-------------|-------------|---------------|
| Same as HAProxy (or no zone) | Not set | No extra options (full weight) |
| Different from HAProxy | Not set | `backup enabled` — only used when same-AZ servers are down |
| Same as HAProxy (or no zone) | `10` | `weight 90` (100 − AZperc) |
| Different from HAProxy | `10` | `weight 10` |

The `AZperc` tag lets you do proportional cross-AZ traffic splitting instead of strict backup-only. A value of `10` means "send 10% of traffic to the other AZ."

All active servers always get a `cookie` value equal to their server name, enabling cookie-based persistence when combined with `backend_options`.

**Example config:**

```yaml
haproxy:
  availability_zone: "us-east-1a"  # AWS
  # availability_zone: "1"         # Azure
  # az_weight_tag: "HAProxy:Instance:AZperc"  # default
```

When `availability_zone` is omitted or `null`, AZ logic is disabled entirely — all servers are treated equally.

## Per-Service Backend Options

The `haproxy.backend_options` config lets you pass extra Dataplane API properties when a backend is first created. Options are keyed by the service name (the value of the `HAProxy:Service:Name` tag).

```yaml
haproxy:
  backend_options:
    WebApp:
      cookie:
        name: "SRVID"
        type: "insert"
        indirect: true
        nocache: true
        httponly: true
    API:
      server_timeout: 60000
      connect_timeout: 5000
```

Any valid [Dataplane API backend field](https://www.haproxy.com/documentation/dataplaneapi/latest/) can be used. Options are merged into the create-backend payload and only take effect when a backend is first created (existing backends are not modified).

## Safety Guarantees

- **Backends are never auto-deleted.** When a service disappears (tags removed, all instances terminated), the daemon sets all servers in that backend to maintenance mode instead of deleting the backend. This prevents accidental deletion from transient cloud API failures or partial responses.
- **Atomic updates.** All changes within a polling cycle are applied in a single Dataplane API transaction — HAProxy sees a consistent snapshot.
- **Version conflict retry.** If another process modifies the HAProxy configuration between transaction creation and commit, the daemon retries up to 3 times with a fresh transaction.
- **Exponential backoff.** Consecutive failures (cloud API errors, Dataplane errors) trigger exponential backoff up to `max_backoff_seconds`, preventing tight failure loops.
- **No persistent state.** All state is in-memory. On restart, the first cycle discovers and reconciles everything from scratch.

## Verifying It Works

After starting the daemon, inspect HAProxy's configuration through the Dataplane API:

```bash
# List all backends
curl -u admin:password http://localhost:5555/v2/services/haproxy/configuration/backends

# List servers in a specific backend
curl -u admin:password \
  "http://localhost:5555/v2/services/haproxy/configuration/servers?backend=aws-myapp-8080-us-east-2"
```

## Project Structure

```
haproxy_cloud_discovery/
├── cli.py                    # Arg parsing, config loading, bootstrap
├── config.py                 # Frozen dataclasses, YAML loader, validation
├── daemon.py                 # Polling loop, signal handling, backoff, provider factory
├── exceptions.py             # ConfigError, DiscoveryError, DataplaneAPIError, ...
├── logging_config.py         # JSON/text structured logging
├── discovery/
│   ├── __init__.py           # CloudDiscoveryClient Protocol
│   ├── azure_client.py       # Azure SDK: VM + VMSS enumeration and IP resolution
│   ├── aws_client.py         # boto3: EC2 instance + ASG member discovery
│   ├── change_detector.py    # State diff engine between polling cycles
│   ├── models.py             # DiscoveredInstance, DiscoveredService dataclasses
│   └── tag_filter.py         # Allowlist/denylist filtering
└── haproxy/
    ├── dataplane_client.py   # REST client for the Dataplane API
    ├── reconciler.py         # Backend/server reconciliation logic
    ├── slot_allocator.py     # Server slot calculation
    └── transaction.py        # Transaction context manager (commit/abort)
```

## Running Tests

```bash
python -m pytest tests/ -v
```

Tests use the `responses` library to mock Dataplane API HTTP calls, `moto` to mock AWS API calls, and `unittest.mock` for Azure SDK and reconciler internals. No live Azure, AWS, or HAProxy connections are needed.

