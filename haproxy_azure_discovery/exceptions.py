"""Custom exception hierarchy for the Azure discovery daemon."""


class AzureDiscoveryError(Exception):
    """Base exception for all daemon errors."""


class ConfigError(AzureDiscoveryError):
    """Invalid or missing configuration."""


class DataplaneAPIError(AzureDiscoveryError):
    """Error communicating with the HAProxy Dataplane API."""

    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class DataplaneVersionConflict(DataplaneAPIError):
    """HTTP 409 — the configuration version changed between read and commit."""

    def __init__(self, message: str = "Configuration version conflict", response_body: str | None = None):
        super().__init__(message, status_code=409, response_body=response_body)
