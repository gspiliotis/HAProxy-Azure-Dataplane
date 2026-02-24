"""Tests for discovery models."""

from datetime import datetime, timezone

from haproxy_cloud_discovery.discovery.models import (
    DiscoveredService,
    DiscoveredInstance,
    group_instances,
)

# Backward-compat alias still works
AzureService = DiscoveredService


def _make_instance(
    instance_id="id1",
    name="vm1",
    private_ip="10.0.0.1",
    service_name="myapp",
    service_port=8080,
    region="eastus",
    instance_port=None,
    **kwargs,
):
    return DiscoveredInstance(
        instance_id=instance_id,
        name=name,
        private_ip=private_ip,
        service_name=service_name,
        service_port=service_port,
        region=region,
        namespace="rg1",
        source="vm",
        instance_port=instance_port,
        **kwargs,
    )


class TestDiscoveredInstance:
    def test_effective_port_uses_service_port_by_default(self):
        inst = _make_instance(service_port=8080)
        assert inst.effective_port == 8080

    def test_effective_port_uses_instance_port_when_set(self):
        inst = _make_instance(service_port=8080, instance_port=9090)
        assert inst.effective_port == 9090

    def test_backend_key(self):
        inst = _make_instance(service_name="api", service_port=443, region="westus")
        assert inst.backend_key == ("api", 443, "westus")

    def test_frozen(self):
        inst = _make_instance()
        try:
            inst.name = "other"  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestDiscoveredService:
    def test_backend_name(self):
        svc = DiscoveredService(service_name="myapp", service_port=8080, region="eastus")
        assert svc.backend_name("azure", "-") == "azure-myapp-8080-eastus"

    def test_backend_name_aws(self):
        svc = DiscoveredService(service_name="myapp", service_port=80, region="us-east-2")
        assert svc.backend_name("aws", "-") == "aws-myapp-80-us-east-2"

    def test_active_count(self):
        svc = DiscoveredService(service_name="x", service_port=80, region="y")
        svc.instances.append(_make_instance())
        svc.instances.append(_make_instance(instance_id="id2"))
        assert svc.active_count == 2


class TestGroupInstances:
    def test_groups_by_key(self):
        instances = [
            _make_instance(instance_id="1", service_name="a", service_port=80, region="east"),
            _make_instance(instance_id="2", service_name="a", service_port=80, region="east"),
            _make_instance(instance_id="3", service_name="b", service_port=443, region="west"),
        ]
        groups = group_instances(instances)
        assert len(groups) == 2
        assert groups[("a", 80, "east")].active_count == 2
        assert groups[("b", 443, "west")].active_count == 1
        # Returns DiscoveredService instances
        assert isinstance(groups[("a", 80, "east")], DiscoveredService)

    def test_empty_list(self):
        assert group_instances([]) == {}
