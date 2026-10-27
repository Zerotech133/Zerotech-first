"""Security tests for browser-to-collector DTO mapping."""
from __future__ import annotations

from typing import Any

import pytest

from collectors.browser_mapper import BrowserCollectorMapper
from collectors.ports import CollectionAccessError, MediaAvailability
from knowledge.browser_fetcher import BrowserFetchResult, BrowserSessionHealth, BrowserSessionStatus


SOURCE = "https://example.invalid/channels/video/1"


class FakeFetcher:
    def __init__(self, status: BrowserSessionStatus = BrowserSessionStatus.AUTHENTICATED) -> None:
        self.status = status
        self.calls = 0

    def session_health(self) -> BrowserSessionHealth:
        return BrowserSessionHealth(self.status)

    def fetch(self, target: dict[str, Any], cursor: str | None = None, run: dict[str, Any] | None = None) -> BrowserFetchResult:
        self.calls += 1
        return BrowserFetchResult(
            source_link=target["source_link"],
            platform_content_id="v1",
            published_at="2025-01-01T00:00:00Z",
            duration_sec=3.5,
            media_status="accepted",
            title="Visible title",
            description="Visible description",
            creator={"creator_id": "c1", "nickname": "Alice", "token": "secret"},
            text_channels=({"text": "spoken", "channel": "asr", "Authorization": "secret"},),
            temporary_media_path="C:/secret/private.mp4",
        )


def test_mapper_projects_browser_result_to_collector_dto() -> None:
    fetcher = FakeFetcher()
    page = BrowserCollectorMapper(fetcher).fetch_video(SOURCE)
    assert page.metadata.source_link == SOURCE
    assert page.metadata.content_id == "v1"
    assert page.metadata.title == "Visible title"
    assert page.metadata.description == "Visible description"
    assert page.metadata.media_status is MediaAvailability.UNAVAILABLE
    assert page.metadata.creator is not None
    assert page.metadata.creator.creator_id == "c1"
    assert page.text_segments[0].text == "spoken"
    assert fetcher.calls == 1


def test_mapper_rejects_every_non_authenticated_state_before_fetch() -> None:
    for status, expected in {
        BrowserSessionStatus.LOGIN_REQUIRED: "AUTHORIZATION_REQUIRED",
        BrowserSessionStatus.EXPIRED: "AUTHORIZATION_REQUIRED",
        BrowserSessionStatus.BLOCKED: "SESSION_BLOCKED",
        BrowserSessionStatus.UNKNOWN: "SESSION_UNKNOWN",
    }.items():
        fetcher = FakeFetcher(status)
        with pytest.raises(CollectionAccessError) as error:
            BrowserCollectorMapper(fetcher).fetch_video(SOURCE)
        assert error.value.code == expected
        assert fetcher.calls == 0


def test_mapper_never_returns_browser_or_credential_fields() -> None:
    page = BrowserCollectorMapper(FakeFetcher()).fetch_video(SOURCE)
    serialized = page.as_dict()
    rendered = repr(serialized)
    for forbidden in ("temporary_media_path", "token", "Authorization", "Cookie", "headers", "storage_state"):
        assert forbidden not in rendered


def test_res_downloader_accepted_is_not_downloaded() -> None:
    page = BrowserCollectorMapper(FakeFetcher()).fetch_video(SOURCE)
    assert page.metadata.media_status is MediaAvailability.UNAVAILABLE


def test_mapper_accepts_page_shape_and_preserves_safe_pagination() -> None:
    class Paged(FakeFetcher):
        def fetch(self, target: dict[str, Any], cursor: str | None = None, run: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "items": [super().fetch(target, cursor, run)],
                "next_cursor": "next-safe",
                "done": False,
                "Authorization": "secret",
            }

    page = BrowserCollectorMapper(Paged()).fetch_video(SOURCE)
    assert page.next_cursor == "next-safe"
    assert page.done is False
