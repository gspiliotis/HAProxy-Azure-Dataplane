"""Tests for the reconciler."""

from unittest.mock import MagicMock, patch, call

import pytest

from haproxy_azure_discovery.config import BackendConfig, HAProxyConfig, ServerSlotsConfig
from haproxy_azure_discovery.discovery.models import AzureService, DiscoveredInstance
from haproxy_azure_discovery.exceptions import DataplaneVersionConflict
from haproxy_azure_discovery.haproxy.reconciler import Reconciler


def _inst(instance_id="id1", ip="10.0.0.1", port=8080, instance_port=None, availability_zone=None, tags=None):
    return DiscoveredInstance(
        instance_id=instance_id,
        name=f"vm-{instance_id}",
        private_ip=ip,
        service_name="app",
        service_port=port,
        instance_port=instance_port,
        region="eastus",
        resource_group="rg1",
        source="vm",
        tags=tags or {},
        availability_zone=availability_zone,
    )


def _svc(instances):
    svc = AzureService(service_name="app", service_port=8080, region="eastus")
    svc.instances = list(instances)
    return svc


@pytest.fixture
def config():
    return HAProxyConfig(
        base_url="http://localhost:5555",
        username="admin",
        password="pwd",
        backend=BackendConfig(name_prefix="azure", name_separator="-"),
        server_slots=ServerSlotsConfig(base=10),
    )


class TestReconciler:
    @patch("haproxy_azure_discovery.haproxy.reconciler.DataplaneClient")
    @patch("haproxy_azure_discovery.haproxy.reconciler.Transaction")
    def test_creates_backend_and_servers(self, MockTxn, MockClient, config):
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.get_backend.return_value = None
        mock_client.list_servers.return_value = []

        txn_instance = MagicMock()
        txn_instance.id = "txn-1"
        txn_instance.client = mock_client
        MockTxn.return_value.__enter__ = MagicMock(return_value=txn_instance)
        MockTxn.return_value.__exit__ = MagicMock(return_value=False)

        reconciler = Reconciler(config)
        service = _svc([_inst("a", "10.0.0.1"), _inst("b", "10.0.0.2")])
        reconciler.reconcile([service], [])

        mock_client.create_backend.assert_called_once()
        # 10 slots: 2 active + 8 maintenance
        assert mock_client.create_server.call_count == 10
        txn_instance.mark_changed.assert_called()

    @patch("haproxy_azure_discovery.haproxy.reconciler.DataplaneClient")
    @patch("haproxy_azure_discovery.haproxy.reconciler.Transaction")
    def test_disables_removed_service(self, MockTxn, MockClient, config):
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.get_backend.return_value = {"name": "azure-app-8080-eastus"}
        mock_client.list_servers.return_value = [
            {"name": "srv1"}, {"name": "srv2"},
        ]

        txn_instance = MagicMock()
        txn_instance.id = "txn-1"
        txn_instance.client = mock_client
        MockTxn.return_value.__enter__ = MagicMock(return_value=txn_instance)
        MockTxn.return_value.__exit__ = MagicMock(return_value=False)

        reconciler = Reconciler(config)
        reconciler.reconcile([], [("app", 8080, "eastus")])

        # Both servers should be set to maintenance
        assert mock_client.replace_server.call_count == 2
        for c in mock_client.replace_server.call_args_list:
            data = c[0][2]  # Third positional arg is the server data
            assert data["maintenance"] == "enabled"
            assert data["address"] == "127.0.0.1"

    @patch("haproxy_azure_discovery.haproxy.reconciler.DataplaneClient")
    @patch("haproxy_azure_discovery.haproxy.reconciler.Transaction")
    def test_noop_when_nothing_to_reconcile(self, MockTxn, MockClient, config):
        reconciler = Reconciler(config)
        reconciler.reconcile([], [])
        MockTxn.assert_not_called()


