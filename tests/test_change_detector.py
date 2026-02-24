"""Tests for the change detector."""

from datetime import datetime, timezone

from haproxy_cloud_discovery.discovery.change_detector import ChangeDetector
from haproxy_cloud_discovery.discovery.models import DiscoveredService, DiscoveredInstance


def _inst(instance_id="id1", created_at=None):
    return DiscoveredInstance(
        instance_id=instance_id,
        name=f"vm-{instance_id}",
        private_ip="10.0.0.1",
        service_name="app",
        service_port=80,
        region="eastus",
        namespace="rg1",
        source="vm",
        created_at=created_at,
    )


def _svc(instances):
    svc = DiscoveredService(service_name="app", service_port=80, region="eastus")
    svc.instances = list(instances)
    return svc


KEY = ("app", 80, "eastus")


class TestChangeDetector:
    def test_first_cycle_everything_is_new(self):
        det = ChangeDetector()
        services = {KEY: _svc([_inst()])}
        changed, removed = det.detect(services)
        assert len(changed) == 1
        assert len(removed) == 0

    def test_no_change_on_second_identical_cycle(self):
        det = ChangeDetector()
        services = {KEY: _svc([_inst()])}
        det.detect(services)
        changed, removed = det.detect(services)
        assert len(changed) == 0
        assert len(removed) == 0

    def test_detects_removed_service(self):
        det = ChangeDetector()
        det.detect({KEY: _svc([_inst()])})
        changed, removed = det.detect({})
        assert len(changed) == 0
        assert removed == [KEY]

    def test_detects_count_change(self):
        det = ChangeDetector()
        det.detect({KEY: _svc([_inst("a")])})
        changed, removed = det.detect({KEY: _svc([_inst("a"), _inst("b")])})
        assert len(changed) == 1
        assert len(removed) == 0

    def test_detects_instance_id_change(self):
        det = ChangeDetector()
        det.detect({KEY: _svc([_inst("a")])})
        changed, removed = det.detect({KEY: _svc([_inst("b")])})
        assert len(changed) == 1

    def test_detects_timestamp_change(self):
        det = ChangeDetector()
        t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
        det.detect({KEY: _svc([_inst("a", created_at=t1)])})
        changed, _ = det.detect({KEY: _svc([_inst("a", created_at=t2)])})
        assert len(changed) == 1

    def test_reset_makes_next_cycle_detect_all(self):
        det = ChangeDetector()
        services = {KEY: _svc([_inst()])}
        det.detect(services)
        det.reset()
        changed, _ = det.detect(services)
        assert len(changed) == 1  # Everything looks new after reset
