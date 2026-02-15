"""Backend/server reconciliation against the HAProxy Dataplane API."""

from __future__ import annotations

import logging
from typing import Any

from ..config import BackendConfig, HAProxyConfig
from ..discovery.models import AzureService
from ..exceptions import DataplaneVersionConflict
from .dataplane_client import DataplaneClient
from .slot_allocator import SlotAllocator
from .transaction import Transaction

logger = logging.getLogger(__name__)

MAX_VERSION_RETRIES = 3


class Reconciler:
    """Reconciles discovered Azure services with HAProxy backends/servers."""

    def __init__(self, config: HAProxyConfig):
        self._client = DataplaneClient(config)
        self._backend_cfg = config.backend
        self._slot_allocator = SlotAllocator(config.server_slots)

    def reconcile(
        self,
        changed_services: list[AzureService],
        removed_keys: list[tuple[str, int, str]],
    ) -> None:
        """Reconcile all changes in a single atomic transaction.

        Retries up to MAX_VERSION_RETRIES on version conflicts.
        """
        if not changed_services and not removed_keys:
            logger.debug("Nothing to reconcile")
            return

        for attempt in range(1, MAX_VERSION_RETRIES + 1):
            try:
                self._do_reconcile(changed_services, removed_keys)
                return
            except DataplaneVersionConflict:
                if attempt < MAX_VERSION_RETRIES:
                    logger.warning(
                        "Version conflict on attempt %d/%d, retrying",
                        attempt, MAX_VERSION_RETRIES,
                    )
                else:
                    logger.error("Version conflict persisted after %d attempts", MAX_VERSION_RETRIES)
                    raise

    def _do_reconcile(
        self,
        changed_services: list[AzureService],
        removed_keys: list[tuple[str, int, str]],
    ) -> None:
        with Transaction(self._client) as txn:
            for service in changed_services:
                self._reconcile_service(txn, service)
                txn.mark_changed()

            for key in removed_keys:
                backend_name = self._backend_name_from_key(key)
                self._disable_all_servers(txn, backend_name)
                txn.mark_changed()

    # ── Changed service reconciliation ──────────────────────────────

    def _reconcile_service(self, txn: Transaction, service: AzureService) -> None:
        backend_name = service.backend_name(self._backend_cfg.name_prefix, self._backend_cfg.name_separator)
        logger.info(
            "Reconciling service %s (%d instances) -> backend %s",
            service.service_name, service.active_count, backend_name,
        )

        # Ensure the backend exists
        self._ensure_backend(txn, backend_name)

        # Calculate slots
        total_slots = self._slot_allocator.calculate_slots(service.active_count)
        slot_names = SlotAllocator.generate_server_names(total_slots)

        # Get existing servers
        existing_servers = {
            s["name"]: s for s in self._client.list_servers(backend_name, txn.id)
        }

        # Assign active instances to slots
        active_instances = sorted(service.instances, key=lambda i: i.instance_id)

        for i, slot_name in enumerate(slot_names):
            if i < len(active_instances):
                inst = active_instances[i]
                server_data = self._active_server_data(slot_name, inst.private_ip, inst.effective_port)
            else:
                server_data = self._maintenance_server_data(slot_name)

            if slot_name in existing_servers:
                self._client.replace_server(slot_name, backend_name, server_data, txn.id)
            else:
                self._client.create_server(backend_name, server_data, txn.id)

        # Remove extra servers beyond our slot count
        for name in existing_servers:
            if name not in set(slot_names):
                logger.debug("Removing extra server %s from backend %s", name, backend_name)
                self._client.delete_server(name, backend_name, txn.id)

    # ── Removed service handling ────────────────────────────────────

    def _disable_all_servers(self, txn: Transaction, backend_name: str) -> None:
        """Set all servers in the backend to maintenance mode (never auto-delete backends)."""
        backend = self._client.get_backend(backend_name, txn.id)
        if backend is None:
            logger.debug("Backend %s not found, nothing to disable", backend_name)
            return

        servers = self._client.list_servers(backend_name, txn.id)
        if not servers:
            logger.debug("No servers in backend %s", backend_name)
            return

        logger.info("Disabling %d servers in removed backend %s", len(servers), backend_name)
        for server in servers:
            server_data = self._maintenance_server_data(server["name"])
            self._client.replace_server(server["name"], backend_name, server_data, txn.id)

    # ── Backend helpers ─────────────────────────────────────────────

    def _ensure_backend(self, txn: Transaction, name: str) -> None:
        """Create the backend if it does not already exist."""
        existing = self._client.get_backend(name, txn.id)
        if existing is not None:
            return

        logger.info("Creating backend %s", name)
        self._client.create_backend(
            {
                "name": name,
                "mode": self._backend_cfg.mode,
                "balance": {"algorithm": self._backend_cfg.balance},
            },
            txn.id,
        )

    # ── Server data builders ────────────────────────────────────────

    @staticmethod
    def _active_server_data(name: str, address: str, port: int) -> dict[str, Any]:
        return {
            "name": name,
            "address": address,
            "port": port,
            "maintenance": "disabled",
            "check": "enabled",
        }

    @staticmethod
    def _maintenance_server_data(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "address": "127.0.0.1",
            "port": 80,
            "maintenance": "enabled",
            "check": "disabled",
        }

    def _backend_name_from_key(self, key: tuple[str, int, str]) -> str:
        sep = self._backend_cfg.name_separator
        prefix = self._backend_cfg.name_prefix
        return f"{prefix}{sep}{key[0]}{sep}{key[1]}{sep}{key[2]}"
