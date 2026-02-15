"""Data models for discovered Azure instances and service groupings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DiscoveredInstance:
    """A single VM or VMSS instance discovered from Azure."""

    instance_id: str
    name: str
    private_ip: str
    service_name: str
    service_port: int
    region: str
    resource_group: str
    source: str  # "vm" or "vmss"
    tags: dict[str, str] = field(default_factory=dict)
    public_ip: str | None = None
    instance_port: int | None = None
    created_at: datetime | None = None
    power_state: str = "unknown"

    @property
    def effective_port(self) -> int:
        """The port to use for the HAProxy server entry (instance_port overrides service_port)."""
        return self.instance_port if self.instance_port is not None else self.service_port

    @property
    def backend_key(self) -> tuple[str, int, str]:
        """Grouping key: (service_name, service_port, region)."""
        return (self.service_name, self.service_port, self.region)


@dataclass
class AzureService:
    """A group of instances that form one HAProxy backend."""

    service_name: str
    service_port: int
    region: str
    instances: list[DiscoveredInstance] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.service_name, self.service_port, self.region)

    @property
    def active_count(self) -> int:
        return len(self.instances)

    def backend_name(self, prefix: str, separator: str) -> str:
        """Generate the HAProxy backend name, e.g. 'azure-myapp-8080-eastus'."""
        return f"{prefix}{separator}{self.service_name}{separator}{self.service_port}{separator}{self.region}"


def group_instances(instances: list[DiscoveredInstance]) -> dict[tuple[str, int, str], AzureService]:
    """Group discovered instances into AzureService objects by (name, port, region)."""
    services: dict[tuple[str, int, str], AzureService] = {}
    for inst in instances:
        key = inst.backend_key
        if key not in services:
            services[key] = AzureService(
                service_name=inst.service_name,
                service_port=inst.service_port,
                region=inst.region,
            )
        services[key].instances.append(inst)
    return services
