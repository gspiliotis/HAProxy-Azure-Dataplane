"""Tests for the CLI entry point."""

import yaml
import pytest

from haproxy_azure_discovery.cli import main


class TestCLI:
    def test_validate_valid_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"azure": {"subscription_id": "sub-123"}}))
        result = main(["--validate", "-c", str(config_path)])
        assert result == 0

    def test_validate_invalid_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"azure": {}}))
        result = main(["--validate", "-c", str(config_path)])
        assert result == 1

    def test_missing_config_file(self):
        result = main(["-c", "/nonexistent/config.yaml", "--validate"])
        assert result == 1
