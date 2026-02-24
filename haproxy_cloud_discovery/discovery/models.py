"""Data models for discovered cloud instances and service groupings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DiscoveredInstance:
    """A single VM/instance discovered from a cloud provider."""

    instance_id: str
    name: str
    private_ip: str
    service_name: str
    service_port: int
    region: str
    namespace: str  # resource group (Azure) or account ID (AWS) or empty
    source: str  # "vm", "vmss", "ec2", "asg"
    tags: dict[str, str] = field(default_factory=dict)
    public_ip: str | None = None
    instance_port: int | None = None
    availability_zone: str | None = None  # "1"/"2"/"3" for Azure, "us-east-1a" etc. for AWS
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
class DiscoveredService:
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
        """Generate the HAProxy backend name, e.g. 'azure-myapp-8080-eastus' or 'aws-myapp-80-us-east-2'."""
        return f"{prefix}{separator}{self.service_name}{separator}{self.service_port}{separator}{self.region}"


# Backward-compatibility alias
AzureService = DiscoveredService


def group_instances(instances: list[DiscoveredInstance]) -> dict[tuple[str, int, str], DiscoveredService]:
    """Group discovered instances into DiscoveredService objects by (name, port, region)."""
    services: dict[tuple[str, int, str], DiscoveredService] = {}
    for inst in instances:
        key = inst.backend_key
        if key not in services:
            services[key] = DiscoveredService(
                service_name=inst.service_name,
                service_port=inst.service_port,
                region=inst.region,
            )
        services[key].instances.append(inst)
    return services
