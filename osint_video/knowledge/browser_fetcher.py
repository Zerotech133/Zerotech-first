"""Safe browser-session collection boundary.

This module deliberately contains no platform-specific browser code.  A browser
adapter is injected by the application and may be backed by Playwright,
Selenium, or a test double.  The adapter contract is intentionally narrow: it
can report coarse session health and return a raw business record.  The public
fetch result is projected onto an allow-list so credentials and browser state
can never cross the worker boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, urlparse
import ipaddress


class BrowserSessionAccessError(RuntimeError):
    """Raised when browser session health does not permit collection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrowserSessionStatus(str, Enum):
    """Coarse browser session state exposed to scheduling code."""

    AUTHENTICATED = "authenticated"
    LOGIN_REQUIRED = "login_required"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrowserSessionHealth:
    """Safe session health DTO.

    Only a status and timestamp are retained.  In particular, this DTO has no
    cookie, token, authorization header, or raw adapter response field.
    """

    status: BrowserSessionStatus = BrowserSessionStatus.UNKNOWN
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        """Normalize status values supplied by simple adapters."""
        if not isinstance(self.status, BrowserSessionStatus):
            object.__setattr__(self, "status", BrowserSessionStatus(str(self.status)))


@dataclass(frozen=True)
class BrowserFetchResult:
    """Allow-listed business fields returned by :class:`BrowserFetcher`."""

    source_link: str
    platform_content_id: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    temporary_media_path: str | None = None
    media_status: str = "unavailable"
    title: str | None = None
    description: str | None = None
    creator: Mapping[str, Any] | None = None
    text_channels: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the public contract without internal temporary media paths."""
        return self.public_dict()

    def public_dict(self) -> dict[str, Any]:
        """Return fields safe for API, Hermes, and event boundaries."""
        value: dict[str, Any] = {
            "source_link": self.source_link,
            "platform_content_id": self.platform_content_id,
            "published_at": self.published_at,
            "duration_sec": self.duration_sec,
            "media_status": self.media_status,
        }
        if self.title is not None:
            value["title"] = self.title
        if self.description is not None:
            value["description"] = self.description
        if self.creator is not None:
            value["creator"] = dict(self.creator)
        if self.text_channels:
            value["text_channels"] = [dict(item) for item in self.text_channels]
        return value

    def internal_dict(self) -> dict[str, Any]:
        """Return worker-internal fields, including temporary media path."""
        return asdict(self)


class BrowserAdapter(Protocol):
    """Injected adapter contract; implementations must own browser details."""

    def check_session_health(self) -> BrowserSessionHealth | BrowserSessionStatus | Mapping[str, Any]:
        """Return coarse health without exposing browser credentials."""

    def fetch(
        self, target: Mapping[str, Any], cursor: str | None, run: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Fetch one business record or a page-shaped mapping."""


class BrowserFetcherProtocol(Protocol):
    """Fetcher protocol compatible with creator worker injection."""

    def fetch(
        self, target: Mapping[str, Any], cursor: str | None = None,
        run: Mapping[str, Any] | None = None,
    ) -> BrowserFetchResult | list[BrowserFetchResult]:
        """Fetch safe business fields only."""

    def session_health(self) -> BrowserSessionHealth:
        """Return safe current session health."""


# Alias with the name used by the architecture notes.
BrowserFetcherPort = BrowserFetcherProtocol


class BrowserSessionHealthProbe(Protocol):
    """Callable coarse health probe backed by visible browser state only."""

    def __call__(self) -> BrowserSessionHealth | BrowserSessionStatus | Mapping[str, Any]:
        """Return health without reading cookies, tokens, or storage state."""


