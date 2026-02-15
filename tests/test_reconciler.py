"""Tests for the reconciler."""

from unittest.mock import MagicMock, patch, call

import pytest

from haproxy_azure_discovery.config import BackendConfig, HAProxyConfig, ServerSlotsConfig
from haproxy_azure_discovery.discovery.models import AzureService, DiscoveredInstance
from haproxy_azure_discovery.exceptions import DataplaneVersionConflict
from haproxy_azure_discovery.haproxy.reconciler import Reconciler


def _inst(instance_id="id1", ip="10.0.0.1", port=8080, instance_port=None):
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
