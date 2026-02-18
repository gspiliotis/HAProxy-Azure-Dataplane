"""Tests for the Dataplane API client."""

import pytest
import responses

from haproxy_azure_discovery.config import HAProxyConfig
from haproxy_azure_discovery.exceptions import DataplaneAPIError, DataplaneVersionConflict
from haproxy_azure_discovery.haproxy.dataplane_client import DataplaneClient

BASE_V2 = "http://localhost:5555/v2"
BASE_V3 = "http://localhost:5555/v3"


@pytest.fixture
def client():
    return DataplaneClient(HAProxyConfig(base_url="http://localhost:5555", username="admin", password="pwd"))


@pytest.fixture
def client_v3():
    return DataplaneClient(HAProxyConfig(
        base_url="http://localhost:5555", api_version="v3", username="admin", password="pwd",
    ))


class TestGetConfigurationVersion:
    @responses.activate
    def test_returns_version(self, client):
        responses.add(responses.GET, f"{BASE_V2}/services/haproxy/configuration/version", body="42")
        assert client.get_configuration_version() == 42


class TestTransactions:
    @responses.activate
    def test_create_transaction(self, client):
        responses.add(
            responses.POST, f"{BASE_V2}/services/haproxy/transactions",
            json={"id": "txn-1", "status": "in_progress"}, status=200,
        )
        assert client.create_transaction(42) == "txn-1"

    @responses.activate
    def test_commit_transaction(self, client):
        responses.add(responses.PUT, f"{BASE_V2}/services/haproxy/transactions/txn-1", status=200)
        client.commit_transaction("txn-1")  # Should not raise

    @responses.activate
    def test_commit_version_conflict(self, client):
        responses.add(
            responses.PUT, f"{BASE_V2}/services/haproxy/transactions/txn-1",
            body="conflict", status=409,
        )
        with pytest.raises(DataplaneVersionConflict):
            client.commit_transaction("txn-1")

    @responses.activate
    def test_delete_transaction(self, client):
        responses.add(responses.DELETE, f"{BASE_V2}/services/haproxy/transactions/txn-1", status=200)
        client.delete_transaction("txn-1")


class TestBackends:
    @responses.activate
    def test_list_backends(self, client):
        responses.add(
            responses.GET, f"{BASE_V2}/services/haproxy/configuration/backends",
            json={"data": [{"name": "b1"}]},
        )
        result = client.list_backends()
        assert len(result) == 1
        assert result[0]["name"] == "b1"

    @responses.activate
    def test_get_backend_found(self, client):
        responses.add(
            responses.GET, f"{BASE_V2}/services/haproxy/configuration/backends/b1",
            json={"data": {"name": "b1", "mode": "http"}},
        )
        result = client.get_backend("b1")
        assert result["name"] == "b1"

    @responses.activate
    def test_get_backend_not_found(self, client):
        responses.add(
            responses.GET, f"{BASE_V2}/services/haproxy/configuration/backends/missing",
            json={"message": "not found"}, status=404,
        )
        assert client.get_backend("missing") is None

    @responses.activate
    def test_create_backend(self, client):
        responses.add(
            responses.POST, f"{BASE_V2}/services/haproxy/configuration/backends",
            json={"name": "b1"}, status=201,
        )
        result = client.create_backend({"name": "b1"}, "txn-1")
        assert result["name"] == "b1"


class TestServers:
    """v2 server tests — servers use flat /configuration/servers?backend=… paths."""

    @responses.activate
    def test_list_servers(self, client):
        responses.add(
            responses.GET, f"{BASE_V2}/services/haproxy/configuration/servers",
            json={"data": [{"name": "srv1"}]},
        )
        result = client.list_servers("b1")
        assert len(result) == 1

    @responses.activate
    def test_create_server(self, client):
        responses.add(
            responses.POST, f"{BASE_V2}/services/haproxy/configuration/servers",
            json={"name": "srv1"}, status=201,
        )
        result = client.create_server("b1", {"name": "srv1"}, "txn-1")
        assert result["name"] == "srv1"

    @responses.activate
    def test_replace_server(self, client):
        responses.add(
            responses.PUT, f"{BASE_V2}/services/haproxy/configuration/servers/srv1",
            json={"name": "srv1"}, status=200,
        )
        result = client.replace_server("srv1", "b1", {"name": "srv1"}, "txn-1")
        assert result["name"] == "srv1"


class TestServersV3:
    """v3 server tests — servers are nested under /configuration/backends/{backend}/servers."""

    @responses.activate
    def test_list_servers(self, client_v3):
        responses.add(
            responses.GET, f"{BASE_V3}/services/haproxy/configuration/backends/b1/servers",
            json=[{"name": "srv1"}],
        )
        result = client_v3.list_servers("b1")
        assert len(result) == 1
        assert result[0]["name"] == "srv1"

    @responses.activate
    def test_create_server(self, client_v3):
        responses.add(
            responses.POST, f"{BASE_V3}/services/haproxy/configuration/backends/b1/servers",
            json={"name": "srv1"}, status=201,
        )
        result = client_v3.create_server("b1", {"name": "srv1"}, "txn-1")
        assert result["name"] == "srv1"

    @responses.activate
    def test_replace_server(self, client_v3):
        responses.add(
            responses.PUT, f"{BASE_V3}/services/haproxy/configuration/backends/b1/servers/srv1",
            json={"name": "srv1"}, status=200,
        )
        result = client_v3.replace_server("srv1", "b1", {"name": "srv1"}, "txn-1")
        assert result["name"] == "srv1"

    @responses.activate
    def test_delete_server(self, client_v3):
        responses.add(
            responses.DELETE, f"{BASE_V3}/services/haproxy/configuration/backends/b1/servers/srv1",
            status=204,
        )
        client_v3.delete_server("srv1", "b1", "txn-1")


class TestErrorHandling:
    @responses.activate
    def test_generic_error(self, client):
        responses.add(
            responses.GET, f"{BASE_V2}/services/haproxy/configuration/version",
            body="internal error", status=500,
        )
        with pytest.raises(DataplaneAPIError) as exc_info:
            client.get_configuration_version()
        assert exc_info.value.status_code == 500