class TestAZWeighting:
    """Tests for AZ-aware server weighting, backup, and cookie logic."""

    def _make_reconciler(self, availability_zone=None, az_weight_tag="HAProxy:Instance:AZperc", backend_options=None):
        cfg = HAProxyConfig(
            base_url="http://localhost:5555",
            username="admin",
            password="pwd",
            backend=BackendConfig(name_prefix="azure", name_separator="-"),
            server_slots=ServerSlotsConfig(base=10),
            availability_zone=availability_zone,
            az_weight_tag=az_weight_tag,
            backend_options=backend_options or {},
        )
        return Reconciler(cfg)

    def test_active_server_has_cookie(self):
        """All active servers should have cookie = server name."""
        r = self._make_reconciler()
        data = r._active_server_data("srv1", "10.0.0.1", 8080, _inst())
        assert data["cookie"] == "srv1"

    def test_no_haproxy_az_configured(self):
        """No AZ in config -> no weight/backup, just cookie."""
        r = self._make_reconciler(availability_zone=None)
        inst = _inst(availability_zone=2, tags={"HAProxy:Instance:AZperc": "10"})
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["cookie"] == "srv1"
        assert "weight" not in data
        assert "backup" not in data

    def test_same_az_no_tag_no_extra_options(self):
        """Same AZ, no AZperc tag -> just cookie, no weight/backup."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=1)
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["cookie"] == "srv1"
        assert "weight" not in data
        assert "backup" not in data

    def test_diff_az_no_tag_backup(self):
        """Different AZ, no AZperc tag -> backup: enabled."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=2)
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["backup"] == "enabled"
        assert "weight" not in data

    def test_same_az_with_azperc_tag(self):
        """Same AZ, AZperc=10 -> weight = 90."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=1, tags={"HAProxy:Instance:AZperc": "10"})
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["weight"] == 90
        assert "backup" not in data

    def test_diff_az_with_azperc_tag(self):
        """Different AZ, AZperc=10 -> weight = 10."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=2, tags={"HAProxy:Instance:AZperc": "10"})
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["weight"] == 10
        assert "backup" not in data

    def test_no_az_on_instance_treated_as_same_az(self):
        """Instance with no zone -> no penalty (treated as same AZ)."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=None)
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert "weight" not in data
        assert "backup" not in data
        assert data["cookie"] == "srv1"

    def test_no_az_on_instance_with_azperc_treated_as_same_az(self):
        """Instance with no zone but AZperc tag -> weight as same-AZ."""
        r = self._make_reconciler(availability_zone=1)
        inst = _inst(availability_zone=None, tags={"HAProxy:Instance:AZperc": "25"})
        data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
        assert data["weight"] == 75
        assert "backup" not in data

    def test_maintenance_server_no_cookie(self):
        """Maintenance slots don't get cookie/weight/backup."""
        r = self._make_reconciler(availability_zone=1)
        data = r._maintenance_server_data("srv1")
        assert "cookie" not in data
        assert "weight" not in data
        assert "backup" not in data
        assert data["maintenance"] == "enabled"

    @patch("haproxy_azure_discovery.haproxy.reconciler.DataplaneClient")
    @patch("haproxy_azure_discovery.haproxy.reconciler.Transaction")
    def test_backend_options_merged(self, MockTxn, MockClient):
        """Extra options from config appear in create_backend call."""
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.get_backend.return_value = None
        mock_client.list_servers.return_value = []

        txn_instance = MagicMock()
        txn_instance.id = "txn-1"
        MockTxn.return_value.__enter__ = MagicMock(return_value=txn_instance)
        MockTxn.return_value.__exit__ = MagicMock(return_value=False)

        r = self._make_reconciler(backend_options={
            "app": {"cookie": {"name": "STICK", "type": "insert"}},
        })
        service = _svc([_inst("a", "10.0.0.1")])
        r.reconcile([service], [])

        backend_data = mock_client.create_backend.call_args[0][0]
        assert backend_data["cookie"] == {"name": "STICK", "type": "insert"}
        assert backend_data["name"] == "azure-app-8080-eastus"

    def test_invalid_azperc_values(self):
        """AZperc values outside 1-99 or non-numeric are ignored."""
        r = self._make_reconciler(availability_zone=1)

        for bad_val in ["0", "100", "-5", "abc", ""]:
            inst = _inst(availability_zone=2, tags={"HAProxy:Instance:AZperc": bad_val})
            data = r._active_server_data("srv1", "10.0.0.1", 8080, inst)
            assert "weight" not in data
            assert data["backup"] == "enabled"
