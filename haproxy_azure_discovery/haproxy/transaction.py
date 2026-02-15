"""Transaction context manager for atomic HAProxy configuration changes."""

from __future__ import annotations

import logging

from ..exceptions import DataplaneAPIError
from .dataplane_client import DataplaneClient

logger = logging.getLogger(__name__)


class Transaction:
    """Context manager that wraps a Dataplane API transaction.

    Usage:
        with Transaction(client) as txn:
            txn.client.create_backend({...}, txn.id)
            txn.mark_changed()
        # Commits if mark_changed() was called, otherwise deletes the empty transaction.
    """

    def __init__(self, client: DataplaneClient):
        self.client = client
        self.id: str = ""
        self._changed = False

    def mark_changed(self) -> None:
        """Signal that this transaction has modifications and should be committed."""
        self._changed = True

    def __enter__(self) -> Transaction:
        version = self.client.get_configuration_version()
        self.id = self.client.create_transaction(version)
        logger.debug("Transaction started: %s (version %d)", self.id, version)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            # Exception occurred — abort the transaction
            logger.warning("Transaction %s aborted due to exception: %s", self.id, exc_val)
            self._safe_delete()
            return False  # Re-raise the exception

        if self._changed:
            logger.info("Committing transaction %s", self.id)
            self.client.commit_transaction(self.id)
        else:
            logger.debug("No changes in transaction %s, deleting", self.id)
            self._safe_delete()

        return False

    def _safe_delete(self) -> None:
        """Best-effort deletion of the transaction."""
        try:
            self.client.delete_transaction(self.id)
        except DataplaneAPIError:
            logger.debug("Could not delete transaction %s (may already be gone)", self.id, exc_info=True)
