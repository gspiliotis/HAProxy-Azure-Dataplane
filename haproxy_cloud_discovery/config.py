"""Frozen dataclasses for configuration and YAML loader with env-var interpolation."""

from __future__ import annotations

import os
import re
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} placeholders with environment variable values."""

    def _replace(match: re.Match) -> str:
        env_key = match.group(1)
        env_val = os.environ.get(env_key)
        if env_val is None:
            raise ConfigError(f"Environment variable '{env_key}' is not set")
        return env_val

    return _ENV_PATTERN.sub(_replace, value)


def _walk_and_interpolate(obj: Any) -> Any:
    """Recursively interpolate env vars in strings throughout a nested structure."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_and_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_interpolate(v) for v in obj]
    return obj


@dataclass(frozen=True)
class AzureConfig:
    subscription_id: str = ""
    resource_groups: list[str] = field(default_factory=list)
    credential_type: str = "default"  # "default" uses DefaultAzureCredential


@dataclass(frozen=True)
class AWSConfig:
    region: str = ""
    account_id: str = ""  # optional; used only for logging/identification
    credential_profile: str = ""  # empty = use default boto3 credential chain


@dataclass(frozen=True)
class TagsConfig:
    service_name_tag: str = "HAProxy:Service:Name"
    service_port_tag: str = "HAProxy:Service:Port"
    instance_port_tag: str = "HAProxy:Instance:Port"
    allowlist: dict[str, str] = field(default_factory=dict)
    denylist: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendConfig:
    name_prefix: str = "azure"
    name_separator: str = "-"
    balance: str = "roundrobin"
    mode: str = "http"


@dataclass(frozen=True)
class ServerSlotsConfig:
    base: int = 10
    growth_factor: float = 1.5
    growth_type: str = "linear"  # "linear" or "exponential"


@dataclass(frozen=True)
class HAProxyConfig:
    base_url: str = "http://localhost:5555"
    api_version: str = "v2"
    username: str = "admin"
    password: str = ""
    timeout: int = 10
    verify_ssl: bool = True
    backend: BackendConfig = field(default_factory=BackendConfig)
    server_slots: ServerSlotsConfig = field(default_factory=ServerSlotsConfig)
    availability_zone: str | None = None  # "1"/"2"/"3" for Azure, "us-east-1a" etc. for AWS
    az_weight_tag: str = "HAProxy:Instance:AZperc"
    backend_options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: int = 30
    jitter_seconds: int = 5
    max_backoff_seconds: int = 300
    backoff_base_seconds: int = 5


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"  # "json" or "text"


@dataclass(frozen=True)
class AppConfig:
    azure: AzureConfig | None = None
    aws: AWSConfig | None = None
    tags: TagsConfig = field(default_factory=TagsConfig)
    haproxy: HAProxyConfig = field(default_factory=HAProxyConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _get_dataclass_type(ft: Any) -> type | None:
    """Return the underlying dataclass type from a type annotation (handles Optional/X|None)."""
    if isinstance(ft, type) and hasattr(ft, "__dataclass_fields__"):
        return ft
    # Handle X | None (Python 3.10+ types.UnionType — no __origin__, has __args__)
    if isinstance(ft, types.UnionType):
        args = [a for a in ft.__args__ if a is not type(None)]
        if len(args) == 1 and isinstance(args[0], type) and hasattr(args[0], "__dataclass_fields__"):
            return args[0]
    # Handle typing.Optional[X] → Union[X, None]
    origin = getattr(ft, "__origin__", None)
    if origin is typing.Union:
        args = [a for a in ft.__args__ if a is not type(None)]
        if len(args) == 1 and isinstance(args[0], type) and hasattr(args[0], "__dataclass_fields__"):
            return args[0]
    return None


def _build_nested(cls: type, data: dict[str, Any]) -> Any:
    """Construct a frozen dataclass, recursively building nested dataclass fields."""
    if not isinstance(data, dict):
        return data
    field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in field_types:
            continue
        ft = field_types[key]
        # Resolve string annotations to actual types in the module scope
        if isinstance(ft, str):
            ft = eval(ft, globals(), {cls.__name__: cls})  # noqa: S307
        dc_type = _get_dataclass_type(ft)
        if dc_type is not None and isinstance(value, dict):
            kwargs[key] = _build_nested(dc_type, value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path) -> AppConfig:
    """Load and validate configuration from a YAML file."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Configuration file must be a YAML mapping")

    raw = _walk_and_interpolate(raw)
    config = _build_nested(AppConfig, raw)
    _validate(config)
    return config


def _validate(config: AppConfig) -> None:
    """Validate configuration values."""
    # Exactly one cloud provider must be configured
    has_azure = config.azure is not None and bool(config.azure.subscription_id)
    has_aws = config.aws is not None and bool(config.aws.region)

    if has_azure and has_aws:
        raise ConfigError(
            "Both 'azure' and 'aws' sections are configured — only one cloud provider may be active at a time"
        )
    if not has_azure and not has_aws:
        raise ConfigError(
            "No cloud provider configured. Add an 'azure' section (with subscription_id) "
            "or an 'aws' section (with region) to your config file."
        )

    if isinstance(config.haproxy.availability_zone, int):
        raise ConfigError(
            "haproxy.availability_zone must be a string, not an integer "
            "(e.g., '1' for Azure zone 1, 'us-east-1a' for AWS)"
        )

    if config.haproxy.server_slots.base < 10:
        raise ConfigError("haproxy.server_slots.base must be >= 10")

    if config.haproxy.server_slots.growth_type not in ("linear", "exponential"):
        raise ConfigError("haproxy.server_slots.growth_type must be 'linear' or 'exponential'")

    if config.polling.interval_seconds < 5:
        raise ConfigError("polling.interval_seconds must be >= 5")

    if config.haproxy.backend.mode not in ("http", "tcp"):
        raise ConfigError("haproxy.backend.mode must be 'http' or 'tcp'")
