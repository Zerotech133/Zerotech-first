"""管理员写请求的 Idempotency-Key 规范化 + 请求指纹（B1-4）。

指纹稳定性保证：策略 ID 顺序/重复、versions 键序、reason 首尾空白、valid_until 时区
表示不同但语义相同 → 同一指纹。相同 key + 相同指纹 → 幂等复放；相同 key + 不同指纹 → 409。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime

_MIN_KEY = 8
_MAX_KEY = 128


class IdempotencyKeyError(ValueError):
    code = "entitlements.invalid_idempotency_key"


def normalize_idempotency_key(raw: str | None) -> str:
    """trim 后校验：长度 8~128，且全部为可打印 ASCII（0x20~0x7E）。"""
    if raw is None:
        raise IdempotencyKeyError("missing idempotency key")
    key = raw.strip()
    if not (_MIN_KEY <= len(key) <= _MAX_KEY):
        raise IdempotencyKeyError("idempotency key length must be 8..128")
    if not all(0x20 <= ord(c) <= 0x7E for c in key):
        raise IdempotencyKeyError("idempotency key must be printable ASCII")
    return key


def _canonical_valid_until(value: str) -> str:
    """带时区 ISO 8601 → 规范化 UTC 字符串（同一时刻不同表示归一）。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("valid_until must be timezone-aware")
    return dt.astimezone(UTC).isoformat()


def _normalize_body(body: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = dict(body)

    if "reason" in out and isinstance(out["reason"], str):
        out["reason"] = out["reason"].strip()

    if "strategy_ids" in out and isinstance(out["strategy_ids"], list):
        out["strategy_ids"] = sorted({str(x) for x in out["strategy_ids"]})

    if "versions" in out and isinstance(out["versions"], Mapping):
        out["versions"] = {str(k): out["versions"][k] for k in out["versions"]}

    if out.get("mode") == "permanent":
        out.pop("valid_until", None)
    elif "valid_until" in out and isinstance(out["valid_until"], str):
        out["valid_until"] = _canonical_valid_until(out["valid_until"])

    return out


def request_fingerprint(
    *,
    method: str,
    route: str,
    action: str,
    target_user_id: str,
    body: Mapping[str, object],
) -> str:
    """稳定 JSON（sort_keys）→ SHA-256 hex。route 用模板（含 {user_id}），非实际 path。"""
    canonical = {
        "method": method.upper(),
        "route": route,
        "action": action,
        "target_user_id": target_user_id,
        "body": _normalize_body(body),
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
