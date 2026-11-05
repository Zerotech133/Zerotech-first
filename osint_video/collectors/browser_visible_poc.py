"""Single-link headed-browser visible-fields proof of concept.

This module is deliberately side-effect free: it accepts one exact HTTPS source
link and an in-process :class:`AuthorizedBrowserSession`, reads only visible DOM
text through a caller-supplied extractor, and returns an in-memory DTO. It does
not persist, enqueue, download media, attach externally, or inspect credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from collectors.ports import VideoMetadata
from knowledge.authorized_browser import AuthorizedBrowserSession
from knowledge.browser_fetcher import BrowserSessionStatus


class VisiblePocError(RuntimeError):
    """Expected failure in the side-effect-free visible page PoC."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VisibleRedirect:
    """Public URL redirect observation; no headers or browser state."""

    url: str


@dataclass(frozen=True)
class VisiblePocReport:
    """Sanitized URL/DOM report retained only in memory."""

    source_link: str
    initial_url: str
    final_url: str
    redirects: tuple[VisibleRedirect, ...]
    fields: "VisiblePageFields | None"
    error_code: str | None


@dataclass(frozen=True)
class VisiblePageFields:
    """Allow-listed visible business fields for one requested source link."""

    source_link: str
    title: str | None = None
    description: str | None = None
    creator_name: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    media_status: str = "unavailable"

    def as_dict(self) -> dict[str, Any]:
        """Serialize only visible business fields."""
        return {
            "source_link": self.source_link,
            "title": self.title,
            "description": self.description,
            "creator_name": self.creator_name,
            "published_at": self.published_at,
            "duration_sec": self.duration_sec,
            "media_status": self.media_status,
        }


VisibleFieldsExtractor = Callable[[Any, str], Mapping[str, Any]]


class BrowserVisiblePagePoc:
    """Extract visible fields from one exact link inside an owned session."""

    def __init__(
        self, session: AuthorizedBrowserSession, extractor: VisibleFieldsExtractor
    ) -> None:
        if session is None:
            raise ValueError("session is required")
        if not callable(extractor):
            raise ValueError("extractor is required")
        self._session = session
        self._extractor = extractor

    def collect(self, source_link: str) -> VisiblePageFields:
        """Collect one page after a coarse authenticated health check."""
        report = self.collect_report(source_link)
        if report.fields is None:
            raise VisiblePocError(
                report.error_code or "DOM_MISMATCH", "visible fields were not found"
            )
        return report.fields

    def collect_report(self, source_link: str) -> "VisiblePocReport":
        """Navigate one exact link and return a sanitized in-memory report."""
        if not isinstance(source_link, str) or not source_link.strip():
            raise ValueError("source_link is required")
        source_link = source_link.strip()
        VideoMetadata(source_link=source_link)
        if not _is_supported_link(source_link):
            raise VisiblePocError(
                "LINK_RESOLUTION_FAILED", "source link is not an allowed public link"
            )
        health = self._session.health()
        if health.status is not BrowserSessionStatus.AUTHENTICATED:
            raise VisiblePocError(
                "HEALTH_GATE", "visible page health is not authenticated"
            )
        initial_url = self._session.read_visible(
            lambda page: str(getattr(page, "url", "") or "")
        )
        self._session.read_visible(lambda page: _navigate(page, source_link))
        final_url = self._session.read_visible(
            lambda page: str(getattr(page, "url", "") or "")
        )
        redirects = tuple(
            VisibleRedirect(url=value) for value in (initial_url, final_url) if value
        )
        if not _is_allowed_final_url(final_url):
            raise VisiblePocError(
                "LINK_RESOLUTION_FAILED", "final URL is not an allowed official page"
            )
        if _is_share_link(source_link) and not _is_allowed_final_url(final_url):
            raise VisiblePocError(
                "LINK_RESOLUTION_FAILED", "share link did not resolve to official page"
            )
        if not _urls_match_expected(source_link, final_url):
            raise VisiblePocError(
                "PAGE_LINK_MISMATCH", "final page URL does not match requested link"
            )
        health = self._session.health()
        if health.status is not BrowserSessionStatus.AUTHENTICATED:
            raise VisiblePocError(
                "HEALTH_GATE", "final page health is not authenticated"
            )
        raw = self._session.read_visible(
            lambda page: self._extractor(page, source_link)
        )
        if not isinstance(raw, Mapping):
            raise VisiblePocError(
                "DOM_MISMATCH", "visible extractor returned no mapping"
            )
        fields = self._project(raw, source_link)
        # ``media_status`` defaults to ``"unavailable"``. Counting that default
        # as an observation would leave this emptiness gate permanently open.
        if not any(
            value
            for key, value in fields.as_dict().items()
            if key != "source_link" and value != "unavailable"
        ):
            raise VisiblePocError("DOM_MISMATCH", "visible fields are empty")
        return VisiblePocReport(
            source_link, initial_url, final_url, redirects, fields, None
        )

    @staticmethod
    def _project(raw: Mapping[str, Any], source_link: str) -> VisiblePageFields:
        """Project extractor data and discard all non-business keys."""
        duration = raw.get("duration_sec")
        try:
            duration_value = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_value = None
        if duration_value is not None and (
            not math.isfinite(duration_value) or duration_value < 0
        ):
            duration_value = None
        media_status = raw.get("media_status", "unavailable")
        if media_status not in {
            "unavailable",
            "ready",
            "expired",
            "error",
            "processing",
        }:
            media_status = "unavailable"

        def text(key: str) -> str | None:
            value = raw.get(key)
            if value is None:
                return None
            value = str(value).strip()
            return value or None

        return VisiblePageFields(
            source_link=source_link,
            title=text("title"),
            description=text("description"),
            creator_name=text("creator_name"),
            published_at=text("published_at"),
            duration_sec=duration_value,
            media_status=str(media_status),
        )


