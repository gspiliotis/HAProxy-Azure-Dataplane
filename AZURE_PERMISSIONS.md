# Azure Permissions for HAProxy Azure Discovery

This document lists the minimum Azure RBAC permissions required by the daemon's service principal or managed identity.

## Recommended: Built-in Role

The **Reader** role at the subscription or resource group scope is sufficient:

```
Scope:  /subscriptions/<subscription-id>
Role:   Reader
```

If you restrict `azure.resource_groups` in the config, you can assign Reader at the resource group level instead of the subscription level.

## Minimum Custom Role

If Reader is too broad, create a custom role with only the actions the daemon uses:

```json
{
  "Name": "HAProxy Azure Discovery",
  "Description": "Read-only access to VMs, VMSS, NICs, and Public IPs for HAProxy service discovery",
  "Actions": [
    "Microsoft.Compute/virtualMachines/read",
    "Microsoft.Compute/virtualMachines/instanceView/read",
    "Microsoft.Compute/virtualMachineScaleSets/read",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/instanceView/read",
    "Microsoft.Network/networkInterfaces/read",
    "Microsoft.Network/publicIPAddresses/read"
  ],
  "NotActions": [],
  "DataActions": [],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/<subscription-id>"
  ]
}
```

### What each action does

| Action | SDK Call | Purpose |
|--------|----------|---------|
| `Microsoft.Compute/virtualMachines/read` | `virtual_machines.list()`, `list_all()` | Enumerate VMs and read their tags, location, NIC references |
| `Microsoft.Compute/virtualMachines/instanceView/read` | `virtual_machines.instance_view()` | Check VM power state (only running VMs are registered) |
| `Microsoft.Compute/virtualMachineScaleSets/read` | `virtual_machine_scale_sets.list()`, `list_all()` | Enumerate VMSS resources and read their tags |
| `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read` | `virtual_machine_scale_set_vms.list()` | List individual instances within a VMSS |
| `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/instanceView/read` | `virtual_machine_scale_set_vms.get_instance_view()` | Check VMSS instance power state |
| `Microsoft.Network/networkInterfaces/read` | `network_interfaces.get()`, `get_virtual_machine_scale_set_network_interface()`, `list_virtual_machine_scale_set_vm_network_interfaces()` | Resolve private IPs from VM and VMSS NICs |
| `Microsoft.Network/publicIPAddresses/read` | `public_ip_addresses.get()` | Resolve public IPs (optional — only used if a NIC has a public IP attached) |

## Entra ID App Registration Setup

If using a service principal instead of managed identity:

1. **Register an application** in Entra ID (Azure AD) > App registrations
2. **Create a client secret** (or use a certificate) under Certificates & secrets
3. **Assign the role** at the appropriate scope:
   ```bash
   az role assignment create \
     --assignee <app-client-id> \
     --role "Reader" \
     --scope "/subscriptions/<subscription-id>"
   ```
   Or with the custom role:
   ```bash
   az role definition create --role-definition @custom-role.json
   az role assignment create \
     --assignee <app-client-id> \
     --role "HAProxy Azure Discovery" \
     --scope "/subscriptions/<subscription-id>"
   ```
4. **Set environment variables** for the daemon:
   ```bash
   AZURE_TENANT_ID=<your-tenant-id>
   AZURE_CLIENT_ID=<your-app-client-id>
   AZURE_CLIENT_SECRET=<your-client-secret>
   ```

`DefaultAzureCredential` picks these up automatically.

## Managed Identity (Recommended for Azure-hosted deployments)

If the daemon runs on an Azure VM or in AKS, use a managed identity instead of a service principal:

1. **Enable system-assigned managed identity** on the VM or create a user-assigned managed identity
2. **Assign the role**:
   ```bash
   az role assignment create \
     --assignee <managed-identity-principal-id> \
     --role "Reader" \
     --scope "/subscriptions/<subscription-id>"
   ```
3. No environment variables needed — `DefaultAzureCredential` detects managed identity automatically

## Notes

- The daemon is **strictly read-only** against Azure. It never creates, modifies, or deletes any Azure resources.
- `Microsoft.Network/publicIPAddresses/read` can be omitted if you don't need public IP resolution (the daemon only requires private IPs to register servers in HAProxy).
- When using `resource_groups` in the config to limit scope, you can assign the role per resource group instead of at the subscription level.
