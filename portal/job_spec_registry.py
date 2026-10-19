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
    "registered_at", "expires_at", "fee_mode", "fee_schedule_ref",
    "fee_schedule_version", "fee_schedule_hash", "fee_provider_ref",
    "fee_calculator_ref", "time_in_force", "max_attempts", "expire_at",
    "partial_fill_params", "market_rule_ref", "market_rule_version",
    "calendar_ref", "calendar_version",
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
    fee_mode: str = "PER_FILL"
    fee_schedule_ref: Optional[str] = None
    fee_schedule_version: Optional[str] = None
    fee_schedule_hash: Optional[str] = None
    fee_provider_ref: Optional[str] = None
    fee_calculator_ref: Optional[str] = None
    time_in_force: str = "DAY"
    max_attempts: Optional[int] = None
    expire_at: Optional[str] = None
    partial_fill_params: Optional[Mapping[str, Any]] = None
    market_rule_ref: Optional[str] = None
    market_rule_version: Optional[str] = None
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
                    self._validate(descriptor, allow_expired=True)
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
            previous = self._descriptors.get(descriptor.source_ref)
            previous_status = self.catalog_status
            previous_error = self.catalog_error
            self._descriptors[descriptor.source_ref] = descriptor.as_dict()
            self.catalog_status = "ok"
            self.catalog_error = None
            try:
                self._save()
            except Exception:
                if previous is None:
                    self._descriptors.pop(descriptor.source_ref, None)
                else:
                    self._descriptors[descriptor.source_ref] = previous
                self.catalog_status = previous_status
                self.catalog_error = previous_error
                raise
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
                "SOURCE_UNAVAILABLE", "CALENDAR_UNAVAILABLE", "CALENDAR_MISMATCH",
                "POLICY_MISMATCH", "INVALID_TIF_CONFIG", "PARTIAL_FILL_UNSUPPORTED",
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
    def _validate(raw: Mapping[str, Any], *, allow_expired: bool = False) -> None:
        for key in _DESCRIPTOR_KEYS:
            if key not in raw or raw[key] in (None, ""):
                if key not in ("expires_at", "calendar_ref", "calendar_version", "fee_schedule_ref", "fee_schedule_version", "fee_schedule_hash", "fee_provider_ref", "fee_calculator_ref", "market_rule_ref", "market_rule_version", "partial_fill_params", "expire_at", "max_attempts"):
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
        if str(raw.get("fee_mode", "PER_FILL")).upper() not in {"PER_FILL", "PER_ORDER"}:
            raise ValueError("INVALID_DESCRIPTOR: invalid fee_mode")
        if str(raw.get("time_in_force", "DAY")).upper() not in {"DAY", "GTC"}:
            raise ValueError("INVALID_DESCRIPTOR: invalid time_in_force")
        for key in ("fee_provider_ref", "fee_calculator_ref", "market_rule_ref"):
            value = raw.get(key)
            if value is not None and not _REF_PATTERN.fullmatch(str(value)):
                raise ValueError("REF_NOT_OPAQUE: invalid descriptor reference")
        if raw.get("fee_schedule_hash") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(raw.get("fee_schedule_hash"))):
            raise ValueError("INVALID_DESCRIPTOR: invalid fee_schedule_hash")
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
                if key == "expires_at" and not allow_expired and parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    raise ValueError("INVALID_DESCRIPTOR: expires_at is in the past")
        encoded = json.dumps(raw, ensure_ascii=False).lower()
        if any(word in encoded for word in _SECRET_WORDS):
            raise ValueError("INVALID_DESCRIPTOR: forbidden secret field")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
