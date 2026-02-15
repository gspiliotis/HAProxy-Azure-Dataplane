# Azure Service Discovery Daemon for HAProxy

A Python daemon that automatically discovers Azure VMs and Virtual Machine Scale Set (VMSS) instances and registers them as HAProxy backends via the [Dataplane API](https://www.haproxy.com/documentation/dataplaneapi/latest/).

This is the Azure equivalent of the AWS EC2 service discovery embedded in HAProxy's Go codebase. Rather than modifying the Go source, it runs as an external sidecar that polls Azure, detects changes, and reconciles HAProxy configuration through atomic transactions.

## How It Works

```
┌──────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  Azure VMs   │     │  Tag Filter  │     │    Change     │     │   HAProxy    │
│  & VMSS      │────>│  (allow /    │────>│   Detector    │────>│  Dataplane   │
│  (tagged)    │     │   deny)      │     │  (state diff) │     │  API (txn)   │
└──────────────┘     └──────────────┘     └───────────────┘     └──────────────┘
       ^                                                               │
       │                         poll loop                             │
       └───────────────────────────────────────────────────────────────┘
```

Each polling cycle:

1. **Discover** — Query Azure for all VMs and VMSS instances with the required tags
2. **Filter** — Apply tag-based allowlist/denylist rules
3. **Group** — Organize instances into services by `(name, port, region)`
4. **Detect changes** — Compare against the previous cycle's state
5. **Reconcile** — Update HAProxy backends and servers in a single atomic transaction

## Azure Resource Tags

Tag your VMs or VMSS resources to make them discoverable:

| Tag | Required | Description |
|-----|----------|-------------|
| `HAProxy:Service:Name` | Yes | Service name — becomes part of the backend name |
| `HAProxy:Service:Port` | Yes | Port the service listens on |
| `HAProxy:Instance:Port` | No | Per-instance port override (defaults to service port) |

These tag names follow the same convention as the built-in AWS EC2 service discovery and are configurable.

**Example:** A VM tagged with `HAProxy:Service:Name=myapp` and `HAProxy:Service:Port=8080` in the `eastus` region creates a backend named `azure-myapp-8080-eastus`.

## Installation

Requires Python 3.11+.

```bash
pip install .
```

Or for development:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Copy the example and edit:

```bash
cp config.example.yaml config.yaml
```

Secrets use `${ENV_VAR}` interpolation — set environment variables instead of putting credentials in the file:

```bash
export AZURE_SUBSCRIPTION_ID="your-subscription-id"
export HAPROXY_DATAPLANE_PASSWORD="your-password"
```

### Configuration Reference

```yaml
azure:
  subscription_id: "${AZURE_SUBSCRIPTION_ID}"   # Required
  resource_groups: []                            # Empty = scan all resource groups
  credential_type: "default"                     # Uses DefaultAzureCredential

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
    name_prefix: "azure"       # Backend naming: {prefix}-{name}-{port}-{region}
    name_separator: "-"
    balance: "roundrobin"      # Any HAProxy balance algorithm
    mode: "http"               # "http" or "tcp"
  server_slots:
    base: 10                   # Minimum server slots per backend
    growth_factor: 1.5
    growth_type: "linear"      # "linear" or "exponential"

polling:
  interval_seconds: 30
  jitter_seconds: 5            # Random jitter to prevent thundering herd
  max_backoff_seconds: 300     # Cap for exponential backoff on failures
  backoff_base_seconds: 5

logging:
  level: "INFO"
  format: "json"               # "json" (production) or "text" (development)
```

### Azure Authentication

The daemon uses [`DefaultAzureCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential), which tries these methods in order:

1. Environment variables (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`)
2. Managed Identity (when running on Azure VMs)
3. Azure CLI (`az login`)
4. Other methods (VS Code, PowerShell, etc.)

The identity needs **Reader** access to the subscription (or the specific resource groups) to list VMs, VMSS, NICs, and public IPs.

## Usage

### Validate configuration

```bash
haproxy-azure-discovery --validate -c config.yaml
```

### Run a single discovery cycle

```bash
haproxy-azure-discovery --once -c config.yaml
```

### Run as a daemon

```bash
haproxy-azure-discovery -c config.yaml
```

### Run as a Python module

```bash
python -m haproxy_azure_discovery -c config.yaml
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
sudo cp systemd/haproxy-azure-discovery.service /etc/systemd/system/
sudo mkdir -p /etc/haproxy-azure-discovery
sudo cp config.yaml /etc/haproxy-azure-discovery/

# Put secrets in the environment file
sudo tee /etc/haproxy-azure-discovery/env <<EOF
AZURE_SUBSCRIPTION_ID=your-subscription-id
HAPROXY_DATAPLANE_PASSWORD=your-password
EOF
sudo chmod 600 /etc/haproxy-azure-discovery/env

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-azure-discovery
```

## Server Slot Allocation

Backends always maintain at least `base` server slots (default: 10). This allows the infrastructure to scale without requiring HAProxy config changes for every new instance.

- Slots beyond the active instance count sit in **maintenance mode** (address `127.0.0.1:80`, checks disabled)
- When active instances exceed the base, additional slots are allocated using the configured growth strategy:
  - **Linear**: `base + ceil((count - base) * growth_factor)`
  - **Exponential**: smallest `base * factor^n` that is `>= count`

## Safety Guarantees

- **Backends are never auto-deleted.** When a service's tags are removed or all instances disappear, the daemon sets all servers in that backend to maintenance mode. This prevents accidental backend deletion from transient Azure API failures or partial responses.
- **Atomic updates.** All changes within a polling cycle are applied in a single Dataplane API transaction — HAProxy sees a consistent snapshot.
- **Version conflict retry.** If another process modifies the HAProxy configuration between the transaction's creation and commit, the daemon retries up to 3 times with a fresh transaction.
- **Exponential backoff.** Consecutive failures (Azure API errors, Dataplane errors) trigger exponential backoff up to `max_backoff_seconds`, preventing tight failure loops.
- **No persistent state.** All state is in-memory. On restart, the first cycle discovers and reconciles everything from scratch.

## Verifying It Works

After starting the daemon, inspect HAProxy's configuration through the Dataplane API:

```bash
# List all backends
curl -u admin:password http://localhost:5555/v2/services/haproxy/configuration/backends

# List servers in a specific backend
curl -u admin:password http://localhost:5555/v2/services/haproxy/configuration/servers?backend=azure-myapp-8080-eastus
```

## Project Structure

```
haproxy_azure_discovery/
├── cli.py                    # Arg parsing, config loading, bootstrap
├── config.py                 # Frozen dataclasses, YAML loader, validation
├── daemon.py                 # Polling loop, signal handling, backoff
├── exceptions.py             # ConfigError, DataplaneAPIError, DataplaneVersionConflict
├── logging_config.py         # JSON/text structured logging
├── discovery/
│   ├── azure_client.py       # Azure SDK: VM + VMSS enumeration and IP resolution
│   ├── change_detector.py    # State diff engine between polling cycles
│   ├── models.py             # DiscoveredInstance, AzureService dataclasses
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

Tests use the `responses` library to mock Dataplane API HTTP calls and `unittest.mock` for Azure SDK interactions. No live Azure or HAProxy connections are needed.
