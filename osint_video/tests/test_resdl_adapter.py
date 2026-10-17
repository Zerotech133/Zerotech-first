"""Focused security and failure tests for the res-downloader adapter."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from resdl_adapter import (
    HttpRequestError,
    MediaInfo,
    PathPolicy,
    PathPolicyError,
    ResDownloaderAdapter,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_adapter_rejects_non_local_base_url() -> None:
    with pytest.raises(ValueError, match="local HTTP"):
        ResDownloaderAdapter(base_url="https://example.test:8899")
    with pytest.raises(ValueError, match="local HTTP"):
        ResDownloaderAdapter(base_url="http://127.0.0.1:8899?token=secret")


def test_adapter_requires_boolean_acceptance_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "queued"})

    adapter = ResDownloaderAdapter(client=_client(handler), path_policy=PathPolicy((tmp_path,)))
    with pytest.raises(HttpRequestError, match="boolean acceptance"):
        adapter.download(MediaInfo("https://example.test/video"), tmp_path)


def test_health_returns_false_when_service_not_listening() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = ResDownloaderAdapter(client=_client(handler))
    assert adapter.health() is False


def test_path_policy_rejects_traversal(tmp_path: Path) -> None:
    policy = PathPolicy((tmp_path / "downloads",))
    with pytest.raises(PathPolicyError):
        policy.validate(tmp_path / "downloads" / ".." / "outside")


def test_media_info_preserves_ordinary_query_and_rejects_credentials() -> None:
    media = MediaInfo.from_mapping(
        {"url": "https://example.test/video?id=123&part=2", "title": "demo"}
    )
    assert media.safe_url == "https://example.test/video?id=123&part=2"
    assert media.to_payload()["url"].endswith("?id=123&part=2")
    with pytest.raises(ValueError, match="credential-like"):
        MediaInfo.from_mapping({"url": "https://example.test/v?token=secret"})
    with pytest.raises(ValueError, match="fragment"):
        MediaInfo.from_mapping({"url": "https://example.test/v#fragment"})
    with pytest.raises(ValueError, match="sensitive"):
        MediaInfo.from_mapping({"url": "https://example.test/v", "decode_key": "secret"})


def test_decode_does_not_log_sensitive_fields(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "downloads"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accepted": True, "task_id": "abc"})

    adapter = ResDownloaderAdapter(
        client=_client(handler), path_policy=PathPolicy((root,))
    )
    with caplog.at_level(logging.WARNING):
        result = adapter.decode(
            MediaInfo("https://example.test/video?id=hidden"),
            decode_key="decode-secret",
            output_dir=root,
        )
    assert result.accepted is True
    # accepted is submission-only; no downloaded/decrypted state is exposed.
    assert not hasattr(result, "downloaded")
    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert "decode-secret" not in rendered
    assert "id=hidden" not in rendered
    assert "OtherData" not in rendered


def test_http_errors_are_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "secret details"})

    adapter = ResDownloaderAdapter(client=_client(handler))
    with pytest.raises(HttpRequestError, match="HTTP 503"):
        adapter._post("/api/get-config", {})
    # health is intentionally a boolean probe; errors are normalized internally.
    assert adapter.health() is False
