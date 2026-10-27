"""WO-QT-20261020-016: independent adversarial receipt/source contract checks."""

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from strategy_layer.r0_receipt_attestation import verify_receipt_attestation
from strategy_layer.r0_source_attestation import verify_source_attestation


DOMAIN = b"r0-research-brief-receipt-v1\x00"
FIELDS = (
    "brief_sha256",
    "batch_id",
    "source_identity",
    "receiver_identity",
    "received_at",
    "message_id",
)
DAY = "20261016"
NOW = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def _public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _brief():
    return json.dumps(
        {
            "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
            "cutover_verdict": "BLOCK_CUTOVER",
            "trade_date": DAY,
            "source": {"sha256": "a" * 64},
        },
        sort_keys=True,
    ).encode()


def _receipt(key, brief, **changes):
    fields = {
        "brief_sha256": hashlib.sha256(brief).hexdigest(),
        "batch_id": DAY,
        "source_identity": "a" * 64,
        "receiver_identity": "research-desk",
        "received_at": "2026-10-20T08:30:00+00:00",
        "message_id": "id-001",
    }
    fields.update(changes)
    message = (
        DOMAIN
        + json.dumps(
            {name: fields[name] for name in FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return {**fields, "signature": base64.b64encode(key.sign(message)).decode()}


def _verify(key, brief, receipt, seen=None):
    return verify_receipt_attestation(
        brief,
        receipt,
        trusted_receiver_public_key=_public(key),
        expected_batch_id=DAY,
        expected_source_identity="a" * 64,
        expected_receiver_identity="research-desk",
        seen_message_ids=set() if seen is None else seen,
        now=NOW,
    )


def test_unsigned_extra_receipt_claim_must_not_be_accepted():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    receipt["production_approved"] = True
    assert _verify(key, brief, receipt)["status"] == "NOT_VERIFIED"


def test_receipt_before_business_day_must_not_be_accepted():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief, received_at="2026-10-15T08:30:00+00:00")
    assert _verify(key, brief, receipt)["status"] == "NOT_VERIFIED"


def test_missing_trust_anchor_cannot_be_replaced_by_embedded_key():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    receipt["public_key"] = base64.b64encode(_public(key)).decode()
    assert (
        verify_receipt_attestation(
            brief,
            receipt,
            trusted_receiver_public_key=None,
            expected_batch_id=DAY,
            expected_source_identity="a" * 64,
            expected_receiver_identity="research-desk",
            seen_message_ids=set(),
            now=NOW,
        )["status"]
        == "NOT_VERIFIED"
    )


def test_source_attestation_does_not_claim_historical_freeze():
    key = Ed25519PrivateKey.generate()
    raw = b'{"detect_date":"2026-10-16"}\n'
    claims = {
        "schema_version": 1,
        "issuer_id": "offline-test",
        "business_date": DAY,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": 1,
        "issued_at": "2026-10-17T00:00:00Z",
    }
    signed = json.dumps(
        claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    credential = {
        "claims": claims,
        "signature": base64.b64encode(key.sign(signed)).decode(),
    }
    result = verify_source_attestation(
        DAY,
        raw,
        credential,
        trusted_public_keys={"offline-test": _public(key)},
        trusted_as_of=NOW,
    )
    assert result["status"] == "VERIFIED"
    assert result["historical_single_day_frozen"] is False
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
