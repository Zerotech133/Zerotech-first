"""Secure mapping from browser fetch results to collector DTOs.

The browser boundary and collector boundary intentionally use different DTO
contracts.  ``BrowserCollectorMapper`` is the narrow bridge between them: it
requires an authenticated browser session, calls the injected fetcher, and
projects only collector business fields.  Browser state, credentials,
headers, temporary paths, and downloader control values are never read or
returned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

from collectors.ports import (
    CollectionAccessError,
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
from knowledge.browser_fetcher import (
    BrowserFetchResult,
    BrowserFetcherProtocol,
    BrowserSessionAccessError,
    BrowserSessionHealth,
    BrowserSessionStatus,
)


class BrowserCollectorMapper:
    """Adapt an authenticated :class:`BrowserFetcherProtocol` to a collector.

    This mapper is deliberately not a browser implementation.  It accepts only
    an already-injected fetcher and an exact caller-supplied HTTPS link.  Session
    health is checked immediately before every fetch, and non-authenticated
    states are rejected before the browser adapter is called.
    """

    _MEDIA_STATUS_MAP: dict[str, MediaAvailability] = {
        "available": MediaAvailability.AVAILABLE,
        "ready": MediaAvailability.AVAILABLE,
        "unavailable": MediaAvailability.UNAVAILABLE,
        "processing": MediaAvailability.PROCESSING,
        "expired": MediaAvailability.EXPIRED,
        "error": MediaAvailability.ERROR,
        # ``accepted`` is a res-downloader queue acknowledgement, not a
        # downloaded media artifact.  It must never become AVAILABLE.
        "accepted": MediaAvailability.UNAVAILABLE,
        "queued": MediaAvailability.UNAVAILABLE,
        "downloaded": MediaAvailability.UNAVAILABLE,
    }

    def __init__(self, fetcher: BrowserFetcherProtocol) -> None:
        if fetcher is None:
            raise ValueError("fetcher is required")
        self._fetcher = fetcher

    def session_health(self) -> SessionHealth:
        """Return safe collector health without retaining browser details."""
        checked_at = datetime.now(timezone.utc)
        try:
            raw = self._fetcher.session_health()
        except Exception:
            return SessionHealth(checked_at=checked_at, reason="HEALTH_PROBE_FAILED")
        return self._normalize_health(raw, checked_at)

    def fetch_video(self, source_link: str, cursor: str | None = None) -> VideoPage:
        """Fetch and map one browser result into a safe :class:`VideoPage`.

        The fetcher is called only after an authenticated health check.  A page
        or list response is accepted for compatibility with browser workers;
        the first business record is mapped and pagination metadata is copied
        only from an allow-listed page mapping.
        """
        # VideoMetadata performs the canonical HTTPS/private-host/credential
        # query validation before touching the browser adapter.
        VideoMetadata(source_link=source_link)
        self._ensure_access()
        try:
            raw = self._fetcher.fetch({"source_link": source_link}, cursor, {})
        except BrowserSessionAccessError as exc:
            raise CollectionAccessError(exc.code, str(exc)) from exc
        except CollectionAccessError:
            raise
        except Exception as exc:
            raise RuntimeError("browser fetch failed") from exc

        record: Any = raw
        next_cursor: str | None = None
        done = True
        if isinstance(raw, Mapping) and ("items" in raw or "data" in raw):
            values = raw.get("items", raw.get("data", ()))
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                record = values[0] if values else {}
            else:
                record = {}
            value = raw.get("next_cursor", raw.get("cursor"))
            next_cursor = str(value) if value is not None else None
            if "done" in raw:
                done = bool(raw.get("done"))
            elif "has_more" in raw:
                done = not bool(raw.get("has_more"))
            else:
                done = next_cursor is None
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            record = raw[0] if raw else {}
        elif isinstance(raw, BrowserFetchResult):
            record = self._browser_result_mapping(raw)
        elif not isinstance(raw, Mapping):
            record = {}

        metadata = self._map_metadata(record, source_link)
        segments = self._map_segments(record)
        # A browser record cannot override the explicitly requested link.
        return VideoPage(metadata=metadata, text_segments=segments, next_cursor=next_cursor, done=done)

    def resolve_creator(self, request: CreatorResolveRequest) -> CreatorResolveResult:
        """Resolve creator data through the same authenticated browser gate."""
        page = self.fetch_video(request.source_link)
        creator = page.metadata.creator
        if creator is None:
            return CreatorResolveResult(None, False, request.source_link, "CREATOR_NOT_FOUND")
        return CreatorResolveResult(creator, True, request.source_link, None)

    def __call__(self, target: Mapping[str, Any], cursor: str | None, run: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Expose creator-worker ``PageFetcher`` compatibility."""
        del run
        source_link = target.get("source_link") or target.get("link")
        if not isinstance(source_link, str):
            raise ValueError("target source_link is required")
        return self.fetch_video(source_link, cursor).as_dict()

    def _ensure_access(self) -> None:
        health = self.session_health()
        if health.status is SessionStatus.AUTHENTICATED:
            return
        if health.status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
            raise CollectionAccessError("AUTHORIZATION_REQUIRED", "browser session requires authorization")
        if health.status is SessionStatus.BLOCKED:
            raise CollectionAccessError("SESSION_BLOCKED", "browser session is blocked")
        raise CollectionAccessError("SESSION_UNKNOWN", "browser session health is unknown")

    @staticmethod
    def _normalize_health(raw: Any, checked_at: datetime) -> SessionHealth:
        if isinstance(raw, SessionHealth):
            return raw
        status: Any = raw
        reason: str | None = None
        if isinstance(raw, BrowserSessionHealth):
            status = raw.status
        elif isinstance(raw, Mapping):
            status = raw.get("status", BrowserSessionStatus.UNKNOWN)
            value = raw.get("reason")
            reason = value.strip()[:80] if isinstance(value, str) and value.strip() else None
        elif isinstance(raw, BrowserSessionStatus):
            status = raw
        try:
            value = status.value if isinstance(status, (BrowserSessionStatus, SessionStatus)) else str(status)
            normalized = SessionStatus(value)
        except (TypeError, ValueError):
            normalized = SessionStatus.UNKNOWN
        return SessionHealth(status=normalized, checked_at=checked_at, reason=reason)

    @staticmethod
    def _browser_result_mapping(result: BrowserFetchResult) -> Mapping[str, Any]:
        """Project a browser result without exposing its temporary media path."""
        return {
            "source_link": result.source_link,
            "platform_content_id": result.platform_content_id,
            "published_at": result.published_at,
            "duration_sec": result.duration_sec,
            "media_status": result.media_status,
            "title": result.title,
            "description": result.description,
            "creator": result.creator,
            "text_channels": result.text_channels,
        }

    @classmethod
    def _map_metadata(cls, raw: Any, source_link: str) -> VideoMetadata:
        values: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        if isinstance(values.get("metadata"), Mapping):
            values = values["metadata"]
        creator = cls._map_creator(values.get("creator"), values.get("platform"))
        content_id = values.get("content_id", values.get("platform_content_id"))
        duration = values.get("duration_sec", values.get("duration"))
        return VideoMetadata(
            source_link=source_link,
            platform=cls._string(values.get("platform")),
            content_id=cls._string(content_id),
            title=cls._string(values.get("title")),
            description=cls._string(values.get("description")),
            published_at=cls._string(values.get("published_at")),
            duration_sec=cls._duration(duration),
            creator=creator,
            media_status=cls._media_status(values.get("media_status")),
        )

    @classmethod
    def _map_creator(cls, raw: Any, platform: Any = None) -> CreatorRef | None:
        if not isinstance(raw, Mapping):
            return None
        creator_id = cls._string(raw.get("creator_id", raw.get("author_id")))
        nickname = cls._string(raw.get("nickname", raw.get("author_name")))
        if not creator_id and not nickname:
            return None
        return CreatorRef(
            platform=cls._string(raw.get("platform")) or cls._string(platform) or "unknown",
            creator_id=creator_id,
            nickname=nickname,
            profile_url=cls._safe_url(raw.get("profile_url")),
            confidence=raw.get("confidence"),
        )

    @classmethod
    def _map_segments(cls, raw: Any) -> tuple[TextSegment, ...]:
        if not isinstance(raw, Mapping):
            return ()
        values: Any = raw.get("text_channels", raw.get("text_segments", raw.get("segments", ())))
        if isinstance(values, Mapping):
            expanded: list[Mapping[str, Any]] = []
            for channel in TextChannel:
                candidates = values.get(channel.value, ())
                if isinstance(candidates, Mapping):
                    candidates = (candidates,)
                if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
                    expanded.extend({**item, "channel": item.get("channel", channel.value)} for item in candidates if isinstance(item, Mapping))
            values = expanded
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return ()
        result: list[TextSegment] = []
        for item in values:
            if not isinstance(item, Mapping):
                continue
            text = cls._string(item.get("text"))
            if not text:
                continue
            try:
                result.append(TextSegment(text=text, channel=item.get("channel", TextChannel.MANUAL), start_sec=item.get("start_sec"), end_sec=item.get("end_sec"), confidence=item.get("confidence")))
            except (TypeError, ValueError):
                continue
        return tuple(result)

    @classmethod
    def _media_status(cls, value: Any) -> MediaAvailability:
        key = value.value if isinstance(value, MediaAvailability) else str(value or "unavailable").strip().lower()
        return cls._MEDIA_STATUS_MAP.get(key, MediaAvailability.UNAVAILABLE)

    @staticmethod
    def _duration(value: Any) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return str(value)

    @staticmethod
    def _safe_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            VideoMetadata(source_link=value)
        except ValueError:
            return None
        return value.strip()


# Descriptive aliases for callers using architecture terminology.
BrowserToCollectorMapper = BrowserCollectorMapper
BrowserCollectorAdapter = BrowserCollectorMapper

__all__ = ["BrowserCollectorAdapter", "BrowserCollectorMapper", "BrowserToCollectorMapper"]
