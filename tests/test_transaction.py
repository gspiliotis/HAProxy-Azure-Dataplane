"""Tests for the transaction context manager."""

from unittest.mock import MagicMock, call, patch

import pytest

from haproxy_azure_discovery.exceptions import DataplaneAPIError, DataplaneVersionConflict
from haproxy_azure_discovery.haproxy.transaction import Transaction


def _mock_client(version=1, txn_id="txn-abc"):
    client = MagicMock()
    client.get_configuration_version.return_value = version
    client.create_transaction.return_value = txn_id
    return client


class TestTransaction:
    def test_commits_when_changed(self):
        client = _mock_client()
        with Transaction(client) as txn:
            txn.mark_changed()
        client.commit_transaction.assert_called_once_with("txn-abc")
        client.delete_transaction.assert_not_called()

    def test_deletes_when_no_changes(self):
        client = _mock_client()
        with Transaction(client) as txn:
            pass  # no mark_changed()
        client.delete_transaction.assert_called_once_with("txn-abc")
        client.commit_transaction.assert_not_called()

    def test_aborts_on_exception(self):
        client = _mock_client()
        with pytest.raises(ValueError, match="boom"):
            with Transaction(client) as txn:
                txn.mark_changed()
                raise ValueError("boom")
        client.delete_transaction.assert_called_once_with("txn-abc")
        client.commit_transaction.assert_not_called()

    def test_safe_delete_swallows_errors(self):
        client = _mock_client()
        client.delete_transaction.side_effect = DataplaneAPIError("gone")
        # Should not raise
        with pytest.raises(RuntimeError):
            with Transaction(client) as txn:
                raise RuntimeError("fail")

    def test_transaction_id_exposed(self):
        client = _mock_client(txn_id="my-txn-id")
        with Transaction(client) as txn:
            assert txn.id == "my-txn-id"
