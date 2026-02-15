"""Tag-based allowlist / denylist filtering for discovered instances."""

from __future__ import annotations

import logging

from ..config import TagsConfig
from .models import DiscoveredInstance

logger = logging.getLogger(__name__)


class TagFilter:
    """Filters instances based on tag allowlist (AND) and denylist (OR)."""

    def __init__(self, tags_config: TagsConfig):
        self._allowlist = tags_config.allowlist
        self._denylist = tags_config.denylist

    def apply(self, instances: list[DiscoveredInstance]) -> list[DiscoveredInstance]:
        before = len(instances)
        result = [inst for inst in instances if self._matches(inst)]
        filtered = before - len(result)
        if filtered:
            logger.info("Tag filter removed %d of %d instances", filtered, before)
        return result

    def _matches(self, instance: DiscoveredInstance) -> bool:
        tags = instance.tags

        # Denylist: excluded if ANY condition matches (OR)
        for key, value in self._denylist.items():
            if tags.get(key) == value:
                logger.debug("Instance %s denied by tag %s=%s", instance.name, key, value)
                return False

        # Allowlist: must match ALL conditions (AND)
        for key, value in self._allowlist.items():
            if tags.get(key) != value:
                logger.debug("Instance %s does not match allowlist tag %s=%s", instance.name, key, value)
                return False

        return True
