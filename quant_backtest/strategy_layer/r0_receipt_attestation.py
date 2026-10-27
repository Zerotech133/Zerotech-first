"""WO-QT-20261020-015：只核验离线研究简报的独立接收证明，不发送或签发回执。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import date, datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("简报含重复 JSON 字段")
        obj[key] = value
    return obj


def _invalid_constant(_value):
    raise ValueError("简报含非法 JSON 数值")


_FIELDS = (
    "brief_sha256",
    "batch_id",
    "source_identity",
    "receiver_identity",
    "received_at",
    "message_id",
)
_RECEIPT_FIELDS = frozenset((*_FIELDS, "signature"))
_DOMAIN = b"r0-research-brief-receipt-v1\x00"
_NOT_VERIFIED = {"status": "NOT_VERIFIED"}


def verify_receipt_attestation(
    brief_bytes: bytes,
    receipt: dict,
    *,
    trusted_receiver_public_key: bytes | None,
    expected_batch_id: str,
    expected_source_identity: str,
    expected_receiver_identity: str,
    seen_message_ids: set[str] | None,
    now: datetime,
) -> dict:
    """以调用方独立给定的 Ed25519 公钥和身份核验；成功也仅证明此回执。

    brief_bytes 是待核验研究产物的原始字节；调用方须持久维护接收方作用域的
    seen_message_ids，且在并发调用之间自行串行化，否则无法保证跨进程防重放。
    """
    if (
        not isinstance(trusted_receiver_public_key, bytes)
        or len(trusted_receiver_public_key) != 32
        or not isinstance(brief_bytes, bytes)
        or not isinstance(receipt, dict)
        or not isinstance(seen_message_ids, set)
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or not all(
            isinstance(value, str) and value
            for value in (
                expected_batch_id,
                expected_source_identity,
                expected_receiver_identity,
            )
        )
    ):
        return _NOT_VERIFIED.copy()

    try:
        brief = json.loads(
            brief_bytes,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if (
            not isinstance(brief, dict)
            or set(receipt) != _RECEIPT_FIELDS
            or brief.get("scope") != "RESEARCH_ONLY_NOT_PRODUCTION"
            or brief.get("cutover_verdict") != "BLOCK_CUTOVER"
            or brief.get("trade_date") != expected_batch_id
            or not isinstance(brief.get("source"), dict)
            or brief["source"].get("sha256") != expected_source_identity
            or any(
                not isinstance(receipt.get(field), str) or not receipt[field]
                for field in _FIELDS
            )
            or receipt["brief_sha256"] != hashlib.sha256(brief_bytes).hexdigest()
            or receipt["batch_id"] != expected_batch_id
            or receipt["source_identity"] != expected_source_identity
            or receipt["receiver_identity"] != expected_receiver_identity
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["brief_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", expected_source_identity)
            or receipt["message_id"] in seen_message_ids
        ):
            return _NOT_VERIFIED.copy()

        if not re.fullmatch(r"[0-9]{8}", expected_batch_id):
            return _NOT_VERIFIED.copy()
        business_date = date.fromisoformat(
            f"{expected_batch_id[:4]}-{expected_batch_id[4:6]}-{expected_batch_id[6:]}"
        )
        received_at = datetime.fromisoformat(receipt["received_at"])
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            return _NOT_VERIFIED.copy()
        received_utc = received_at.astimezone(
            timezone.utc
        )  # 先归一 UTC，防偏移洗白/误拒
        if received_utc.date() < business_date or received_utc > now:
            return _NOT_VERIFIED.copy()

        message = _DOMAIN + json.dumps(
            {field: receipt[field] for field in _FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = base64.b64decode(receipt["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(trusted_receiver_public_key).verify(
            signature, message
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        binascii.Error,
        InvalidSignature,
    ):
        return _NOT_VERIFIED.copy()

    seen_message_ids.add(receipt["message_id"])
    return {"status": "VERIFIED_RECEIPT_ONLY"}
