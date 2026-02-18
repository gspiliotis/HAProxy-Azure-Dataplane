"""Azure SDK client for discovering VMs and VMSS instances."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

from ..config import AzureConfig, TagsConfig
from .models import DiscoveredInstance

logger = logging.getLogger(__name__)

TAG_SERVICE_NAME = "HAProxy:Service:Name"
TAG_SERVICE_PORT = "HAProxy:Service:Port"
TAG_INSTANCE_PORT = "HAProxy:Instance:Port"


class AzureClient:
    """Discovers VMs and VMSS instances from Azure using the management SDK."""

    def __init__(self, azure_config: AzureConfig, tags_config: TagsConfig):
        self._config = azure_config
        self._tags = tags_config
        self._credential = DefaultAzureCredential()
        self._compute = ComputeManagementClient(self._credential, azure_config.subscription_id)
        self._network = NetworkManagementClient(self._credential, azure_config.subscription_id)

    def discover_all(self) -> list[DiscoveredInstance]:
        """Run full discovery: VMs + VMSS instances. Returns only running instances with required tags."""
        instances: list[DiscoveredInstance] = []
        instances.extend(self._discover_vms())
        instances.extend(self._discover_vmss())
        logger.info("Discovery complete", extra={"total_instances": len(instances)})
        return instances

    # ── VM discovery ────────────────────────────────────────────────

    def _discover_vms(self) -> list[DiscoveredInstance]:
        """Enumerate standalone VMs across configured resource groups."""
        instances: list[DiscoveredInstance] = []
        resource_groups = self._config.resource_groups

        if resource_groups:
            vms = []
            for rg in resource_groups:
                logger.debug("Listing VMs in resource group %s", rg)
                vms.extend(self._compute.virtual_machines.list(rg))
        else:
            logger.debug("Listing VMs across all resource groups")
            vms = list(self._compute.virtual_machines.list_all())

        for vm in vms:
            tags = vm.tags or {}
            service_name = tags.get(self._tags.service_name_tag)
            service_port_str = tags.get(self._tags.service_port_tag)
            if not service_name or not service_port_str:
                continue

            try:
                service_port = int(service_port_str)
            except ValueError:
                logger.warning("VM %s has non-integer service port tag: %s", vm.name, service_port_str)
                continue

            instance_port = self._parse_instance_port(tags)

            # Extract resource group from the VM's ID
            rg = self._resource_group_from_id(vm.id)

            # Get power state via instance view
            if not self._is_running_vm(rg, vm.name):
                logger.debug("Skipping VM %s — not running", vm.name)
                continue

            private_ip, public_ip = self._resolve_vm_ips(vm)
            if not private_ip:
                logger.warning("VM %s has no private IP, skipping", vm.name)
                continue

            created_at = self._parse_timestamp(vm.time_created) if hasattr(vm, "time_created") and vm.time_created else None

            instances.append(DiscoveredInstance(
                instance_id=vm.vm_id or vm.id,
                name=vm.name,
                private_ip=private_ip,
                service_name=service_name,
                service_port=service_port,
                instance_port=instance_port,
                region=vm.location,
                resource_group=rg,
                source="vm",
                tags=tags,
                public_ip=public_ip,
                created_at=created_at,
                power_state="running",
            ))

        logger.info("VM discovery found %d instances", len(instances))
        return instances

    def _is_running_vm(self, resource_group: str, vm_name: str) -> bool:
        """Check if a VM is in the 'running' power state."""
        try:
            instance_view = self._compute.virtual_machines.instance_view(resource_group, vm_name)
            for status in (instance_view.statuses or []):
                if status.code and status.code.lower() == "powerstate/running":
                    return True
        except Exception:
            logger.debug("Could not get instance view for VM %s/%s", resource_group, vm_name, exc_info=True)
        return False

    def _resolve_vm_ips(self, vm) -> tuple[str | None, str | None]:
        """Resolve private and public IPs from a VM's network interfaces."""
        private_ip = None
        public_ip = None

        if not vm.network_profile or not vm.network_profile.network_interfaces:
            return private_ip, public_ip

        for nic_ref in vm.network_profile.network_interfaces:
            nic_rg = self._resource_group_from_id(nic_ref.id)
            nic_name = nic_ref.id.split("/")[-1]
            try:
                nic = self._network.network_interfaces.get(nic_rg, nic_name)
            except Exception:
                logger.debug("Could not fetch NIC %s", nic_ref.id, exc_info=True)
                continue

            for ip_config in (nic.ip_configurations or []):
                if ip_config.private_ip_address and not private_ip:
                    private_ip = ip_config.private_ip_address

                if ip_config.public_ip_address and ip_config.public_ip_address.id:
                    try:
                        pip_rg = self._resource_group_from_id(ip_config.public_ip_address.id)
                        pip_name = ip_config.public_ip_address.id.split("/")[-1]
                        pip = self._network.public_ip_addresses.get(pip_rg, pip_name)
                        if pip.ip_address:
                            public_ip = pip.ip_address
                    except Exception:
                        logger.debug("Could not fetch public IP %s", ip_config.public_ip_address.id, exc_info=True)

            if private_ip:
                break

        return private_ip, public_ip

    # ── VMSS discovery ──────────────────────────────────────────────

    def _discover_vmss(self) -> list[DiscoveredInstance]:
        """Enumerate VMSS instances across configured resource groups."""
        instances: list[DiscoveredInstance] = []
        resource_groups = self._config.resource_groups

        if resource_groups:
            vmss_list = []
            for rg in resource_groups:
                logger.debug("Listing VMSS in resource group %s", rg)
                vmss_list.extend(self._compute.virtual_machine_scale_sets.list(rg))
        else:
            logger.debug("Listing VMSS across all resource groups")
            vmss_list = list(self._compute.virtual_machine_scale_sets.list_all())

        for vmss in vmss_list:
            tags = vmss.tags or {}
            service_name = tags.get(self._tags.service_name_tag)
            service_port_str = tags.get(self._tags.service_port_tag)
            if not service_name or not service_port_str:
                continue

            try:
                service_port = int(service_port_str)
            except ValueError:
                logger.warning("VMSS %s has non-integer service port tag: %s", vmss.name, service_port_str)
                continue

            instance_port = self._parse_instance_port(tags)
            rg = self._resource_group_from_id(vmss.id)

            vmss_instances = list(
                self._compute.virtual_machine_scale_set_vms.list(rg, vmss.name)
            )
            logger.debug("VMSS %s has %d instances", vmss.name, len(vmss_instances))

            for vm_instance in vmss_instances:
                inst_id = vm_instance.instance_id

                # Check power state
                if not self._is_running_vmss_instance(rg, vmss.name, inst_id):
                    logger.debug("Skipping VMSS instance %s/%s — not running", vmss.name, inst_id)
                    continue

                private_ip = self._resolve_vmss_instance_ip(rg, vmss.name, inst_id, vm_instance)
                if not private_ip:
                    logger.warning("VMSS instance %s/%s has no private IP, skipping", vmss.name, inst_id)
                    continue

                # Instance-level tags can override VMSS-level tags
                inst_tags = {**tags, **(vm_instance.tags or {})}
                inst_service_name = inst_tags.get(self._tags.service_name_tag, service_name)
                inst_port_str = inst_tags.get(self._tags.service_port_tag, str(service_port))
                try:
                    inst_service_port = int(inst_port_str)
                except ValueError:
                    inst_service_port = service_port
                inst_instance_port = self._parse_instance_port(inst_tags)

                unique_id = f"{vmss.id}/virtualMachines/{inst_id}"
                vm_name = vm_instance.name or f"{vmss.name}_{inst_id}"

                instances.append(DiscoveredInstance(
                    instance_id=unique_id,
                    name=vm_name,
                    private_ip=private_ip,
                    service_name=inst_service_name,
                    service_port=inst_service_port,
                    instance_port=inst_instance_port,
                    region=vmss.location,
                    resource_group=rg,
                    source="vmss",
                    tags=inst_tags,
                    power_state="running",
                ))

        logger.info("VMSS discovery found %d instances", len(instances))
        return instances

    def _is_running_vmss_instance(self, resource_group: str, vmss_name: str, instance_id: str) -> bool:
        """Check if a VMSS instance is running."""
        try:
            instance_view = self._compute.virtual_machine_scale_set_vms.get_instance_view(
                resource_group, vmss_name, instance_id,
            )
            for status in (instance_view.statuses or []):
                if status.code and status.code.lower() == "powerstate/running":
                    return True
        except Exception:
            logger.debug(
                "Could not get instance view for VMSS %s/%s/%s",
                resource_group, vmss_name, instance_id, exc_info=True,
            )
        return False

    def _resolve_vmss_instance_ip(self, resource_group: str, vmss_name: str, instance_id: str, vm_instance=None) -> str | None:
        """Resolve the private IP of a VMSS instance via its network interfaces.

        VMSS NICs require dedicated APIs — the standard NIC get/list used for
        standalone VMs will not return results.  The targeted GET
        (get_virtual_machine_scale_set_network_interface) reliably returns full
        IP configuration, whereas the list API may omit private_ip_address.
        """
        # Primary: extract NIC names from the VM instance's network profile and
        # use the targeted GET which reliably returns IP details.
        if (
            vm_instance
            and vm_instance.network_profile
            and vm_instance.network_profile.network_interfaces
        ):
            for nic_ref in vm_instance.network_profile.network_interfaces:
                nic_name = nic_ref.id.split("/")[-1]
                try:
                    nic = self._network.network_interfaces.get_virtual_machine_scale_set_network_interface(
                        resource_group, vmss_name, instance_id, nic_name,
                    )
                    for ip_config in (nic.ip_configurations or []):
                        if ip_config.private_ip_address:
                            return ip_config.private_ip_address
                except Exception:
                    logger.debug(
                        "Could not fetch NIC %s for VMSS instance %s/%s/%s",
                        nic_name, resource_group, vmss_name, instance_id,
                        exc_info=True,
                    )

        # Fallback: list all NICs for this VMSS VM.
        try:
            nics = self._network.network_interfaces.list_virtual_machine_scale_set_vm_network_interfaces(
                resource_group, vmss_name, instance_id,
            )
            for nic in nics:
                for ip_config in (nic.ip_configurations or []):
                    if ip_config.private_ip_address:
                        return ip_config.private_ip_address
        except Exception:
            logger.debug(
                "Could not list NICs for VMSS instance %s/%s/%s",
                resource_group, vmss_name, instance_id, exc_info=True,
            )
        return None

    # ── Helpers ──────────────────────────────────────────────────────

    def _parse_instance_port(self, tags: dict[str, str]) -> int | None:
        """Parse the optional HAProxy:Instance:Port tag."""
        raw = tags.get(self._tags.instance_port_tag)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    def _resource_group_from_id(resource_id: str) -> str:
        """Extract the resource group name from an Azure resource ID."""
        parts = resource_id.split("/")
        for i, part in enumerate(parts):
            if part.lower() == "resourcegroups" and i + 1 < len(parts):
                return parts[i + 1]
        return ""

    @staticmethod
    def _parse_timestamp(ts) -> datetime | None:
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(str(ts))
        except (ValueError, TypeError):
            return None
