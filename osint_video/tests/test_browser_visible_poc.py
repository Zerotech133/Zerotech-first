"""Pure DOM-fixture tests for the single-link visible browser PoC."""
from __future__ import annotations

from typing import Any

import pytest

from collectors.browser_visible_poc import (
    BrowserVisiblePagePoc,
    VisiblePocError,
    extract_visible_dom_fields,
)
from knowledge.authorized_browser import AuthorizedBrowserSession
from knowledge.browser_fetcher import BrowserSessionHealth, BrowserSessionStatus


SOURCE = "https://channels.weixin.qq.com/platform/home"


class Context:
    def close(self) -> None:
        return None


class Locator:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.first = self

    def text_content(self) -> str | None:
        return self.value


class FixturePage:
    def __init__(self, url: str = SOURCE, values: dict[str, str] | None = None) -> None:
        self.url = url
        self.values = values if values is not None else {
            "[data-osint='title']": "Fixture title",
            "[data-osint='description']": "Fixture description",
            "[data-osint='creator']": "Fixture creator",
            "[data-osint='published-at']": "2025-01-01",
            "[data-osint='duration-sec']": "12.5",
            "[data-osint='media-status']": "processing",
        }
        self.goto_calls: list[str] = []

    def title(self) -> str:
        return "视频号管理"

    def goto(self, url: str, **_: Any) -> None:
        self.goto_calls.append(url)
        self.url = "https://channels.weixin.qq.com/platform/home"

    def locator(self, selector: str) -> Locator:
        return Locator(self.values.get(selector))


def session(page: FixturePage, status: BrowserSessionStatus = BrowserSessionStatus.AUTHENTICATED) -> AuthorizedBrowserSession:
    return AuthorizedBrowserSession(Context(), page, lambda _: BrowserSessionHealth(status))


def test_navigation_and_redacted_report() -> None:
    page = FixturePage()
    report = BrowserVisiblePagePoc(session(page), extract_visible_dom_fields).collect_report(SOURCE)
    assert page.goto_calls == [SOURCE]
    assert report.final_url == SOURCE
    assert report.fields is not None
    assert "cookies" not in repr(report).lower()
    assert "token" not in repr(report).lower()


def test_sph_share_link_resolves_to_official_landing_page() -> None:
    page = FixturePage()
    report = BrowserVisiblePagePoc(session(page), extract_visible_dom_fields).collect_report("https://weixin.qq.com/sph/abc")
    assert page.goto_calls == ["https://weixin.qq.com/sph/abc"]
    assert report.final_url == SOURCE


def test_url_mismatch_and_cross_domain_are_rejected() -> None:
    mismatch = FixturePage(url="https://channels.weixin.qq.com/platform/other")
    mismatch.goto = lambda url, **kwargs: setattr(mismatch, "url", "https://channels.weixin.qq.com/platform/other")
    with pytest.raises(VisiblePocError, match="final page URL") as exc:
        BrowserVisiblePagePoc(session(mismatch), extract_visible_dom_fields).collect_report(SOURCE)
    assert exc.value.code == "PAGE_LINK_MISMATCH"

    cross = FixturePage()
    cross.goto = lambda url, **kwargs: setattr(cross, "url", "https://evil.example/landing")
    with pytest.raises(VisiblePocError) as cross_error:
        BrowserVisiblePagePoc(session(cross), extract_visible_dom_fields).collect_report("https://weixin.qq.com/sph/abc")
    assert cross_error.value.code == "LINK_RESOLUTION_FAILED"


def test_dom_mismatch_and_health_gate_have_explicit_codes() -> None:
    empty = FixturePage(values={})
    with pytest.raises(VisiblePocError) as dom_error:
        BrowserVisiblePagePoc(session(empty), extract_visible_dom_fields).collect_report(SOURCE)
    assert dom_error.value.code == "DOM_MISMATCH"

    with pytest.raises(VisiblePocError) as health_error:
        BrowserVisiblePagePoc(session(FixturePage(), BrowserSessionStatus.UNKNOWN), extract_visible_dom_fields).collect_report(SOURCE)
    assert health_error.value.code == "HEALTH_GATE"
