"""Server slot calculation for HAProxy backends."""

from __future__ import annotations

import math

from ..config import ServerSlotsConfig


class SlotAllocator:
    """Calculates how many server slots a backend should have and generates names."""

    def __init__(self, config: ServerSlotsConfig):
        self._base = config.base
        self._growth_factor = config.growth_factor
        self._growth_type = config.growth_type

    def calculate_slots(self, active_count: int) -> int:
        """Return the number of server slots needed for the given active count.

        If active_count <= base, returns base.
        Otherwise, grows linearly or exponentially above the base.
        """
        if active_count <= self._base:
            return self._base

        if self._growth_type == "exponential":
            # Find smallest base * factor^n >= active_count
            n = math.ceil(math.log(active_count / self._base) / math.log(self._growth_factor))
            return max(int(math.ceil(self._base * (self._growth_factor ** n))), active_count)

        # Linear: base + growth_factor * (count - base), rounded up to nearest int
        extra = math.ceil((active_count - self._base) * self._growth_factor)
        return self._base + extra

    @staticmethod
    def generate_server_names(count: int) -> list[str]:
        """Generate server slot names: ['srv1', 'srv2', ..., 'srvN']."""
        return [f"srv{i}" for i in range(1, count + 1)]