class BrowserFetcher:
    """Safe adapter-backed browser fetcher.

    No default adapter is provided, preventing accidental real-platform calls.
    Callers must inject an adapter explicitly.  ``__call__`` returns the page
    shape expected by ``knowledge.creator_workers.PageFetcher``.
    """

    _PUBLIC_FIELDS = frozenset(
        {
            "source_link",
            "platform_content_id",
            "published_at",
            "duration_sec",
            "temporary_media_path",
            "media_status",
        }
    )

    def __init__(self, adapter: BrowserAdapter, health_probe: BrowserSessionHealthProbe | None = None) -> None:
        if adapter is None:
            raise ValueError("adapter is required")
        self._adapter: BrowserAdapter = adapter
        self._health_probe = health_probe

    def session_health(self) -> BrowserSessionHealth:
        """Get adapter health and normalize failures to UNKNOWN."""
        checked_at = datetime.now(timezone.utc)
        try:
            raw = self._health_probe() if self._health_probe is not None else self._adapter.check_session_health()
        except Exception:
            return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN, checked_at=checked_at)
        if isinstance(raw, BrowserSessionHealth):
            return raw
        if isinstance(raw, BrowserSessionStatus):
            return BrowserSessionHealth(status=raw, checked_at=checked_at)
        if isinstance(raw, Mapping):
            status = raw.get("status", BrowserSessionStatus.UNKNOWN)
            try:
                normalized = BrowserSessionStatus(str(status))
            except ValueError:
                normalized = BrowserSessionStatus.UNKNOWN
            return BrowserSessionHealth(status=normalized, checked_at=checked_at)
        return BrowserSessionHealth(checked_at=checked_at)

    def fetch(
        self,
        target: Mapping[str, Any],
        cursor: str | None = None,
        run: Mapping[str, Any] | None = None,
    ) -> BrowserFetchResult | list[BrowserFetchResult]:
        """Fetch and project records without propagating adapter metadata.

        The direct API returns projected records.  Page metadata is preserved by
        ``__call__`` for the creator worker protocol.
        """
        raw = self._fetch_raw(target, cursor, run or {})
        if isinstance(raw, Mapping) and self._looks_like_page(raw):
            values = raw.get("items", raw.get("data", []))
            if not isinstance(values, (list, tuple)):
                values = []
            return [self._project(item, target) for item in values if isinstance(item, Mapping)]
        if isinstance(raw, Mapping):
            return self._project(raw, target)
        raise TypeError("browser adapter fetch must return a mapping")

    def __call__(
        self, target: Mapping[str, Any], cursor: str | None, run: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Provide ``PageFetcher`` compatibility while retaining page metadata."""
        raw = self._fetch_raw(target, cursor, run)
        if isinstance(raw, Mapping) and self._looks_like_page(raw):
            values = raw.get("items", raw.get("data", []))
            if not isinstance(values, (list, tuple)):
                values = []
            items = [self._project(item, target).internal_dict() for item in values if isinstance(item, Mapping)]
            next_cursor = raw.get("next_cursor", raw.get("cursor"))
            has_more = raw.get("has_more")
            done = raw.get("done")
            if done is None:
                done = False if has_more is True or next_cursor is not None else not bool(has_more)
            return {
                "items": items,
                "next_cursor": str(next_cursor) if next_cursor is not None else None,
                "done": bool(done),
            }
        result = self._project(raw, target) if isinstance(raw, Mapping) else None
        if result is None:
            raise TypeError("browser adapter fetch must return a mapping")
        return {"items": [result.as_dict()], "next_cursor": None, "done": True}

    def _ensure_access(self) -> None:
        """Require authenticated browser session before adapter.fetch."""
        status = self.session_health().status
        if status is BrowserSessionStatus.AUTHENTICATED:
            return
        if status in {BrowserSessionStatus.LOGIN_REQUIRED, BrowserSessionStatus.EXPIRED}:
            raise BrowserSessionAccessError("AUTHORIZATION_REQUIRED", "browser session requires authorization")
        if status is BrowserSessionStatus.BLOCKED:
            raise BrowserSessionAccessError("SESSION_BLOCKED", "browser session is blocked")
        raise BrowserSessionAccessError("SESSION_UNKNOWN", "browser session health is unknown")

    def _fetch_raw(
        self, target: Mapping[str, Any], cursor: str | None, run: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Call the injected adapter exactly once through one shared boundary."""
        self._ensure_access()
        safe_target = self._safe_target(target)
        safe_run = self._safe_run(run)
        raw = self._adapter.fetch(safe_target, cursor, safe_run)
        if not isinstance(raw, Mapping):
            raise TypeError("browser adapter fetch must return a mapping")
        return raw

    @staticmethod
    def _looks_like_page(value: Mapping[str, Any]) -> bool:
        return "items" in value or "data" in value

    @classmethod
    def _project(cls, raw: Mapping[str, Any], target: Mapping[str, Any]) -> BrowserFetchResult:
        """Project an adapter record; unknown (including sensitive) keys vanish."""
        source_link = raw.get("source_link") or target.get("source_link") or target.get("link")
        if not isinstance(source_link, str) or not source_link.strip():
            raise ValueError("source_link is required")
        source_link = source_link.strip()
        parsed_link = urlparse(source_link)
        if parsed_link.scheme.lower() != "https" or not parsed_link.hostname:
            raise ValueError("source_link must use https")
        try:
            address = ipaddress.ip_address(parsed_link.hostname)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                raise ValueError("source_link host is not allowed")
        except ValueError as exc:
            if str(exc) == "source_link host is not allowed":
                raise
        query_keys = {key.lower() for key, _ in parse_qsl(parsed_link.query)}
        if query_keys.intersection({"cookie", "token", "generaltoken", "authorization", "access_token", "refresh_token", "session"}):
            raise ValueError("source_link contains credential-like query parameters")
        duration = raw.get("duration_sec")
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                duration = None
            if duration is not None and (not math.isfinite(duration) or duration < 0):
                duration = None
        media_status = raw.get("media_status", "unavailable")
        if not isinstance(media_status, str) or media_status not in {"unavailable", "ready", "expired", "error", "processing"}:
            media_status = "unavailable"
        published = raw.get("published_at")
        if published is not None and not isinstance(published, str):
            published = str(published)
        content_id = raw.get("platform_content_id")
        if content_id is not None and not isinstance(content_id, str):
            content_id = str(content_id)
        media_path = raw.get("temporary_media_path")
        if media_path is not None and not isinstance(media_path, str):
            media_path = str(media_path)
        title = raw.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else None
        description = raw.get("description")
        description = description.strip() if isinstance(description, str) and description.strip() else None
        creator = raw.get("creator")
        if not isinstance(creator, Mapping):
            creator = None
        else:
            creator = {
                key: value for key, value in creator.items()
                if str(key).lower() in {"platform", "creator_id", "nickname", "profile_url", "confidence"}
            }
        channels = raw.get("text_channels", raw.get("text_segments", ()))
        safe_channels: tuple[Mapping[str, Any], ...] = ()
        if isinstance(channels, Mapping):
            expanded: list[Mapping[str, Any]] = []
            for channel_name, values in channels.items():
                if isinstance(values, Mapping):
                    values = (values,)
                if isinstance(values, (list, tuple)):
                    expanded.extend(
                        {**item, "channel": item.get("channel", channel_name)}
                        for item in values if isinstance(item, Mapping)
                    )
            channels = expanded
        if isinstance(channels, (list, tuple)):
            safe_values: list[Mapping[str, Any]] = []
            for channel in channels:
                if not isinstance(channel, Mapping) or not isinstance(channel.get("text"), str) or not channel.get("text", "").strip():
                    continue
                safe_values.append({
                    key: channel[key] for key in ("text", "channel", "start_sec", "end_sec", "confidence") if key in channel
                })
            safe_channels = tuple(safe_values)
        return BrowserFetchResult(
            source_link=source_link,
            platform_content_id=content_id,
            published_at=published,
            duration_sec=duration,
            temporary_media_path=media_path,
            media_status=media_status,
            title=title,
            description=description,
            creator=creator,
            text_channels=safe_channels,
        )

    @staticmethod
    def _strip_sensitive(value: Any) -> Any:
        """Recursively remove credential-like mapping keys before adapter calls."""
        sensitive = {"cookie", "token", "generaltoken", "authorization", "access_token", "refresh_token", "session"}
        if isinstance(value, Mapping):
            return {key: BrowserFetcher._strip_sensitive(item) for key, item in value.items() if str(key).lower() not in sensitive}
        if isinstance(value, list):
            return [BrowserFetcher._strip_sensitive(item) for item in value]
        if isinstance(value, tuple):
            return tuple(BrowserFetcher._strip_sensitive(item) for item in value)
        return value

    @staticmethod
    def _safe_target(target: Mapping[str, Any]) -> dict[str, Any]:
        """Pass only routing fields to adapters; never pass credential-like data."""
        allowed = {"source_link", "link", "platform", "platform_content_id", "target_id", "nickname"}
        return BrowserFetcher._strip_sensitive({key: value for key, value in target.items() if key in allowed})

    @staticmethod
    def _safe_run(run: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"run_id", "target_id", "run_type", "input_cursor"}
        return BrowserFetcher._strip_sensitive({key: value for key, value in run.items() if key in allowed})


def get_session_health(fetcher: BrowserFetcherProtocol) -> BrowserSessionHealth:
    """Service-function API for schedulers that need a health probe."""
    return fetcher.session_health()


__all__ = [
    "BrowserAdapter",
    "BrowserSessionHealthProbe",
    "BrowserFetcher",
    "BrowserSessionAccessError",
    "BrowserFetcherPort",
    "BrowserFetcherProtocol",
    "BrowserFetchResult",
    "BrowserSessionHealth",
    "BrowserSessionStatus",
    "get_session_health",
]
