"""Whitelisted DTOs crossing the res-downloader boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit


_ALLOWED_FIELDS = frozenset({"url", "title", "author", "media_id", "duration", "format"})
_SENSITIVE_FIELDS = frozenset(
    {
        "key",
        "decode_key",
        "otherdata",
        "other_data",
        "cookie",
        "authorization",
        "token",
        "password",
    }
)
_QUERY_CREDENTIAL_MARKERS = ("token", "key", "sig", "signature", "auth", "credential", "password", "secret")


@dataclass(frozen=True)
class MediaInfo:
    """Validated media metadata accepted by the adapter.

    Only stable, non-secret metadata is retained. Ordinary URL query parameters
    are preserved for signed media URLs, while credential-like query names are
    rejected at the boundary to avoid accidental secret disclosure.
    """

    url: str
    title: str = ""
    author: str = ""
    media_id: str = ""
    duration: float | None = None
    format: str = "mp4"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        query_names = {name.lower() for name, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        credential_names = {
            name for name in query_names
            if any(marker in name for marker in _QUERY_CREDENTIAL_MARKERS)
        }
        if credential_names:
            raise ValueError(f"credential-like URL query fields are not allowed: {sorted(credential_names)}")
        if parsed.fragment:
            raise ValueError("URL fragments are not allowed")
        if self.duration is not None and self.duration < 0:
            raise ValueError("duration must be non-negative")
        if not self.format.strip() or len(self.format) > 16:
            raise ValueError("format must be a short non-empty string")

    @property
    def safe_url(self) -> str:
        """Return the validated URL, preserving ordinary query parameters."""
        return self.url

    def to_payload(self) -> dict[str, Any]:
        """Build a request payload containing only whitelisted fields."""
        payload: dict[str, Any] = {
            "url": self.safe_url,
            "title": self.title,
            "author": self.author,
            "media_id": self.media_id,
            "format": self.format,
        }
        if self.duration is not None:
            payload["duration"] = self.duration
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MediaInfo":
        """Parse an untrusted mapping, rejecting unknown or sensitive fields."""
        original_keys = {str(k) for k in value}
        if any(key != key.lower() for key in original_keys):
            raise ValueError("media fields must use canonical lowercase names")
        sensitive = original_keys & _SENSITIVE_FIELDS
        if sensitive:
            raise ValueError(f"sensitive media fields are not allowed: {sorted(sensitive)}")
        unknown = original_keys - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"unsupported media fields: {sorted(unknown)}")
        return cls(
            url=str(value.get("url", "")),
            title=str(value.get("title", "")),
            author=str(value.get("author", "")),
            media_id=str(value.get("media_id", "")),
            duration=float(value["duration"]) if value.get("duration") is not None else None,
            format=str(value.get("format", "mp4")),
        )
