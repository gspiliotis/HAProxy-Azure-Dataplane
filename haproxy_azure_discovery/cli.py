"""Argument parsing, configuration loading, and daemon bootstrap."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import AppConfig, load_config
from .daemon import Daemon
from .exceptions import AzureDiscoveryError, ConfigError
from .logging_config import configure_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="haproxy-azure-discovery",
        description="Azure Service Discovery Daemon for HAProxy",
    )
    parser.add_argument(
        "-c", "--config",
        required=True,
        help="Path to the YAML configuration file",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single discovery cycle and exit",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the configuration file and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load config (minimal logging until config is loaded)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    configure_logging(config.logging)

    if args.validate:
        logger.info("Configuration is valid")
        return 0

    daemon = Daemon(config)

    try:
        if args.once:
            logger.info("Running single discovery cycle (--once)")
            daemon.run_once()
        else:
            daemon.run()
    except AzureDiscoveryError as exc:
        logger.error("Fatal error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 0

    return 0
