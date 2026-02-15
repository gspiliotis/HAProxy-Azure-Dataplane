"""State diff engine — detects changes between discovery cycles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .models import AzureService

logger = logging.getLogger(__name__)


@dataclass
class ServiceState:
    """Snapshot of a service's instances at a point in time."""

    instance_ids: frozenset[str] = field(default_factory=frozenset)
    count: int = 0
    timestamps: frozenset[datetime | None] = field(default_factory=frozenset)


class ChangeDetector:
    """Tracks per-service state and detects changes between polling cycles."""

    def __init__(self) -> None:
        self._previous: dict[tuple[str, int, str], ServiceState] = {}

    def reset(self) -> None:
        """Clear all stored state (e.g. on SIGHUP)."""
        logger.info("Change detector state reset — next cycle will reconcile everything")
        self._previous.clear()

    def detect(
        self,
        current_services: dict[tuple[str, int, str], AzureService],
    ) -> tuple[list[AzureService], list[tuple[str, int, str]]]:
        """Compare current services against the previous state.

        Returns:
            (changed_services, removed_keys) where:
            - changed_services: services that are new or have any changes
            - removed_keys: service keys that were in the previous state but not now
        """
        changed: list[AzureService] = []
        removed: list[tuple[str, int, str]] = []

        current_keys = set(current_services.keys())
        previous_keys = set(self._previous.keys())

        # Removed services
        for key in previous_keys - current_keys:
            logger.info(
                "Service removed: %s:%d@%s", key[0], key[1], key[2],
            )
            removed.append(key)

        # New or changed services
        for key, service in current_services.items():
            current_state = self._snapshot(service)

            if key not in self._previous:
                logger.info(
                    "New service discovered: %s:%d@%s with %d instances",
                    key[0], key[1], key[2], current_state.count,
                )
                changed.append(service)
                continue

            prev = self._previous[key]
            if self._has_changed(prev, current_state, key):
                changed.append(service)

        # Update stored state
        self._previous = {
            key: self._snapshot(svc) for key, svc in current_services.items()
        }

        logger.info(
            "Change detection: %d changed, %d removed, %d unchanged",
            len(changed), len(removed),
            len(current_keys) - len(changed) - len(current_keys - previous_keys),
        )
        return changed, removed

    def _has_changed(self, prev: ServiceState, curr: ServiceState, key: tuple) -> bool:
        if prev.count != curr.count:
            logger.info("Service %s:%d@%s count changed: %d -> %d", *key, prev.count, curr.count)
            return True
        if prev.instance_ids != curr.instance_ids:
            added = curr.instance_ids - prev.instance_ids
            removed = prev.instance_ids - curr.instance_ids
            logger.info(
                "Service %s:%d@%s instances changed: +%d -%d",
                *key, len(added), len(removed),
            )
            return True
        if prev.timestamps != curr.timestamps:
            logger.info("Service %s:%d@%s timestamps changed", *key)
            return True
        return False

    @staticmethod
    def _snapshot(service: AzureService) -> ServiceState:
        return ServiceState(
            instance_ids=frozenset(inst.instance_id for inst in service.instances),
            count=service.active_count,
            timestamps=frozenset(inst.created_at for inst in service.instances),
        )
