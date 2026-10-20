"""WO-QT-20261020-014：仅内存验证来源签名，不签发、不证明历史点时冻结。

调用方须通过与源/凭证分离的可信渠道取得 issuer_id -> Ed25519 原始公钥。
凭证签名覆盖固定 JSON 规范化 claims；签发时间是被签名的声明，不是可信时间戳。
即使签名有效，也不证明签发者可信、原始采集真实性、历史可见性或单日冻结。
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_DAY = re.compile(r"[0-9]{8}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_CLAIMS = frozenset(("schema_version", "issuer_id", "business_date",
                     "source_sha256", "event_count", "issued_at"))


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("重复 JSON 字段")
        obj[key] = value
    return obj


def _invalid_constant(_value):
    raise ValueError("非法 JSON 数值")


def verify_source_attestation(
    business_day: str,
    source_bytes: bytes,
    attestation: dict | None,
    *,
    trusted_public_keys: Mapping[str, bytes] | None,
    trusted_as_of: datetime | None = None,
) -> dict:
    """核对调用方提供的同一源字节与独立 Ed25519 凭证；异常输入一律拒绝。

    只接受 bytes 和显式可信公钥映射；绝不读路径、manifest 或密钥文件。
    VERIFIED 只表示签名/声明/本次内存快照一致，不表示 READY 或真实历史冻结。
    """
    result = {
        "status": "REJECTED", "identity_status": "UNVERIFIED",
        "issuer_id": None, "business_date": business_day,
        "source_sha256": None, "event_count": None, "issued_at_claim": None,
        "historical_single_day_frozen": False,
        "real_source_verdict": "BLOCKED_REAL_DATA",
        "reason": "凭证验证失败",
    }
    if not trusted_public_keys or attestation is None:
        result["status"] = "UNVERIFIED"
        result["reason"] = "缺少独立可信公钥或来源凭证"
        return result
    try:
        if not isinstance(business_day, str) or not _DAY.fullmatch(business_day):
            raise ValueError("业务日格式错误")
        day = date.fromisoformat(f"{business_day[:4]}-{business_day[4:6]}-{business_day[6:]}")
        if type(source_bytes) is not bytes:
            raise ValueError("源快照必须为本次读取的原始 bytes")
        if type(attestation) is not dict or set(attestation) != {"claims", "signature"}:
            raise ValueError("凭证仅允许 claims 和 signature")
        claims = attestation["claims"]
        if type(claims) is not dict or set(claims) != _CLAIMS:
            raise ValueError("签名字段不完整或包含未绑定字段")
        issuer = claims["issuer_id"]
        if type(issuer) is not str or not issuer or issuer not in trusted_public_keys:
            raise ValueError("签发身份不在外部可信根中")
        key = trusted_public_keys[issuer]
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("外部可信公钥无效")
        if type(claims["schema_version"]) is not int or claims["schema_version"] != 1:
            raise ValueError("不支持凭证版本")
        if type(claims["business_date"]) is not str or claims["business_date"] != business_day:
            raise ValueError("凭证业务日不一致")
        if type(claims["source_sha256"]) is not str or not _HASH.fullmatch(claims["source_sha256"]):
            raise ValueError("摘要格式错误")
        if type(claims["event_count"]) is not int or claims["event_count"] < 0:
            raise ValueError("事件数无效")
        timestamp = claims["issued_at"]
        if type(timestamp) is not str or not _UTC.fullmatch(timestamp):
            raise ValueError("签发时间须为 UTC 秒级时间")
        issued = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if issued.date() < day:
            raise ValueError("签发时间早于业务日")
        now = trusted_as_of if trusted_as_of is not None else datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("可信核验时间须带时区")
        if issued > now:
            raise ValueError("签发时间晚于核验时间")
        signature_text = attestation["signature"]
        if type(signature_text) is not str:
            raise ValueError("签名编码无效")
        signature = base64.b64decode(signature_text, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != signature_text:
            raise ValueError("签名长度或编码无效")
        payload = json.dumps(claims, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        Ed25519PublicKey.from_public_bytes(key).verify(signature, payload)

        actual = hashlib.sha256(source_bytes).hexdigest()
        if actual != claims["source_sha256"]:
            raise ValueError("源字节摘要不一致")
        text = source_bytes.decode("utf-8")
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        if any(not line.strip() for line in lines):
            raise ValueError("源包含空白行")
        if len(lines) != claims["event_count"]:
            raise ValueError("源事件数不一致")
        expected_date = day.isoformat()
        for line in lines:
            row = json.loads(line, object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
            if type(row) is not dict or row.get("detect_date") != expected_date:
                raise ValueError("源事件不属于凭证业务日")
    except (ValueError, TypeError, UnicodeError, KeyError, binascii.Error, InvalidSignature) as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result

    result.update(status="VERIFIED", identity_status="VERIFIED", issuer_id=issuer,
                  source_sha256=actual, event_count=len(lines), issued_at_claim=timestamp,
                  reason="签名与本次内存快照相符；不证明签发时间权威性或历史冻结")
    return result