def _is_share_link(source_link: str) -> bool:
    parsed = urlparse(source_link)
    return parsed.hostname == "weixin.qq.com" and parsed.path.startswith("/sph/")


def _is_supported_link(source_link: str) -> bool:
    parsed = urlparse(source_link)
    return parsed.scheme.lower() == "https" and (
        parsed.hostname == "channels.weixin.qq.com" or _is_share_link(source_link)
    )


def _is_allowed_final_url(final_url: str) -> bool:
    parsed = urlparse(final_url)
    if parsed.scheme.lower() != "https" or parsed.hostname != "channels.weixin.qq.com":
        return False
    return any(
        parsed.path == prefix or parsed.path.startswith(prefix + "/")
        for prefix in ("/", "/platform", "/channels", "/web")
    )


def _urls_match_expected(source_link: str, final_url: str) -> bool:
    if _is_share_link(source_link):
        return _is_allowed_final_url(final_url)
    return source_link.rstrip("/") == final_url.rstrip("/")


def _navigate(page: Any, source_link: str) -> None:
    goto = getattr(page, "goto", None)
    if not callable(goto):
        raise VisiblePocError(
            "LINK_RESOLUTION_FAILED", "page navigation is unavailable"
        )
    goto(source_link, wait_until="domcontentloaded")


def extract_visible_dom_fields(page: Any, source_link: str) -> Mapping[str, Any]:
    """Read only visible text from a controlled DOM fixture or Playwright page.

    Selectors are intentionally generic and can be replaced by a platform-owned
    fixture adapter. No evaluate, cookies, headers, storage, or network API is
    used.
    """
    del source_link

    def text(selector: str) -> str | None:
        locator = page.locator(selector)
        value = locator.first.text_content()
        return value.strip() if isinstance(value, str) and value.strip() else None

    values: dict[str, Any] = {}
    selectors = {
        "title": "[data-osint='title']",
        "description": "[data-osint='description']",
        "creator_name": "[data-osint='creator']",
        "published_at": "[data-osint='published-at']",
        "duration_sec": "[data-osint='duration-sec']",
        "media_status": "[data-osint='media-status']",
    }
    for key, selector in selectors.items():
        value = text(selector)
        if value is not None:
            values[key] = value
    return values


__all__ = [
    "BrowserVisiblePagePoc",
    "VisiblePageFields",
    "VisiblePocError",
    "VisiblePocReport",
    "VisibleRedirect",
    "extract_visible_dom_fields",
]
