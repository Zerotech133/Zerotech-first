"""Read-only Hermes connector for the Watch Tower snapshot endpoint.

The Watch Tower service is an upstream fact source.  This module intentionally
contains only a GET client and schema validation; it has no write-side methods
or trading-domain dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

WATCH_TOWER_SCHEMA: str = "watch_tower.snapshot.v1"
DEFAULT_WATCH_TOWER_URL: str = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS: float = 5.0


class WatchTowerConnectorError(RuntimeError):
    """Stable, fail-visible error raised for unavailable or invalid snapshots."""

    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        self.code: str = code
        self.status_code: int = status_code
        super().__init__(message)

    def as_error_object(self) -> dict[str, Any]:
        """Return a safe JSON-compatible error object without upstream payloads."""
        return {"error": self.code, "message": str(self), "status_code": self.status_code}


@dataclass(frozen=True)
class WatchTowerSnapshot:
    """Validated immutable snapshot envelope from Watch Tower."""

    schema: str
    version: str | int
    data: Mapping[str, Any]
    raw: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the original validated snapshot object."""
        return dict(self.raw)


def _validate_snapshot(payload: Any) -> WatchTowerSnapshot:
    """Validate the minimum v1 envelope while preserving opaque data fields.

    The connector requires an object, the exact schema identifier, a non-empty
    version, and a mapping under ``data``.  This avoids silently accepting an
    HTML/error body or a future incompatible schema while allowing Watch Tower
    to evolve fields inside ``data``.
    """
    if not isinstance(payload, dict):
        raise WatchTowerConnectorError(
            "watch_tower.invalid_json", "Watch Tower snapshot must be a JSON object"
        )
    if payload.get("schema") != WATCH_TOWER_SCHEMA:
        raise WatchTowerConnectorError(
            "watch_tower.schema_mismatch",
            f"Unsupported Watch Tower schema: {payload.get('schema')!r}",
        )
    version: Any = payload.get("version")
    if not isinstance(version, (str, int)) or isinstance(version, bool) or not str(version):
        raise WatchTowerConnectorError(
            "watch_tower.invalid_structure", "Watch Tower snapshot requires a non-empty version"
        )
    data: Any = payload.get("data")
    if not isinstance(data, dict):
        raise WatchTowerConnectorError(
            "watch_tower.invalid_structure", "Watch Tower snapshot requires an object data field"
        )
    return WatchTowerSnapshot(
        schema=WATCH_TOWER_SCHEMA,
        version=version,
        data=data,
        raw=payload,
    )


class HermesWatchTowerConnector:
    """HTTP GET client for ``/api/watch-tower/snapshot``.

    ``caller`` is metadata for an optional supplied audit callback.  It is only
    reported when the caller actually records an event; this client does not
    claim attribution merely by constructing or using the connector.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_WATCH_TOWER_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Any | None = None,
        audit_recorder: Any | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url: str = base_url.rstrip("/")
        self._timeout_seconds: float = float(timeout_seconds)
        self._client: Any = client if client is not None else httpx.Client()
        self._audit_recorder: Any | None = audit_recorder

    def get_snapshot(self) -> WatchTowerSnapshot:
        """Fetch and validate one Watch Tower snapshot via GET only."""
        url: str = f"{self._base_url}/api/watch-tower/snapshot"
        try:
            response: Any = self._client.get(url, timeout=self._timeout_seconds)
        except httpx.TimeoutException as exc:
            raise WatchTowerConnectorError(
                "watch_tower.timeout", "Watch Tower snapshot request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise WatchTowerConnectorError(
                "watch_tower.unavailable", "Watch Tower snapshot request failed"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise WatchTowerConnectorError(
                "watch_tower.unavailable", "Watch Tower snapshot request failed"
            ) from exc

        status_code: int = int(getattr(response, "status_code", 0))
        if status_code < 200 or status_code >= 300:
            raise WatchTowerConnectorError(
                "watch_tower.upstream_http_error",
                f"Watch Tower returned HTTP {status_code}",
            )
        try:
            payload: Any = response.json()
        except (ValueError, TypeError) as exc:
            raise WatchTowerConnectorError(
                "watch_tower.invalid_json", "Watch Tower returned invalid JSON"
            ) from exc
        snapshot: WatchTowerSnapshot = _validate_snapshot(payload)
        if self._audit_recorder is not None:
            self._audit_recorder(
                {
                    "caller": "hermes-watch-tower",
                    "operation": "snapshot_read",
                    "schema": snapshot.schema,
                }
            )
        return snapshot

    def close(self) -> None:
        """Close the underlying HTTP client when it owns a closeable client."""
        close: Any = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "HermesWatchTowerConnector":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
