"""Safe platform adapter backed by explicitly supplied page data.

The adapter is intentionally platform-agnostic.  It does not perform discovery,
site-wide search, browser attachment, credential handling, or media downloads.
Hosts inject either a page-data provider or an already-authorized browser bridge.
Only allow-listed page fields are projected to :mod:`collectors.ports` DTOs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from collectors.ports import (
    CollectionAccessError,
    CollectionFailureError,
    CreatorRef,
    CreatorResolveRequest,
    CreatorResolveResult,
    MediaAvailability,
    SessionHealth,
    SessionStatus,
    TextChannel,
    TextSegment,
    VideoMetadata,
    VideoPage,
)


class PageDataProvider(Protocol):
    """Load page data for one caller-supplied link only."""

    def __call__(self, source_link: str) -> Mapping[str, Any]:
        """Return visible, authorized page data for ``source_link``."""


class PageDataParser(Protocol):
    """Parse one provider response into business fields."""

    def __call__(self, raw: Mapping[str, Any], source_link: str) -> Mapping[str, Any]:
        """Return a mapping containing only page business data."""


class BrowserAttach(Protocol):
    """Optional attach hook supplied by a host-owned authorized browser bridge."""

    def __call__(self) -> Any:
        """Attach to a browser without exposing credentials or storage state."""


class PlatformAdapter:
    """Minimal secure :class:`~collectors.ports.CollectorAdapter` implementation.

    This class never searches a platform.  Every operation requires a concrete
    HTTPS link supplied by the caller and an authenticated health result.  A
    provider can be a deterministic page-data fixture in tests or a host-owned
    browser bridge that returns visible page data.  Cookies, tokens,
    authorization headers and storage state are neither read nor retained.
    """

    def __init__(
        self,
        page_data_provider: PageDataProvider | None = None,
        *,
        page_data: Mapping[str, Mapping[str, Any]] | None = None,
        page_parser: PageDataParser | None = None,
        health_probe: Callable[[], SessionHealth | SessionStatus | Mapping[str, Any]] | None = None,
        initial_status: SessionStatus = SessionStatus.UNKNOWN,
        browser_attach: BrowserAttach | None = None,
    ) -> None:
        if page_data_provider is not None and page_data is not None:
            raise ValueError("provide page_data_provider or page_data, not both")
        if not isinstance(initial_status, SessionStatus):
            try:
                initial_status = SessionStatus(str(initial_status))
            except ValueError:
                initial_status = SessionStatus.UNKNOWN
        self._page_data_provider = page_data_provider
        self._page_data = dict(page_data or {})
        self._page_parser = page_parser or self._default_page_parser
        self._health_probe = health_probe
        self._initial_status = initial_status
        self._browser_attach = browser_attach

    def session_health(self) -> SessionHealth:
        """Return coarse health and never expose raw probe responses."""
        checked_at = datetime.now(timezone.utc)
        if self._health_probe is not None:
            try:
                raw = self._health_probe()
            except Exception:
                return SessionHealth(SessionStatus.UNKNOWN, checked_at, "HEALTH_PROBE_FAILED")
            return self._normalize_health(raw, checked_at)
        if self._page_data:
            return SessionHealth(self._initial_status, checked_at)
        # An attach hook is not itself proof of authorization.  Do not claim
        # authenticated until the host's visible-page probe says so.
        if self._browser_attach is None:
            return SessionHealth(SessionStatus.UNKNOWN, checked_at, "BROWSER_ATTACH_UNAVAILABLE")
        return SessionHealth(self._initial_status, checked_at)

    def resolve_creator(self, request: CreatorResolveRequest) -> CreatorResolveResult:
        """Resolve creator metadata from the one explicitly supplied source link."""
        self._ensure_access()
        try:
            page = self._load_page(request.source_link)
            creator = self._map_creator(page, request.platform)
            if creator is None:
                return CreatorResolveResult(None, False, request.source_link, "CREATOR_NOT_FOUND")
            return CreatorResolveResult(creator, True, request.source_link, None)
        except (CollectionAccessError, ValueError):
            raise
        except Exception:
            return CreatorResolveResult(None, False, request.source_link, "PAGE_DATA_UNAVAILABLE")

    def fetch_video(self, source_link: str, cursor: str | None = None) -> VideoPage:
        """Fetch one caller-supplied page; cursor is accepted for port compatibility."""
        del cursor  # Pagination is intentionally not invented by this adapter.
        self._ensure_access()
        # VideoMetadata validates HTTPS/private-host/credential query policy.
        VideoMetadata(source_link=source_link)
        try:
            page = self._load_page(source_link)
            metadata = self._map_metadata(page, source_link)
            segments = self._map_segments(page)
            return VideoPage(
                metadata=metadata,
                text_segments=segments,
                next_cursor=self._safe_cursor(page.get("next_cursor")),
                done=self._done(page),
            )
        except (CollectionAccessError, ValueError):
            raise
        except CollectionFailureError:
            raise
        except Exception as exc:
            raise CollectionFailureError("PAGE_DATA_UNAVAILABLE", "authorized page data unavailable") from exc

    def _ensure_access(self) -> None:
        health = self.session_health()
        if health.status is SessionStatus.AUTHENTICATED:
            return
        if health.status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
            raise CollectionAccessError("AUTHORIZATION_REQUIRED", "collector session requires authorization")
        if health.status is SessionStatus.BLOCKED:
            raise CollectionAccessError("SESSION_BLOCKED", "collector session is blocked")
        if health.reason == "BROWSER_ATTACH_UNAVAILABLE":
            raise CollectionAccessError("BROWSER_ATTACH_UNAVAILABLE", "authorized browser attach is unavailable")
        raise CollectionAccessError("SESSION_UNKNOWN", "collector session health is unknown")

    def _load_page(self, source_link: str) -> Mapping[str, Any]:
        """Load only an exact requested link; no fallback or search is allowed."""
        VideoMetadata(source_link=source_link)
        if self._page_data_provider is not None:
            raw = self._page_data_provider(source_link)
        elif self._page_data:
            raw = self._page_data.get(source_link)
            if raw is None:
                raise CollectionFailureError("PAGE_NOT_PROVIDED", "page data was not supplied for this link")
        elif self._browser_attach is not None:
            # The host bridge may return a visible page-data mapping.  We do not
            # inspect the attached object and never ask it for browser state.
            raw = self._browser_attach()
        else:
            raise CollectionAccessError("BROWSER_ATTACH_UNAVAILABLE", "authorized browser attach is unavailable")
        if not isinstance(raw, Mapping):
            raise CollectionFailureError("INVALID_PAGE_DATA", "page provider returned no page mapping")
        authorized = raw.get("authorized")
        if authorized is False or str(raw.get("authorization_state", "")).lower() in {"login_required", "expired", "blocked", "unknown"}:
            raise CollectionAccessError("AUTHORIZATION_REQUIRED", "page is not authorized")
        raw_link = raw.get("source_link") or raw.get("link")
        if raw_link is not None and raw_link != source_link:
            raise CollectionFailureError("PAGE_LINK_MISMATCH", "page data does not match requested link")
        parsed = self._page_parser(raw, source_link)
        if not isinstance(parsed, Mapping):
            raise CollectionFailureError("INVALID_PAGE_DATA", "page parser returned no page mapping")
        return self._strip_sensitive(parsed)

    @classmethod
    def _default_page_parser(cls, raw: Mapping[str, Any], source_link: str) -> Mapping[str, Any]:
        """Copy recognized fields while dropping credentials and browser state."""
        del source_link
        return cls._strip_sensitive(raw)

    @classmethod
    def _map_creator(cls, page: Mapping[str, Any], platform: str | None) -> CreatorRef | None:
        raw = page.get("creator")
        if not isinstance(raw, Mapping):
            raw = page
        creator_id = cls._string(raw.get("creator_id", raw.get("finder_id", raw.get("author_id"))))
        nickname = cls._string(raw.get("nickname", raw.get("author_name", raw.get("creator_name"))))
        profile_url = cls._safe_profile_url(raw.get("profile_url"))
        resolved_platform = cls._string(raw.get("platform")) or platform
        if not (creator_id or nickname):
            return None
        return CreatorRef(resolved_platform or "unknown", creator_id, nickname, profile_url, raw.get("confidence"))

    @classmethod
    def _map_metadata(cls, page: Mapping[str, Any], source_link: str) -> VideoMetadata:
        raw = page.get("metadata") if isinstance(page.get("metadata"), Mapping) else page
        creator = cls._map_creator(raw, cls._string(raw.get("platform")))
        media = raw.get("media_status", MediaAvailability.UNAVAILABLE)
        return VideoMetadata(
            source_link=source_link,
            platform=cls._string(raw.get("platform")),
            content_id=cls._string(raw.get("content_id", raw.get("platform_content_id"))),
            title=cls._string(raw.get("title")),
            description=cls._string(raw.get("description")),
            published_at=cls._string(raw.get("published_at")),
            duration_sec=raw.get("duration_sec", raw.get("duration")),
            creator=creator,
            media_status=media,
        )

    @classmethod
    def _map_segments(cls, page: Mapping[str, Any]) -> tuple[TextSegment, ...]:
        raw_values = page.get("text_segments", page.get("text_channels", page.get("segments", ())))
        if isinstance(raw_values, Mapping):
            expanded: list[Mapping[str, Any]] = []
            for channel in ("asr", "ocr", "subtitle", "description", "comment", "manual"):
                values = raw_values.get(channel, ())
                if isinstance(values, Mapping):
                    values = [values]
                if isinstance(values, (list, tuple)):
                    expanded.extend({**item, "channel": item.get("channel", channel)} for item in values if isinstance(item, Mapping))
            raw_values = expanded
        if not isinstance(raw_values, (list, tuple)):
            return ()
        result: list[TextSegment] = []
        for item in raw_values:
            if not isinstance(item, Mapping):
                continue
            text = cls._string(item.get("text"))
            if not text:
                continue
            try:
                result.append(TextSegment(text, item.get("channel", TextChannel.MANUAL), item.get("start_sec"), item.get("end_sec"), item.get("confidence")))
            except (TypeError, ValueError):
                continue
        return tuple(result)

    @staticmethod
    def _normalize_health(raw: Any, checked_at: datetime) -> SessionHealth:
        if isinstance(raw, SessionHealth):
            return raw
        status = raw if isinstance(raw, SessionStatus) else raw.get("status") if isinstance(raw, Mapping) else SessionStatus.UNKNOWN
        try:
            status_value = status if isinstance(status, SessionStatus) else SessionStatus(str(status))
        except ValueError:
            status_value = SessionStatus.UNKNOWN
        reason = raw.get("reason") if isinstance(raw, Mapping) and isinstance(raw.get("reason"), str) else None
        return SessionHealth(status_value, checked_at, reason[:80] if reason else None)

    @staticmethod
    def _strip_sensitive(value: Any) -> Any:
        sensitive = {"cookie", "cookies", "token", "generaltoken", "authorization", "access_token", "refresh_token", "session", "storage_state", "storagestate", "headers"}
        if isinstance(value, Mapping):
            return {key: PlatformAdapter._strip_sensitive(item) for key, item in value.items() if str(key).lower() not in sensitive}
        if isinstance(value, list):
            return [PlatformAdapter._strip_sensitive(item) for item in value]
        if isinstance(value, tuple):
            return tuple(PlatformAdapter._strip_sensitive(item) for item in value)
        return value

    @staticmethod
    def _string(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else (str(value) if value is not None else None)

    @staticmethod
    def _safe_profile_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            VideoMetadata(source_link=value)
        except ValueError:
            return None
        return value.strip()

    @staticmethod
    def _safe_cursor(value: Any) -> str | None:
        return value.strip()[:256] if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _done(page: Mapping[str, Any]) -> bool:
        if "done" in page:
            return bool(page.get("done"))
        if "has_more" in page:
            return not bool(page.get("has_more"))
        return page.get("next_cursor") is None


__all__ = ["BrowserAttach", "PageDataParser", "PageDataProvider", "PlatformAdapter"]
