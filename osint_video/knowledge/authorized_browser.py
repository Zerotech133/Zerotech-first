"""In-process authorized browser session and visible-page adapter.

The session owns a live browser context and a *specific* page without exposing
either object outside the process. The visible-page adapter reads only
allow-listed DOM selectors from that bound page. It never evaluates script,
reads cookies, headers, storage state, tokens, or arbitrary browser paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlparse
import ipaddress

from knowledge.browser_fetcher import (
    BrowserSessionAccessError,
    BrowserSessionHealth,
    BrowserSessionStatus,
)


class VisiblePageHealthProbe(Protocol):
    """Probe that inspects only visible page URL/title and returns coarse health."""

    def __call__(self, page: Any) -> BrowserSessionHealth:
        """Return safe health for the page without reading browser state."""


class AuthorizedBrowserSession:
    """Own a browser context in-process and expose health/read/close operations."""

    def __init__(
        self,
        context: Any | None,
        page: Any,
        health_probe: VisiblePageHealthProbe,
    ) -> None:
        if page is None:
            raise ValueError("page is required")
        if not callable(health_probe):
            raise ValueError("health_probe is required")
        # ``context`` remains an optional legacy owner for launch-script
        # shutdown. Page-only hosts should use ``from_page`` and retain no
        # context/profile object in this session.
        self._context: Any | None = context
        self._page: Any | None = page
        self._health_probe: VisiblePageHealthProbe = health_probe
        self._closed: bool = False

    @classmethod
    def from_page(
        cls, page: Any, health_probe: VisiblePageHealthProbe
    ) -> "AuthorizedBrowserSession":
        """Build a page-only session without accepting a context or profile."""
        if page is None:
            raise ValueError("page is required")
        return cls(None, page, health_probe)

    @property
    def closed(self) -> bool:
        """Return whether this in-process session has been closed."""
        return self._closed

    def health(self) -> BrowserSessionHealth:
        """Return coarse health from the visible page, never browser state."""
        if self._closed or self._page is None:
            return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
        try:
            health = self._health_probe(self._page)
        except Exception:
            return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
        if not isinstance(health, BrowserSessionHealth):
            try:
                health = BrowserSessionHealth(status=health)
            except (TypeError, ValueError):
                return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
        return health

    def read_visible(self, reader: Callable[[Any], Any]) -> Any:
        """Run one reader against the bound visible page and pass its value back.

        The reader is invoked exactly once with the exact page this session owns
        and its return value is neither copied nor coerced nor validated here.
        The page object itself is never returned, so callers cannot retain or
        rebind it. Errors raised by the reader propagate unchanged; only a
        closed session or a discarded page is refused.
        """
        if not callable(reader):
            raise ValueError("reader is required")
        if self._closed or self._page is None:
            raise RuntimeError("authorized browser session is closed")
        return reader(self._page)

    def close(self) -> None:
        """Close the owned context and discard in-process references."""
        if self._closed:
            return
        self._closed = True
        context = self._context
        self._page = None
        self._context = None
        if context is None:
            return
        try:
            context.close()
        except Exception:
            # Closing is best effort; no browser state is inspected or returned.
            return

    def page_adapter(
        self, selectors: "VisiblePageSelectors | None" = None
    ) -> "VisiblePageBrowserAdapter":
        """Create a safe adapter bound to this session's exact page."""
        if self._closed or self._page is None:
            raise RuntimeError("authorized browser session is closed")
        return VisiblePageBrowserAdapter(self, self._page, selectors=selectors)


@dataclass(frozen=True)
class VisiblePageSelectors:
    """Allow-listed DOM selectors for visible business fields."""

    content_id: tuple[str, ...] = ("[data-content-id]",)
    title: tuple[str, ...] = ("[data-video-title]", "h1")
    description: tuple[str, ...] = ("[data-video-description]", "[data-description]")
    published_at: tuple[str, ...] = ("[data-published-at]", "time")
    duration: tuple[str, ...] = ("[data-duration]",)
    creator_id: tuple[str, ...] = ("[data-creator-id]",)
    creator_name: tuple[str, ...] = ("[data-creator-name]",)
    creator_profile: tuple[str, ...] = ("[data-creator-profile]",)
    asr: tuple[str, ...] = ("[data-text-channel='asr']",)
    ocr: tuple[str, ...] = ("[data-text-channel='ocr']",)
    subtitle: tuple[str, ...] = ("[data-text-channel='subtitle']",)
    description_text: tuple[str, ...] = ("[data-text-channel='description']",)
    comment: tuple[str, ...] = ("[data-text-channel='comment']",)


