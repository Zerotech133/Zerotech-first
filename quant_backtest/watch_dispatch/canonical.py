"""Strict, offline-only canonical JSON primitives; digests are not credentials."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    """Encode only unambiguous JSON values; reject floats and key collisions."""
    def check(item: Any) -> Any:
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is list:
            return [check(child) for child in item]
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                raise ValueError("JSON_KEY_INVALID")
            return {key: check(child) for key, child in item.items()}
        raise ValueError("JSON_VALUE_INVALID")

    return json.dumps(check(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_canonical(raw: bytes) -> Any:
    """Reject duplicate keys, non-finite numbers and non-canonical encoding."""
    if type(raw) is not bytes:
        raise ValueError("JSON_BYTES_INVALID")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def reject(_: str) -> None:
        raise ValueError("JSON_NONFINITE")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=reject)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON_INVALID") from exc
    if canonical_bytes(value) != raw:
        raise ValueError("JSON_NOT_CANONICAL")
    return value


def digest(value: Any) -> str:
    """Content digest only: never signature, authority or permission."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_text(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC_REQUIRED")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
