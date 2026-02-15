"""Tests for logging configuration."""

import json
import logging

from haproxy_azure_discovery.config import LoggingConfig
from haproxy_azure_discovery.logging_config import JSONFormatter, TextFormatter, configure_logging


class TestJSONFormatter:
    def test_formats_as_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello %s", args=("world",), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_includes_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.service = "myapp"  # type: ignore
        record.backend = "azure-myapp-80"  # type: ignore
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["service"] == "myapp"
        assert parsed["backend"] == "azure-myapp-80"


class TestConfigureLogging:
    def test_json_format(self):
        configure_logging(LoggingConfig(level="DEBUG", format="json"))
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert any(isinstance(h.formatter, JSONFormatter) for h in root.handlers)

    def test_text_format(self):
        configure_logging(LoggingConfig(level="WARNING", format="text"))
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert any(isinstance(h.formatter, TextFormatter) for h in root.handlers)

    def test_suppresses_noisy_loggers(self):
        configure_logging(LoggingConfig())
        assert logging.getLogger("azure").level >= logging.WARNING
        assert logging.getLogger("urllib3").level >= logging.WARNING