class VisiblePageBrowserAdapter:
    """Read business fields from one authorized visible page only.

    ``page_bound`` is an explicit composition-root capability marker. Hosts may
    pass this adapter to worker composition; generic context/profile adapters
    intentionally do not satisfy that contract.

    ``page`` is explicitly bound by the host. This adapter never discovers or
    selects pages from a context and never invokes ``evaluate``. A caller must
    still provide a visible-page health probe; health is checked both before
    and after reading the page.
    """

    def __init__(
        self,
        session: AuthorizedBrowserSession,
        page: Any,
        *,
        selectors: VisiblePageSelectors | None = None,
    ) -> None:
        if session is None or page is None:
            raise ValueError("session and page are required")
        if session.closed:
            raise ValueError("authorized browser session is closed")
        self._session = session
        self._page = page
        self._selectors = selectors or VisiblePageSelectors()
        self.page_bound: bool = True

    def check_session_health(self) -> BrowserSessionHealth:
        """Return current coarse health from the bound visible page."""
        return self._session.health()

    def fetch(
        self,
        target: Mapping[str, Any],
        cursor: str | None = None,
        run: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Extract allow-listed visible fields for one exact HTTPS source link."""
        del cursor, run
        health = self._session.health()
        if health.status is not BrowserSessionStatus.AUTHENTICATED:
            raise BrowserSessionAccessError(
                "SESSION_NOT_AUTHENTICATED", "visible browser page is not authenticated"
            )
        source_link = target.get("source_link") or target.get("link")
        _validate_exact_https_link(source_link)
        bound_url = getattr(self._page, "url", "")
        if not isinstance(bound_url, str) or bound_url.strip() != source_link.strip():
            raise BrowserSessionAccessError(
                "PAGE_LINK_MISMATCH",
                "bound visible page does not match requested source link",
            )
        values: dict[str, Any] = {
            "source_link": source_link,
            "content_id": self._read_first(
                self._selectors.content_id, attribute="data-content-id"
            ),
            "title": self._read_first(self._selectors.title),
            "description": self._read_first(self._selectors.description),
            "published_at": self._read_first(
                self._selectors.published_at, attribute="datetime"
            ),
            "duration_sec": self._read_first(
                self._selectors.duration, attribute="data-duration"
            ),
        }
        creator = self._creator()
        if creator:
            values["creator"] = creator
        channels: list[dict[str, Any]] = []
        channel_selectors = {
            "asr": self._selectors.asr,
            "ocr": self._selectors.ocr,
            "subtitle": self._selectors.subtitle,
            "description": self._selectors.description_text,
            "comment": self._selectors.comment,
        }
        for channel, channel_values in channel_selectors.items():
            for text in self._read_all(channel_values):
                channels.append({"text": text, "channel": channel})
        if channels:
            values["text_channels"] = channels
        result = self._session.health()
        if result.status is not BrowserSessionStatus.AUTHENTICATED:
            raise BrowserSessionAccessError(
                "SESSION_NOT_AUTHENTICATED",
                "visible browser page authorization changed",
            )
        return values

    def _creator(self) -> dict[str, str] | None:
        creator_id = self._read_first(
            self._selectors.creator_id, attribute="data-creator-id"
        )
        nickname = self._read_first(self._selectors.creator_name)
        profile_url = self._read_first(
            self._selectors.creator_profile, attribute="href"
        )
        creator = {
            key: value
            for key, value in (
                ("creator_id", creator_id),
                ("nickname", nickname),
                ("profile_url", profile_url),
            )
            if value
        }
        return creator or None

    def _read_first(
        self, selectors: Sequence[str], *, attribute: str | None = None
    ) -> str | None:
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                if attribute:
                    value = locator.get_attribute(attribute)
                else:
                    value = locator.inner_text()
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _read_all(self, selectors: Sequence[str]) -> list[str]:
        for selector in selectors:
            try:
                values = self._page.locator(selector).all_inner_texts()
            except Exception:
                continue
            if isinstance(values, list):
                return [
                    value.strip()
                    for value in values
                    if isinstance(value, str) and value.strip()
                ]
        return []


def _validate_exact_https_link(value: Any) -> None:
    """Validate caller-supplied source link without resolving or navigating."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_link is required")
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source_link must use https")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    ):
        raise ValueError("source_link host is not allowed")
    forbidden = {
        "cookie",
        "token",
        "authorization",
        "access_token",
        "refresh_token",
        "session",
    }
    if forbidden.intersection(key.lower() for key, _ in parse_qsl(parsed.query)):
        raise ValueError("source_link contains credential-like query parameters")


__all__ = [
    "AuthorizedBrowserSession",
    "VisiblePageBrowserAdapter",
    "VisiblePageHealthProbe",
    "VisiblePageSelectors",
]
