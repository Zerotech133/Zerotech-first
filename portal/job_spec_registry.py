# -*- coding: utf-8 -*-
"""Persistent, non-executable descriptors for minute execution sources."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import re
import tempfile
import threading
from typing import Any, Dict, Mapping, Optional

_DESCRIPTOR_KEYS = (
    "source_ref", "source_version", "source_kind", "market", "timezone",
    "timeframe", "store_config_ref", "intent_batch_ref", "provider_policy_ref",
    "calendar_ref", "calendar_version", "registered_at", "expires_at",
)
_SECRET_WORDS = ("password", "secret", "token", "key", "dsn", "connection")
_REF_PATTERN = re.compile(r"(?:runtime|registry):[A-Za-z0-9_-]{1,64}(?::[A-Za-z0-9_-]{1,64})*")
_CALENDAR_VERSION_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class JobSpecDescriptor:
    source_ref: str
    source_version: str
    source_kind: str
    market: str
    timezone: str
    timeframe: str
    store_config_ref: str
    intent_batch_ref: str
    provider_policy_ref: str
    registered_at: str
    expires_at: Optional[str] = None
    calendar_ref: Optional[str] = None
    calendar_version: Optional[str] = None
    availability: str = "available"
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JobSpecRegistry:
    """Persist descriptors only; runtime objects remain process-local."""

    def __init__(self, catalog_path: str) -> None:
        self.catalog_path = os.path.abspath(catalog_path)
        self._descriptors: Dict[str, Dict[str, Any]] = {}
        self.catalog_status: str = "missing"
        self.catalog_error: Optional[str] = None
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        """Load catalog and retain an explicit degraded state on corruption."""
        with self._lock:
            if not os.path.exists(self.catalog_path):
                self._descriptors = {}
                self.catalog_status = "missing"
                self.catalog_error = None
                return
            try:
                with open(self.catalog_path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, dict):
                    raise ValueError("catalog root must be an object")
                for source_ref, descriptor in payload.items():
                    if not isinstance(descriptor, dict):
                        raise ValueError("catalog descriptor must be an object")
                    self._validate(descriptor)
                    if str(source_ref) != str(descriptor.get("source_ref")):
                        raise ValueError("catalog source_ref key mismatch")
                self._descriptors = payload
                self.catalog_status = "ok"
                self.catalog_error = None
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._descriptors = {}
                self.catalog_status = "invalid"
                self.catalog_error = str(exc)[:300]

    def register(self, descriptor: JobSpecDescriptor) -> JobSpecDescriptor:
        self._validate(descriptor.as_dict())
        with self._lock:
            self._descriptors[descriptor.source_ref] = descriptor.as_dict()
            self.catalog_status = "ok"
            self.catalog_error = None
            self._save()
        return descriptor

    def get(self, source_ref: str) -> Optional[JobSpecDescriptor]:
        raw = self._descriptors.get(str(source_ref))
        if not isinstance(raw, dict):
            return None
        values = {key: raw.get(key) for key in _DESCRIPTOR_KEYS}
        values.update({"availability": raw.get("availability", "available"), "error": raw.get("error")})
        return JobSpecDescriptor(**values)

    def mark_unavailable(self, source_ref: str, error: str) -> None:
        """Mark a source unavailable with a stable, non-sensitive error code."""
        with self._lock:
            raw = self._descriptors.get(str(source_ref))
            if raw is None:
                return
            raw["availability"] = "source_unavailable"
            requested = str(error or "SOURCE_UNAVAILABLE")
            raw["error"] = requested if requested in {
                "SOURCE_UNAVAILABLE", "CALENDAR_UNAVAILABLE", "CALENDAR_MISMATCH"
            } else "SOURCE_UNAVAILABLE"
            self._save()

    def mark_available(self, source_ref: str) -> None:
        """Clear a prior availability error after successful rehydration."""
        with self._lock:
            raw = self._descriptors.get(str(source_ref))
            if raw is None:
                return
            raw["availability"] = "available"
            raw["error"] = None
            self._save()

    def descriptors(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._descriptors.items()}

    def is_expired(self, source_ref: str, now: Optional[datetime] = None) -> bool:
        """Return whether a descriptor has passed its optional expiry timestamp."""
        descriptor = self.get(source_ref)
        if descriptor is None or not descriptor.expires_at:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            current = current.replace(tzinfo=timezone.utc)
        expires = datetime.fromisoformat(str(descriptor.expires_at).replace("Z", "+00:00"))
        if expires.tzinfo is None or expires.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return current.astimezone(timezone.utc) >= expires.astimezone(timezone.utc)

    def _save(self) -> None:
        parent = os.path.dirname(self.catalog_path)
        os.makedirs(parent, exist_ok=True)
        fd, temp = tempfile.mkstemp(
            prefix=os.path.basename(self.catalog_path) + ".",
            suffix=".tmp",
            dir=parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._descriptors, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.catalog_path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def _validate(raw: Mapping[str, Any]) -> None:
        for key in _DESCRIPTOR_KEYS:
            if key not in raw or raw[key] in (None, ""):
                if key not in ("expires_at", "calendar_ref", "calendar_version"):
                    raise ValueError(f"INVALID_DESCRIPTOR: missing field {key}")
        calendar_ref = raw.get("calendar_ref")
        calendar_version = raw.get("calendar_version")
        if (calendar_ref is None) != (calendar_version is None):
            raise ValueError("INVALID_DESCRIPTOR: calendar_ref/calendar_version must be provided together")
        if calendar_ref is not None:
            if not _REF_PATTERN.fullmatch(str(calendar_ref)):
                raise ValueError("REF_NOT_OPAQUE: invalid calendar_ref")
            if not _CALENDAR_VERSION_PATTERN.fullmatch(str(calendar_version)):
                raise ValueError("INVALID_DESCRIPTOR: invalid calendar_version")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(raw.get("source_ref", ""))):
            raise ValueError("INVALID_DESCRIPTOR: invalid source_ref")
        for key in ("store_config_ref", "intent_batch_ref", "provider_policy_ref"):
            value = str(raw.get(key, ""))
            if not _REF_PATTERN.fullmatch(value):
                raise ValueError("REF_NOT_OPAQUE: invalid descriptor reference")
        for key in ("registered_at", "expires_at"):
            value = raw.get(key)
            if value:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError(f"INVALID_DESCRIPTOR: {key} must be timezone-aware")
                if key == "expires_at" and parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    raise ValueError("INVALID_DESCRIPTOR: expires_at is in the past")
        encoded = json.dumps(raw, ensure_ascii=False).lower()
        if any(word in encoded for word in _SECRET_WORDS):
            raise ValueError("INVALID_DESCRIPTOR: forbidden secret field")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
