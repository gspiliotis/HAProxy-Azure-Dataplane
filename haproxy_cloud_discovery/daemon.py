"""Main polling loop with signal handling and exponential backoff."""

from __future__ import annotations

import logging
import random
import signal
import time
from types import FrameType

from .config import AppConfig
from .discovery import CloudDiscoveryClient
from .discovery.azure_client import AzureClient
from .discovery.change_detector import ChangeDetector
from .discovery.models import group_instances
from .discovery.tag_filter import TagFilter
from .haproxy.reconciler import Reconciler

logger = logging.getLogger(__name__)


class Daemon:
    """Polling daemon: discover -> filter -> group -> detect changes -> reconcile -> sleep."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._client: CloudDiscoveryClient = self._build_client(config)
        self._tag_filter = TagFilter(config.tags)
        self._change_detector = ChangeDetector()
        self._reconciler = Reconciler(config.haproxy)
        self._shutdown = False
        self._consecutive_failures = 0

    @staticmethod
    def _build_client(config: AppConfig) -> CloudDiscoveryClient:
        """Instantiate the appropriate cloud discovery client based on config."""
        if config.azure is not None and config.azure.subscription_id:
            return AzureClient(config.azure, config.tags)
        if config.aws is not None and config.aws.region:
            from .discovery.aws_client import AWSClient  # lazy import keeps azure SDK optional
            return AWSClient(config.aws, config.tags)
        # Should not reach here — _validate() enforces one-provider-only
        raise RuntimeError("No cloud provider configured")

    def run_once(self) -> None:
        """Execute a single discovery + reconciliation cycle."""
        self._cycle()

    def run(self) -> None:
        """Run the polling loop until shutdown signal."""
        self._install_signal_handlers()
        logger.info("Daemon started, polling every %ds", self._config.polling.interval_seconds)

        while not self._shutdown:
            cycle_start = time.monotonic()

            try:
                self._cycle()
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                logger.exception(
                    "Cycle failed (consecutive failures: %d)",
                    self._consecutive_failures,
                )

            elapsed = time.monotonic() - cycle_start
            sleep_time = self._calculate_sleep(elapsed)
            logger.debug("Sleeping %.1fs before next cycle", sleep_time)
            self._interruptible_sleep(sleep_time)

        logger.info("Daemon stopped")

    def _cycle(self) -> None:
        """One full discovery-to-reconciliation cycle."""
        start = time.monotonic()

        # Discover
        instances = self._client.discover_all()

        # Filter
        instances = self._tag_filter.apply(instances)

        # Group by service
        services = group_instances(instances)

        # Detect changes
        changed, removed = self._change_detector.detect(services)

        # Reconcile
        if changed or removed:
            self._reconciler.reconcile(changed, removed)

        elapsed = time.monotonic() - start
        logger.info(
            "Cycle complete",
            extra={"elapsed_seconds": round(elapsed, 2)},
        )

    def _calculate_sleep(self, elapsed: float) -> float:
        """Determine how long to sleep, applying backoff and jitter."""
        base = self._config.polling.interval_seconds

        if self._consecutive_failures > 0:
            backoff = min(
                self._config.polling.backoff_base_seconds * (2 ** (self._consecutive_failures - 1)),
                self._config.polling.max_backoff_seconds,
            )
            base = backoff

        # Jitter
        jitter = random.uniform(0, self._config.polling.jitter_seconds)

        # Subtract elapsed time from interval
        sleep = max(0.0, base - elapsed + jitter)
        return sleep

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in short increments so we can respond to shutdown signals."""
        end = time.monotonic() + seconds
        while not self._shutdown and time.monotonic() < end:
            remaining = end - time.monotonic()
            time.sleep(min(remaining, 1.0))

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGHUP, self._handle_reload)

    def _handle_shutdown(self, signum: int, frame: FrameType | None) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down", sig_name)
        self._shutdown = True

    def _handle_reload(self, signum: int, frame: FrameType | None) -> None:
        logger.info("Received SIGHUP, resetting change detector state")
        self._change_detector.reset()
