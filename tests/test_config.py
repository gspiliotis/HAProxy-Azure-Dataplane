"""Tests for configuration loading and validation."""

import os
import tempfile

import pytest
import yaml

from haproxy_azure_discovery.config import AppConfig, load_config
from haproxy_azure_discovery.exceptions import ConfigError


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data))
    return str(path)


class TestLoadConfig:
    def test_minimal_valid_config(self, tmp_path):
        data = {"azure": {"subscription_id": "sub-123"}}
        config = load_config(_write_config(tmp_path, data))
        assert config.azure.subscription_id == "sub-123"
        assert config.haproxy.server_slots.base == 10
        assert config.polling.interval_seconds == 30

    def test_missing_file_raises(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/file.yaml")

    def test_missing_subscription_id_raises(self, tmp_path):
        data = {"azure": {"resource_groups": ["rg1"]}}
        with pytest.raises(ConfigError, match="subscription_id"):
            load_config(_write_config(tmp_path, data))

    def test_server_slots_base_too_low(self, tmp_path):
        data = {
            "azure": {"subscription_id": "sub-123"},
            "haproxy": {"server_slots": {"base": 5}},
        }
        with pytest.raises(ConfigError, match="base must be >= 10"):
            load_config(_write_config(tmp_path, data))

    def test_invalid_growth_type(self, tmp_path):
        data = {
            "azure": {"subscription_id": "sub-123"},
            "haproxy": {"server_slots": {"growth_type": "quadratic"}},
        }
        with pytest.raises(ConfigError, match="growth_type"):
            load_config(_write_config(tmp_path, data))

    def test_polling_interval_too_low(self, tmp_path):
        data = {
            "azure": {"subscription_id": "sub-123"},
            "polling": {"interval_seconds": 2},
        }
        with pytest.raises(ConfigError, match="interval_seconds"):
            load_config(_write_config(tmp_path, data))

    def test_env_var_interpolation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_SUB_ID", "env-sub-456")
        data = {"azure": {"subscription_id": "${TEST_SUB_ID}"}}
        config = load_config(_write_config(tmp_path, data))
        assert config.azure.subscription_id == "env-sub-456"

    def test_env_var_missing_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SURELY_MISSING_VAR", raising=False)
        data = {"azure": {"subscription_id": "${SURELY_MISSING_VAR}"}}
        with pytest.raises(ConfigError, match="SURELY_MISSING_VAR"):
            load_config(_write_config(tmp_path, data))

    def test_full_config(self, tmp_path):
        data = {
            "azure": {"subscription_id": "s1", "resource_groups": ["rg1", "rg2"]},
            "tags": {
                "allowlist": {"env": "prod"},
                "denylist": {"skip": "true"},
            },
            "haproxy": {
                "base_url": "http://lb:5555",
                "username": "u",
                "password": "p",
                "backend": {"name_prefix": "az", "balance": "leastconn", "mode": "tcp"},
                "server_slots": {"base": 20, "growth_type": "exponential", "growth_factor": 2.0},
            },
            "polling": {"interval_seconds": 60, "jitter_seconds": 10},
            "logging": {"level": "DEBUG", "format": "text"},
        }
        config = load_config(_write_config(tmp_path, data))
        assert config.azure.resource_groups == ["rg1", "rg2"]
        assert config.tags.allowlist == {"env": "prod"}
        assert config.haproxy.backend.balance == "leastconn"
        assert config.haproxy.backend.mode == "tcp"
        assert config.haproxy.server_slots.growth_type == "exponential"
        assert config.logging.format == "text"

    def test_invalid_backend_mode(self, tmp_path):
        data = {
            "azure": {"subscription_id": "sub-123"},
            "haproxy": {"backend": {"mode": "udp"}},
        }
        with pytest.raises(ConfigError, match="mode"):
            load_config(_write_config(tmp_path, data))

    def test_non_mapping_file(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("just a string")
        with pytest.raises(ConfigError, match="mapping"):
            load_config(str(path))

    def test_az_fields_load(self, tmp_path):
        data = {
            "azure": {"subscription_id": "sub-123"},
            "haproxy": {
                "availability_zone": 2,
                "az_weight_tag": "Custom:AZ:Tag",
                "backend_options": {
                    "MyApp": {
                        "cookie": {"name": "STICK", "type": "insert"},
                    },
                },
            },
        }
        config = load_config(_write_config(tmp_path, data))
        assert config.haproxy.availability_zone == 2
        assert config.haproxy.az_weight_tag == "Custom:AZ:Tag"
        assert config.haproxy.backend_options == {
            "MyApp": {"cookie": {"name": "STICK", "type": "insert"}},
        }

    def test_az_fields_defaults(self, tmp_path):
        data = {"azure": {"subscription_id": "sub-123"}}
        config = load_config(_write_config(tmp_path, data))
        assert config.haproxy.availability_zone is None
        assert config.haproxy.az_weight_tag == "HAProxy:Instance:AZperc"
        assert config.haproxy.backend_options == {}
