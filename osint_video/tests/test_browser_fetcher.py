"""Security-focused unit tests for the injected browser fetcher boundary."""

from __future__ import annotations

from typing import Any

from knowledge.browser_fetcher import (
    BrowserFetcher,
    BrowserFetchResult,
    BrowserSessionAccessError,
    BrowserSessionHealth,
    BrowserSessionStatus,
)
from scripts.launch_authorized_browser import (
    probe_live_context,
    visible_context_health,
    visible_page_health,
)


class Adapter:
    def __init__(self) -> None:
        self.target: dict[str, Any] | None = None
        self.run: dict[str, Any] | None = None

    def check_session_health(self) -> dict[str, str]:
        return {"status": "authenticated", "Authorization": "must-not-leak"}

    def fetch(
        self, target: dict[str, Any], cursor: str | None, run: dict[str, Any]
    ) -> dict[str, Any]:
        self.target = target
        self.run = run
        return {
            "source_link": "https://example.invalid/post/1",
            "platform_content_id": "p1",
            "published_at": "2025-01-01T00:00:00Z",
            "duration_sec": "2.5",
            "temporary_media_path": "/tmp/p1.mp4",
            "media_status": "ready",
            "Cookie": "secret",
            "generalToken": "secret",
            "Authorization": "secret",
        }


def test_fetch_projects_only_business_fields_and_filters_inputs() -> None:
    adapter = Adapter()
    fetcher = BrowserFetcher(adapter)
    result = fetcher.fetch(
        {
            "source_link": "https://example.invalid/post/1",
            "Cookie": "secret",
            "target_id": 3,
        },
        "cursor-1",
        {"run_id": 4, "run_type": "daily_scan", "Authorization": "secret"},
    )
    assert isinstance(result, BrowserFetchResult)
    assert result.as_dict() == {
        "source_link": "https://example.invalid/post/1",
        "platform_content_id": "p1",
        "published_at": "2025-01-01T00:00:00Z",
        "duration_sec": 2.5,
        "media_status": "ready",
    }
    assert result.internal_dict()["temporary_media_path"] == "/tmp/p1.mp4"
    assert adapter.target == {
        "source_link": "https://example.invalid/post/1",
        "target_id": 3,
    }
    assert adapter.run == {"run_id": 4, "run_type": "daily_scan"}


def test_session_health_normalizes_unknown_status_without_raw_fields() -> None:
    fetcher = BrowserFetcher(Adapter())
    health = fetcher.session_health()
    assert health.status is BrowserSessionStatus.AUTHENTICATED
    assert isinstance(health, BrowserSessionHealth)
    assert "Authorization" not in health.__dict__


def test_call_is_creator_worker_compatible_and_preserves_page_metadata() -> None:
    class PageAdapter(Adapter):
        def fetch(
            self, target: dict[str, Any], cursor: str | None, run: dict[str, Any]
        ) -> dict[str, Any]:
            value = super().fetch(target, cursor, run)
            return {
                "items": [value],
                "next_cursor": "n1",
                "has_more": True,
                "done": False,
            }

    fetcher = BrowserFetcher(PageAdapter())
    page = fetcher({"source_link": "https://example.invalid/post/1"}, None, {})
    assert page["done"] is False
    assert page["next_cursor"] == "n1"
    assert len(page["items"]) == 1
    assert "temporary_media_path" in page["items"][0]


def test_session_gate_prevents_adapter_fetch_for_non_authenticated_states() -> None:
    class GateAdapter(Adapter):
        def __init__(self, status: str) -> None:
            super().__init__()
            self.status = status
            self.fetch_calls = 0

        def check_session_health(self) -> dict[str, str]:
            return {"status": self.status}

        def fetch(
            self, target: dict[str, Any], cursor: str | None, run: dict[str, Any]
        ) -> dict[str, Any]:
            self.fetch_calls += 1
            return super().fetch(target, cursor, run)

    expected = {
        "login_required": "AUTHORIZATION_REQUIRED",
        "expired": "AUTHORIZATION_REQUIRED",
        "blocked": "SESSION_BLOCKED",
        "unknown": "SESSION_UNKNOWN",
    }
    for status, code in expected.items():
        adapter = GateAdapter(status)
        try:
            BrowserFetcher(adapter)(
                {"source_link": "https://example.invalid/post/1"}, None, {}
            )
            raise AssertionError("session gate should reject non-authenticated state")
        except BrowserSessionAccessError as exc:
            assert exc.code == code
        assert adapter.fetch_calls == 0


def test_visible_page_health_rejects_generic_https() -> None:
    class Page:
        url = "https://example.invalid/home"

        def title(self) -> str:
            return "Home"

    assert visible_page_health(Page()).status is BrowserSessionStatus.UNKNOWN


def test_visible_page_health_requires_official_video_channel_page() -> None:
    class LoginPage:
        url = "https://channels.weixin.qq.com/login"

        def title(self) -> str:
            return "视频号登录"

    class SignedInPage:
        url = "https://channels.weixin.qq.com/platform/home"

        def title(self) -> str:
            return "视频号管理"

    assert (
        visible_page_health(LoginPage()).status is BrowserSessionStatus.LOGIN_REQUIRED
    )
    assert (
        visible_page_health(SignedInPage()).status is BrowserSessionStatus.AUTHENTICATED
    )


def test_visible_page_health_probe_uses_only_url_and_title() -> None:
    class Page:
        url = "https://channels.weixin.qq.com/platform/home"

        def title(self) -> str:
            return "视频号管理"

        def cookies(self) -> None:
            raise AssertionError("cookies must not be read")

    assert visible_page_health(Page()).status is BrowserSessionStatus.AUTHENTICATED


def test_live_context_health_uses_visible_page_only() -> None:
    class Page:
        url = "https://example.invalid/creator"

        def title(self) -> str:
            return "Creator"

        def context(self) -> None:
            raise AssertionError("context state must not be read")

    class Context:
        pages = [Page()]

        def cookies(self) -> None:
            raise AssertionError("cookies must not be read")

    assert visible_context_health(Context()).status is BrowserSessionStatus.UNKNOWN
    assert probe_live_context(Context()).status is BrowserSessionStatus.UNKNOWN


def test_injected_health_probe_controls_session_gate() -> None:
    adapter = Adapter()
    fetcher = BrowserFetcher(
        adapter, health_probe=lambda: BrowserSessionStatus.LOGIN_REQUIRED
    )
    try:
        fetcher({"source_link": "https://example.invalid/post/1"}, None, {})
        raise AssertionError("login-required probe must block fetch")
    except BrowserSessionAccessError as exc:
        assert exc.code == "AUTHORIZATION_REQUIRED"
    assert adapter.target is None


def test_public_dict_hides_temporary_media_path() -> None:
    result = BrowserFetcher(Adapter()).fetch(
        {"source_link": "https://example.invalid/post/1"}
    )
    assert isinstance(result, BrowserFetchResult)
    assert "temporary_media_path" not in result.public_dict()
