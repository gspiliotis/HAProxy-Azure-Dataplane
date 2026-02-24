"""Backend/server reconciliation against the HAProxy Dataplane API."""

from __future__ import annotations

import logging
from typing import Any

from ..config import HAProxyConfig
from ..discovery.models import DiscoveredInstance, DiscoveredService
from ..exceptions import DataplaneVersionConflict
from .dataplane_client import DataplaneClient
from .slot_allocator import SlotAllocator
from .transaction import Transaction

logger = logging.getLogger(__name__)

MAX_VERSION_RETRIES = 3


class Reconciler:
    """Reconciles discovered cloud services with HAProxy backends/servers."""

    def __init__(self, config: HAProxyConfig):
        self._client = DataplaneClient(config)
        self._backend_cfg = config.backend
        self._slot_allocator = SlotAllocator(config.server_slots)
        self._haproxy_az = config.availability_zone
        self._az_weight_tag = config.az_weight_tag
        self._backend_options = config.backend_options

    def reconcile(
        self,
        changed_services: list[DiscoveredService],
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
        changed_services: list[DiscoveredService],
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

    def _reconcile_service(self, txn: Transaction, service: DiscoveredService) -> None:
        backend_name = service.backend_name(self._backend_cfg.name_prefix, self._backend_cfg.name_separator)
        logger.info(
            "Reconciling service %s (%d instances) -> backend %s",
            service.service_name, service.active_count, backend_name,
        )

        # Ensure the backend exists
        self._ensure_backend(txn, backend_name, service.service_name)

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
                server_data = self._active_server_data(slot_name, inst.private_ip, inst.effective_port, inst)
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

    def _ensure_backend(self, txn: Transaction, name: str, service_name: str = "") -> None:
        """Create the backend if it does not already exist."""
        existing = self._client.get_backend(name, txn.id)
        if existing is not None:
            return

        logger.info("Creating backend %s", name)
        backend_data: dict[str, Any] = {
            "name": name,
            "mode": self._backend_cfg.mode,
            "balance": {"algorithm": self._backend_cfg.balance},
        }
        extra = self._backend_options.get(service_name, {})
        if extra:
            backend_data.update(extra)
        self._client.create_backend(backend_data, txn.id)

    # ── Server data builders ────────────────────────────────────────

    def _active_server_data(self, name: str, address: str, port: int, instance: DiscoveredInstance | None = None) -> dict[str, Any]:
        server_data: dict[str, Any] = {
            "name": name,
            "address": address,
            "port": port,
            "maintenance": "disabled",
            "check": "enabled",
            "cookie": name,
        }

        if self._haproxy_az is not None and instance is not None:
            # Parse AZ weight percentage tag (1-99)
            az_perc = self._parse_az_perc(instance.tags.get(self._az_weight_tag))
            same_az = instance.availability_zone is None or instance.availability_zone == self._haproxy_az

            if az_perc is not None:
                server_data["weight"] = (100 - az_perc) if same_az else az_perc
            elif not same_az:
                server_data["backup"] = "enabled"

        return server_data

    @staticmethod
    def _parse_az_perc(raw: str | None) -> int | None:
        """Parse the AZ weight percentage tag value. Returns int in 1-99 range or None."""
        if raw is None:
            return None
        try:
            val = int(raw)
        except (ValueError, TypeError):
            return None
        return val if 1 <= val <= 99 else None

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
