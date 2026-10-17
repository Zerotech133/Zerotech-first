"""Security-focused tests for the injected PlatformAdapter."""
from __future__ import annotations

from typing import Any

import pytest

from collectors.ports import CollectionAccessError, CreatorResolveRequest, SessionStatus
from collectors.platform_adapter import PlatformAdapter


SOURCE = "https://example.invalid/channels/video/1"


def _page() -> dict[str, Any]:
    return {
        "authorized": True,
        "source_link": SOURCE,
        "platform": "wx_channels",
        "content_id": "v1",
        "title": "Visible title",
        "description": "Visible description",
        "duration_sec": "3.5",
        "creator": {
            "creator_id": 42,
            "nickname": "Alice",
            "profile_url": "https://example.invalid/channels/u/42",
            "token": "must not leak",
        },
        "text_segments": [
            {"text": "spoken", "channel": "asr", "start_sec": 0, "end_sec": 1},
            {"text": "字幕", "channel": "subtitle", "start_sec": 1, "end_sec": 2},
        ],
        "Cookie": "must not be retained",
        "storage_state": {"cookies": ["secret"]},
    }


def test_platform_adapter_maps_explicit_page_data_only() -> None:
    adapter = PlatformAdapter(page_data={SOURCE: _page()}, initial_status=SessionStatus.AUTHENTICATED)
    result = adapter.fetch_video(SOURCE)
    assert result.metadata.content_id == "v1"
    assert result.metadata.duration_sec == 3.5
    assert [segment.text for segment in result.text_segments] == ["spoken", "字幕"]
    creator = adapter.resolve_creator(CreatorResolveRequest(SOURCE)).creator
    assert creator is not None
    assert creator.creator_id == "42"
    assert creator.nickname == "Alice"


def test_platform_adapter_does_not_search_for_missing_link() -> None:
    adapter = PlatformAdapter(page_data={SOURCE: _page()}, initial_status=SessionStatus.AUTHENTICATED)
    with pytest.raises(Exception) as error:
        adapter.fetch_video("https://example.invalid/channels/video/other")
    assert getattr(error.value, "code", None) == "PAGE_NOT_PROVIDED"


def test_unknown_or_unauthorized_state_is_rejected() -> None:
    unknown = PlatformAdapter(page_data={SOURCE: _page()})
    with pytest.raises(CollectionAccessError) as error:
        unknown.fetch_video(SOURCE)
    assert error.value.code == "SESSION_UNKNOWN"

    unauthorized = PlatformAdapter(
        page_data={SOURCE: {**_page(), "authorized": False}},
        initial_status=SessionStatus.AUTHENTICATED,
    )
    with pytest.raises(CollectionAccessError) as error:
        unauthorized.fetch_video(SOURCE)
    assert error.value.code == "AUTHORIZATION_REQUIRED"


def test_missing_browser_attach_returns_explicit_safe_error() -> None:
    adapter = PlatformAdapter(
        health_probe=lambda: SessionStatus.AUTHENTICATED,
        initial_status=SessionStatus.AUTHENTICATED,
    )
    with pytest.raises(CollectionAccessError) as error:
        adapter.fetch_video(SOURCE)
    assert error.value.code == "BROWSER_ATTACH_UNAVAILABLE"


def test_provider_receives_only_exact_link_and_sensitive_fields_are_removed() -> None:
    seen: list[str] = []

    def provider(source_link: str) -> dict[str, Any]:
        seen.append(source_link)
        return {**_page(), "Authorization": "secret", "headers": {"Cookie": "secret"}}

    adapter = PlatformAdapter(
        page_data_provider=provider,
        health_probe=lambda: SessionStatus.AUTHENTICATED,
    )
    adapter.fetch_video(SOURCE)
    assert seen == [SOURCE]
