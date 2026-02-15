"""REST client for the HAProxy Dataplane API."""

from __future__ import annotations

import logging
from typing import Any

import requests

from ..config import HAProxyConfig
from ..exceptions import DataplaneAPIError, DataplaneVersionConflict

logger = logging.getLogger(__name__)


class DataplaneClient:
    """Thin wrapper around the HAProxy Dataplane API v2."""

    def __init__(self, config: HAProxyConfig):
        self._base = f"{config.base_url}/{config.api_version}"
        self._session = requests.Session()
        self._session.auth = (config.username, config.password)
        self._session.headers["Content-Type"] = "application/json"
        self._session.verify = config.verify_ssl
        self._timeout = config.timeout

    # ── Configuration version ───────────────────────────────────────

    def get_configuration_version(self) -> int:
        """Return the current HAProxy configuration version."""
        resp = self._get("/services/haproxy/configuration/version")
        return int(resp.text)

    # ── Transactions ────────────────────────────────────────────────

    def create_transaction(self, version: int) -> str:
        """Start a new transaction and return its ID."""
        resp = self._post("/services/haproxy/transactions", params={"version": version})
        return resp.json()["id"]

    def commit_transaction(self, transaction_id: str) -> None:
        """Commit a transaction. Raises DataplaneVersionConflict on 409."""
        self._put(f"/services/haproxy/transactions/{transaction_id}")

    def delete_transaction(self, transaction_id: str) -> None:
        """Delete (abort) a transaction."""
        self._delete(f"/services/haproxy/transactions/{transaction_id}")

    # ── Backends ────────────────────────────────────────────────────

    def list_backends(self, transaction_id: str | None = None) -> list[dict[str, Any]]:
        params = self._txn_params(transaction_id)
        resp = self._get("/services/haproxy/configuration/backends", params=params)
        return resp.json().get("data", [])

    def get_backend(self, name: str, transaction_id: str | None = None) -> dict[str, Any] | None:
        params = self._txn_params(transaction_id)
        try:
            resp = self._get(f"/services/haproxy/configuration/backends/{name}", params=params)
            return resp.json().get("data", resp.json())
        except DataplaneAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def create_backend(self, data: dict[str, Any], transaction_id: str) -> dict[str, Any]:
        params = self._txn_params(transaction_id)
        resp = self._post("/services/haproxy/configuration/backends", json=data, params=params)
        return resp.json()

    def delete_backend(self, name: str, transaction_id: str) -> None:
        params = self._txn_params(transaction_id)
        self._delete(f"/services/haproxy/configuration/backends/{name}", params=params)

    # ── Servers ─────────────────────────────────────────────────────

    def list_servers(self, backend: str, transaction_id: str | None = None) -> list[dict[str, Any]]:
        params = self._txn_params(transaction_id)
        resp = self._get(f"/services/haproxy/configuration/servers", params={**params, "backend": backend})
        return resp.json().get("data", [])

    def create_server(self, backend: str, data: dict[str, Any], transaction_id: str) -> dict[str, Any]:
        params = {**self._txn_params(transaction_id), "backend": backend}
        resp = self._post("/services/haproxy/configuration/servers", json=data, params=params)
        return resp.json()

    def replace_server(self, name: str, backend: str, data: dict[str, Any], transaction_id: str) -> dict[str, Any]:
        params = {**self._txn_params(transaction_id), "backend": backend}
        resp = self._put(f"/services/haproxy/configuration/servers/{name}", json=data, params=params)
        return resp.json()

    def delete_server(self, name: str, backend: str, transaction_id: str) -> None:
        params = {**self._txn_params(transaction_id), "backend": backend}
        self._delete(f"/services/haproxy/configuration/servers/{name}", params=params)

    # ── Internal HTTP helpers ───────────────────────────────────────

    def _txn_params(self, transaction_id: str | None) -> dict[str, str]:
        if transaction_id:
            return {"transaction_id": transaction_id}
        return {}

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: Any = None, params: dict | None = None) -> requests.Response:
        return self._request("POST", path, json=json, params=params)

    def _put(self, path: str, json: Any = None, params: dict | None = None) -> requests.Response:
        return self._request("PUT", path, json=json, params=params)

    def _delete(self, path: str, params: dict | None = None) -> requests.Response:
        return self._request("DELETE", path, params=params)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self._base}{path}"
        kwargs.setdefault("timeout", self._timeout)
        logger.debug("%s %s params=%s", method, path, kwargs.get("params"))

        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise DataplaneAPIError(f"Request failed: {exc}") from exc

        if resp.status_code == 409:
            raise DataplaneVersionConflict(response_body=resp.text)

        if resp.status_code >= 400:
            raise DataplaneAPIError(
                f"HTTP {resp.status_code} on {method} {path}: {resp.text}",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        return resp
