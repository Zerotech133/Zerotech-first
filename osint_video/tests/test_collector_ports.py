"""Unit tests for the platform-neutral collector contract."""
from __future__ import annotations

from typing import Any

from collectors.ports import (
    CreatorResolveRequest,
    CollectionFailureError,
    MediaAvailability,
    SafeCollector,
    SessionStatus,
    TextChannel,
)


class Adapter:
    def session_health(self) -> dict[str, Any]:
        return {"status": "authenticated", "Cookie": "secret", "headers": {"Authorization": "secret"}}

    def resolve_creator(self, request: CreatorResolveRequest) -> dict[str, Any]:
        return {
            "resolved": True,
            "creator": {"platform": "demo", "creator_id": 7, "nickname": "Alice", "profile_url": "https://example.invalid/u/7", "token": "secret"},
            "secret": "must not leak",
        }

    def fetch_video(self, source_link: str, cursor: str | None = None) -> dict[str, Any]:
        return {
            "metadata": {"platform": "demo", "content_id": 9, "title": "hello", "duration_sec": "3.5", "media_status": "unavailable", "storage_path": "/secret/path"},
            "text_channels": [
                {"text": "hello world", "channel": "asr", "start_sec": 0, "end_sec": 1, "confidence": 0.8, "Cookie": "secret"},
                {"text": "标题文字", "channel": "ocr", "start_sec": 1, "end_sec": 2},
                {"text": "字幕文字", "channel": "subtitle", "start_sec": 2, "end_sec": 3},
            ],
            "next_cursor": "c2",
            "done": False,
        }


def test_safe_collector_projects_health_creator_and_video() -> None:
    collector = SafeCollector(Adapter())
    health = collector.session_health()
    assert health.status is SessionStatus.AUTHENTICATED
    assert "Cookie" not in health.as_dict()

    resolved = collector.resolve_creator(CreatorResolveRequest("https://example.invalid/post/1"))
    payload = resolved.as_dict()
    assert payload["creator"]["creator_id"] == "7"
    assert "token" not in payload["creator"]

    page = collector.fetch_video("https://example.invalid/post/1")
    assert page.metadata.media_status is MediaAvailability.UNAVAILABLE
    assert page.metadata.duration_sec == 3.5
    assert [segment.channel for segment in page.text_segments] == [TextChannel.ASR, TextChannel.OCR, TextChannel.SUBTITLE]
    assert "storage_path" not in page.metadata.as_dict()
    assert "Cookie" not in page.text_segments[0].as_dict()


def test_collector_is_compatible_with_creator_worker_page_fetcher() -> None:
    page = SafeCollector(Adapter())({"source_link": "https://example.invalid/post/1"}, None, {"Cookie": "secret"})
    assert page["done"] is False
    assert page["next_cursor"] == "c2"
    assert page["items"][0]["metadata"]["media_status"] == "unavailable"


def test_metadata_only_degrades_only_for_explicit_media_unavailable() -> None:
    class MediaUnavailable(Adapter):
        def fetch_video(self, source_link: str, cursor: str | None = None) -> dict[str, Any]:
            error = RuntimeError("media unavailable")
            error.media_unavailable = True
            raise error

    page = SafeCollector(MediaUnavailable()).fetch_video("https://example.invalid/post/2")
    assert page.metadata.source_link.endswith("/post/2")
    assert page.metadata.media_status is MediaAvailability.UNAVAILABLE
    assert page.text_segments == ()


def test_hard_adapter_failure_is_not_reported_as_metadata_success() -> None:
    class Broken(Adapter):
        def fetch_video(self, source_link: str, cursor: str | None = None) -> dict[str, Any]:
            raise RuntimeError("network failure")

    try:
        SafeCollector(Broken()).fetch_video("https://example.invalid/post/3")
        raise AssertionError("hard adapter failure should propagate safely")
    except CollectionFailureError as exc:
        assert exc.code == "COLLECTION_FAILED"


def test_credential_query_and_private_host_are_rejected() -> None:
    try:
        CreatorResolveRequest("https://example.invalid/post/1?token=secret")
        raise AssertionError("credential query should be rejected")
    except ValueError:
        pass
    try:
        SafeCollector(Adapter()).fetch_video("https://127.0.0.1/post/1")
        raise AssertionError("private host should be rejected")
    except ValueError:
        pass


def test_session_health_gate_blocks_non_authenticated_states() -> None:
    from collectors.ports import CollectionAccessError

    class GateAdapter(Adapter):
        def __init__(self, status: str) -> None:
            self.status = status

        def session_health(self) -> dict[str, str]:
            return {"status": self.status}

    for status, code in (("login_required", "AUTHORIZATION_REQUIRED"), ("expired", "AUTHORIZATION_REQUIRED"), ("blocked", "SESSION_BLOCKED"), ("unknown", "SESSION_UNKNOWN")):
        collector = SafeCollector(GateAdapter(status))
        try:
            collector({"source_link": "https://example.invalid/post/1"}, None, {})
            raise AssertionError("session gate should reject collection")
        except CollectionAccessError as exc:
            assert exc.code == code
