"""Cloud discovery package — provider-agnostic Protocol and public exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import DiscoveredInstance


@runtime_checkable
class CloudDiscoveryClient(Protocol):
    """Protocol that every cloud discovery client must satisfy."""

    def discover_all(self) -> list[DiscoveredInstance]:
        """Return all running instances tagged for HAProxy service discovery."""
        ...
