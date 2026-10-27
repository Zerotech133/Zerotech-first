"""Safe, platform-neutral collection ports and allow-listed DTOs.

The collector boundary separates workers from browser/platform details.  DTOs
contain business metadata only; credentials, browser headers, cookies, tokens,
and storage state are rejected or silently omitted and never serialized.
Media is optional: metadata and text remain useful when a media URL/file is
unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, urlparse
import ipaddress


class SessionStatus(str, Enum):
    """Coarse session state suitable for scheduling decisions."""

    AUTHENTICATED = "authenticated"
    LOGIN_REQUIRED = "login_required"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class MediaAvailability(str, Enum):
    """Media availability, where unavailable is a supported degraded mode."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"
    PROCESSING = "processing"
    ERROR = "error"


class TextChannel(str, Enum):
    """Origin channel for an extracted text segment."""

    ASR = "asr"
    OCR = "ocr"
    SUBTITLE = "subtitle"
    DESCRIPTION = "description"
    COMMENT = "comment"
    MANUAL = "manual"


@dataclass(frozen=True)
class SessionHealth:
    """Safe session probe result; no raw adapter response is retained."""

    status: SessionStatus = SessionStatus.UNKNOWN
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SessionStatus):
            try:
                status = SessionStatus(str(self.status))
            except ValueError:
                status = SessionStatus.UNKNOWN
            object.__setattr__(self, "status", status)
        if self.checked_at.tzinfo is None:
            object.__setattr__(
                self, "checked_at", self.checked_at.replace(tzinfo=timezone.utc)
            )

    def as_dict(self) -> dict[str, Any]:
        """Serialize only safe health fields."""
        return {
            "status": self.status.value,
            "checked_at": self.checked_at.astimezone(timezone.utc).isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CreatorRef:
    """Platform creator identity with optional display metadata."""

    platform: str
    creator_id: str | None = None
    nickname: str | None = None
    profile_url: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        platform = self.platform.strip()
        if not platform:
            raise ValueError("platform is required")
        object.__setattr__(self, "platform", platform)
        if self.confidence is not None:
            value = float(self.confidence)
            object.__setattr__(
                self,
                "confidence",
                min(1.0, max(0.0, value)) if math.isfinite(value) else None,
            )

    def as_dict(self) -> dict[str, Any]:
        """Return allow-listed creator fields."""
        return {
            "platform": self.platform,
            "creator_id": self.creator_id,
            "nickname": self.nickname,
            "profile_url": self.profile_url,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class CreatorResolveRequest:
    """Input for resolving a creator from a source link or stable ID."""

    source_link: str
    platform: str | None = None
    creator_id: str | None = None

    def __post_init__(self) -> None:
        _validate_source_link(self.source_link)


@dataclass(frozen=True)
class CreatorResolveResult:
    """Creator resolution result; unresolved creators are valid degraded output."""

    creator: CreatorRef | None
    resolved: bool = False
    source_link: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return safe creator resolution fields."""
        return {
            "creator": self.creator.as_dict() if self.creator else None,
            "resolved": self.resolved,
            "source_link": self.source_link,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class VideoMetadata:
    """Allow-listed metadata for a video/page, independent of media retrieval."""

    source_link: str
    platform: str | None = None
    content_id: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    creator: CreatorRef | None = None
    media_status: MediaAvailability = MediaAvailability.UNAVAILABLE

    def __post_init__(self) -> None:
        _validate_source_link(self.source_link)
        if not isinstance(self.media_status, MediaAvailability):
            try:
                status = MediaAvailability(str(self.media_status))
            except ValueError:
                status = MediaAvailability.UNAVAILABLE
            object.__setattr__(self, "media_status", status)
        if self.duration_sec is not None:
            duration = float(self.duration_sec)
            object.__setattr__(
                self,
                "duration_sec",
                duration if math.isfinite(duration) and duration >= 0 else None,
            )

    def as_dict(self) -> dict[str, Any]:
        """Return metadata only; no media paths, URLs, or adapter fields."""
        return {
            "source_link": self.source_link,
            "platform": self.platform,
            "content_id": self.content_id,
            "title": self.title,
            "description": self.description,
            "published_at": self.published_at,
            "duration_sec": self.duration_sec,
            "creator": self.creator.as_dict() if self.creator else None,
            "media_status": self.media_status.value,
        }


@dataclass(frozen=True)
class TextSegment:
    """Timestamped text emitted by an allowed text channel."""

    text: str
    channel: TextChannel
    start_sec: float | None = None
    end_sec: float | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ValueError("text is required")
        object.__setattr__(self, "text", text)
        if not isinstance(self.channel, TextChannel):
            try:
                channel = TextChannel(str(self.channel))
            except ValueError as exc:
                raise ValueError("unsupported text channel") from exc
            object.__setattr__(self, "channel", channel)
        start = _finite_nonnegative(self.start_sec)
        end = _finite_nonnegative(self.end_sec)
        if start is not None and end is not None and end < start:
            raise ValueError("end_sec must be >= start_sec")
        object.__setattr__(self, "start_sec", start)
        object.__setattr__(self, "end_sec", end)
        if self.confidence is not None:
            confidence = float(self.confidence)
            object.__setattr__(
                self,
                "confidence",
                min(1.0, max(0.0, confidence)) if math.isfinite(confidence) else None,
            )

    def as_dict(self) -> dict[str, Any]:
        """Return a safe text segment."""
        return {
            "text": self.text,
            "channel": self.channel.value,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VideoPage:
    """Page-shaped collection output consumed by creator workers."""

    metadata: VideoMetadata
    text_segments: tuple[TextSegment, ...] = ()
    next_cursor: str | None = None
    done: bool = True

    def as_dict(self) -> dict[str, Any]:
        """Return worker-compatible page output with allow-listed item fields."""
        return {
            "items": [
                {
                    "metadata": self.metadata.as_dict(),
                    "text_channels": [
                        segment.as_dict() for segment in self.text_segments
                    ],
                }
            ],
            "next_cursor": self.next_cursor,
            "done": self.done,
        }


class CollectorAdapter(Protocol):
    """Injected platform adapter; implementations own all browser/API details."""

    def session_health(self) -> SessionHealth | SessionStatus | Mapping[str, Any]:
        """Return coarse health; credentials must not appear in the result."""

    def resolve_creator(
        self, request: CreatorResolveRequest
    ) -> CreatorResolveResult | Mapping[str, Any]:
        """Resolve creator identity from an approved source link."""

    def fetch_video(
        self, source_link: str, cursor: str | None = None
    ) -> VideoPage | Mapping[str, Any]:
        """Fetch metadata/text and optionally media status without requiring media."""


class CollectionFailureError(RuntimeError):
    """Raised when adapter collection fails for a non-media reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CollectionAccessError(RuntimeError):
    """Raised when session health does not permit a collection attempt."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CollectorPort(Protocol):
    """Platform-independent port injected into creator/history workers."""

    def session_health(self) -> SessionHealth:
        """Return safe current session health."""

    def resolve_creator(self, request: CreatorResolveRequest) -> CreatorResolveResult:
        """Resolve a creator using a validated request."""

    def fetch_video(self, source_link: str, cursor: str | None = None) -> VideoPage:
        """Return a metadata/text page; media may be unavailable."""


class SafeCollector:
    """Adapter wrapper that projects all responses onto the DTO allow-list."""

    def __init__(self, adapter: CollectorAdapter) -> None:
        if adapter is None:
            raise ValueError("adapter is required")
        self._adapter = adapter

    def session_health(self) -> SessionHealth:
        """Normalize adapter health and hide failures as UNKNOWN."""
        checked_at = datetime.now(timezone.utc)
        try:
            raw = self._adapter.session_health()
        except Exception:
            return SessionHealth(checked_at=checked_at)
        if isinstance(raw, SessionHealth):
            return raw
        if isinstance(raw, SessionStatus):
            return SessionHealth(status=raw, checked_at=checked_at)
        if isinstance(raw, Mapping):
            status = raw.get("status", SessionStatus.UNKNOWN)
            reason = _safe_code(raw.get("reason"))
            try:
                status_value = SessionStatus(str(status))
            except ValueError:
                status_value = SessionStatus.UNKNOWN
            return SessionHealth(
                status=status_value, checked_at=checked_at, reason=reason
            )
        return SessionHealth(checked_at=checked_at)

    def resolve_creator(self, request: CreatorResolveRequest) -> CreatorResolveResult:
        """Resolve and project creator data without adapter metadata."""
        self._ensure_access()
        try:
            raw = self._adapter.resolve_creator(request)
        except Exception as exc:
            return CreatorResolveResult(
                None, False, request.source_link, type(exc).__name__
            )
        if isinstance(raw, CreatorResolveResult):
            return raw
        if not isinstance(raw, Mapping):
            return CreatorResolveResult(
                None, False, request.source_link, "INVALID_RESPONSE"
            )
        creator_raw = raw.get("creator")
        creator = _project_creator(creator_raw, request.platform)
        return CreatorResolveResult(
            creator=creator,
            resolved=bool(raw.get("resolved", creator is not None)),
            source_link=request.source_link,
            error_code=_safe_code(raw.get("error_code")),
        )

    def __call__(
        self,
        target: Mapping[str, Any],
        cursor: str | None,
        run: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Expose creator-worker ``PageFetcher`` shape without leaking run state."""
        health = self.session_health()
        if health.status is not SessionStatus.AUTHENTICATED:
            if health.status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
                raise CollectionAccessError(
                    "AUTHORIZATION_REQUIRED", "collector session requires authorization"
                )
            if health.status is SessionStatus.BLOCKED:
                raise CollectionAccessError(
                    "SESSION_BLOCKED", "collector session is blocked"
                )
            raise CollectionAccessError(
                "SESSION_UNKNOWN", "collector session health is unknown"
            )
        source_link = target.get("source_link") or target.get("link")
        if not isinstance(source_link, str):
            raise ValueError("target source_link is required")
        page = self.fetch_video(source_link, cursor)
        return page.as_dict()

    def _ensure_access(self) -> None:
        """Enforce the session gate before any adapter collection call."""
        status = self.session_health().status
        if status is SessionStatus.AUTHENTICATED:
            return
        if status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
            raise CollectionAccessError(
                "AUTHORIZATION_REQUIRED", "collector session requires authorization"
            )
        if status is SessionStatus.BLOCKED:
            raise CollectionAccessError(
                "SESSION_BLOCKED", "collector session is blocked"
            )
        raise CollectionAccessError(
            "SESSION_UNKNOWN", "collector session health is unknown"
        )

    def fetch_video(self, source_link: str, cursor: str | None = None) -> VideoPage:
        """Fetch safe metadata/text; invalid or unavailable media never blocks output."""
        self._ensure_access()
        _validate_source_link(source_link)
        try:
            raw = self._adapter.fetch_video(source_link, cursor)
        except CollectionAccessError:
            raise
        except Exception as exc:
            if _is_media_unavailable_error(exc):
                metadata = VideoMetadata(
                    source_link=source_link, media_status=MediaAvailability.UNAVAILABLE
                )
                return VideoPage(metadata=metadata, done=True)
            raise CollectionFailureError(
                "COLLECTION_FAILED", "collector fetch failed"
            ) from exc
        if isinstance(raw, VideoPage):
            return raw
        if not isinstance(raw, Mapping):
            raise TypeError("collector adapter fetch_video must return a mapping")
        metadata_raw = raw.get("metadata", raw)
        if not isinstance(metadata_raw, Mapping):
            metadata_raw = {}
        metadata = _project_metadata(metadata_raw, source_link)
        segments_raw = raw.get("text_channels", raw.get("text_segments", []))
        segments = (
            tuple(
                _project_segment(item)
                for item in segments_raw
                if isinstance(item, Mapping)
            )
            if isinstance(segments_raw, Sequence)
            and not isinstance(segments_raw, (str, bytes))
            else ()
        )
        next_cursor = raw.get("next_cursor")
        return VideoPage(
            metadata,
            segments,
            str(next_cursor) if next_cursor is not None else None,
            bool(raw.get("done", True)),
        )


def _is_media_unavailable_error(exc: Exception) -> bool:
    """Recognize only explicit media-unavailable adapter failures for degradation."""
    return bool(getattr(exc, "media_unavailable", False)) or getattr(
        exc, "code", ""
    ) in {"MEDIA_UNAVAILABLE", "MEDIA_EXPIRED"}


def _finite_nonnegative(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _validate_source_link(source_link: str) -> None:
    if not isinstance(source_link, str) or not source_link.strip():
        raise ValueError("source_link is required")
    parsed = urlparse(source_link.strip())
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
        "generaltoken",
        "authorization",
        "access_token",
        "refresh_token",
        "session",
    }
    if {key.lower() for key, _ in parse_qsl(parsed.query)} & forbidden:
        raise ValueError("source_link contains credential-like query parameters")


def _project_creator(raw: Any, platform: str | None = None) -> CreatorRef | None:
    if isinstance(raw, CreatorRef):
        return raw
    if not isinstance(raw, Mapping):
        return None
    value = str(raw.get("platform") or platform or "unknown")
    return CreatorRef(
        value,
        _string(raw.get("creator_id")),
        _string(raw.get("nickname")),
        _safe_url(raw.get("profile_url")),
        raw.get("confidence"),
    )


def _project_metadata(raw: Mapping[str, Any], source_link: str) -> VideoMetadata:
    media = raw.get("media_status", MediaAvailability.UNAVAILABLE)
    creator = _project_creator(raw.get("creator"), raw.get("platform"))
    return VideoMetadata(
        source_link=source_link,
        platform=_string(raw.get("platform")),
        content_id=_string(raw.get("content_id", raw.get("platform_content_id"))),
        title=_string(raw.get("title")),
        description=_string(raw.get("description")),
        published_at=_string(raw.get("published_at")),
        duration_sec=_finite_nonnegative(raw.get("duration_sec")),
        creator=creator,
        media_status=media,
    )


def _project_segment(raw: Mapping[str, Any]) -> TextSegment:
    return TextSegment(
        text=str(raw.get("text") or ""),
        channel=raw.get("channel", TextChannel.MANUAL),
        start_sec=raw.get("start_sec"),
        end_sec=raw.get("end_sec"),
        confidence=raw.get("confidence"),
    )


def _string(value: Any) -> str | None:
    return (
        value.strip()
        if isinstance(value, str) and value.strip()
        else (str(value) if value is not None else None)
    )


def _safe_code(value: Any) -> str | None:
    """Keep adapter error/reason fields bounded and free of raw exception text."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:80]


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        _validate_source_link(value)
    except ValueError:
        return None
    return value.strip()


__all__ = [
    "CollectionAccessError",
    "CollectionFailureError",
    "CollectorAdapter",
    "CollectorPort",
    "CreatorRef",
    "CreatorResolveRequest",
    "CreatorResolveResult",
    "MediaAvailability",
    "SafeCollector",
    "SessionHealth",
    "SessionStatus",
    "TextChannel",
    "TextSegment",
    "VideoMetadata",
    "VideoPage",
]
