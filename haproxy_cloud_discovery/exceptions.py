"""Custom exception hierarchy for the cloud discovery daemon."""


class DiscoveryError(Exception):
    """Base exception for all daemon errors."""


# Backward-compatibility alias
AzureDiscoveryError = DiscoveryError


class ConfigError(DiscoveryError):
    """Invalid or missing configuration."""


class DataplaneAPIError(DiscoveryError):
    """Error communicating with the HAProxy Dataplane API."""

    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class DataplaneVersionConflict(DataplaneAPIError):
    """HTTP 409 — the configuration version changed between read and commit."""

    def __init__(self, message: str = "Configuration version conflict", response_body: str | None = None):
        super().__init__(message, status_code=409, response_body=response_body)
