"""Minimal, safe HTTP client for res-downloader 3.1.3 local API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from .dto import MediaInfo
from .security import PathPolicy, PathPolicyError

_LOGGER = logging.getLogger(__name__)


class ResDownloaderError(RuntimeError):
    """Base class for normalized adapter failures."""


class ServiceUnavailableError(ResDownloaderError):
    """Raised when the local service cannot be reached."""


class HttpRequestError(ResDownloaderError):
    """Raised for non-success HTTP responses or malformed service replies."""


@dataclass(frozen=True)
class DownloadResult:
    """Submission acknowledgement from the asynchronous endpoint.

    ``accepted`` means only that res-downloader accepted the request. Since the
    service exposes no task-status API, this result must not be interpreted as
    ``downloaded`` or ``decrypted``; output-file hashing/ffprobe validation is
    intentionally left to a separate post-download workflow.
    """

    accepted: bool
    task_id: str | None = None
    message: str = ""


class ResDownloaderAdapter:
    """Call res-downloader without exposing secrets or permitting path traversal."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8899",
        *,
        path_policy: PathPolicy | None = None,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed_base = urlsplit(base_url)
        try:
            hostname = parsed_base.hostname
            port = parsed_base.port
        except ValueError as exc:
            raise ValueError("base_url must contain a valid port") from exc
        if (
            parsed_base.scheme != "http"
            or hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.path not in {"", "/"}
            or parsed_base.query
            or parsed_base.fragment
            or (port is not None and port != 8899)
        ):
            raise ValueError("base_url must target the local HTTP service without credentials or URL components")
        self._base_url = base_url.rstrip("/")
        self._path_policy = path_policy
        self._timeout = timeout
        self._client = client

    def _post(self, endpoint: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Perform a JSON POST and normalize transport/protocol errors."""
        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self._base_url}{endpoint}", json=dict(payload), timeout=self._timeout
                )
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        f"{self._base_url}{endpoint}", json=dict(payload)
                    )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            _LOGGER.warning("res-downloader unavailable endpoint=%s", endpoint)
            raise ServiceUnavailableError("res-downloader service unavailable") from exc
        except httpx.HTTPError as exc:
            _LOGGER.warning("res-downloader transport error endpoint=%s", endpoint)
            raise HttpRequestError("res-downloader request failed") from exc

        if response.status_code >= 400:
            _LOGGER.warning("res-downloader HTTP error endpoint=%s status=%s", endpoint, response.status_code)
            raise HttpRequestError(f"res-downloader returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise HttpRequestError("res-downloader returned invalid JSON") from exc
        if not isinstance(body, Mapping):
            raise HttpRequestError("res-downloader returned an invalid response")
        return body

    def health(self) -> bool:
        """Probe service health through its config endpoint."""
        try:
            self._post("/api/get-config", {})
            return True
        except ResDownloaderError:
            return False

    def download(self, media: MediaInfo, output_dir: str | Path) -> DownloadResult:
        """Submit an asynchronous download request after path validation."""
        if self._path_policy is None:
            raise PathPolicyError("download path policy is required")
        target = self._path_policy.validate_directory(output_dir)
        body = self._post(
            "/api/download",
            {"media": media.to_payload(), "output_dir": str(target)},
        )
        accepted_value = body.get("accepted", body.get("success"))
        if not isinstance(accepted_value, bool):
            raise HttpRequestError("res-downloader returned no boolean acceptance status")
        accepted = accepted_value
        task_id_value = body.get("task_id", body.get("id"))
        task_id = str(task_id_value) if task_id_value is not None else None
        message = str(body.get("message", ""))
        return DownloadResult(accepted=accepted, task_id=task_id, message=message)

    def decode(self, media: MediaInfo, *, decode_key: str, output_dir: str | Path) -> DownloadResult:
        """Submit decode request while keeping decode credentials out of logs/results."""
        if not decode_key:
            raise ValueError("decode_key must not be empty")
        if self._path_policy is None:
            raise PathPolicyError("download path policy is required")
        target = self._path_policy.validate_directory(output_dir)
        body = self._post(
            "/api/wx-file-decode",
            {"media": media.to_payload(), "decode_key": decode_key, "output_dir": str(target)},
        )
        accepted_value = body.get("accepted", body.get("success"))
        if not isinstance(accepted_value, bool):
            raise HttpRequestError("res-downloader returned no boolean acceptance status")
        accepted = accepted_value
        task_id_value = body.get("task_id", body.get("id"))
        task_id = str(task_id_value) if task_id_value is not None else None
        return DownloadResult(accepted=accepted, task_id=task_id, message=str(body.get("message", "")))

