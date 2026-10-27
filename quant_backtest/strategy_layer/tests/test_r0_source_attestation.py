"""WO-QT-20261020-014：独立来源签名契约的离线对抗测试。"""

import base64
from datetime import datetime, timezone
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from strategy_layer.r0_source_attestation import verify_source_attestation


DAY = "20261016"
ISSUED = "2026-10-17T02:03:04Z"


def snapshot(day="2026-10-16"):
    return (
        json.dumps(
            {
                "market": "CN",
                "code": "002633.SZ",
                "type": "1买",
                "detect_date": day,
                "dt": day,
                "price": 10,
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def credential(key, raw, *, day=DAY, count=1, issued=ISSUED, issuer="source-a"):
    claims = {
        "schema_version": 1,
        "issuer_id": issuer,
        "business_date": day,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": count,
        "issued_at": issued,
    }
    payload = json.dumps(
        claims,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "claims": claims,
        "signature": base64.b64encode(key.sign(payload)).decode("ascii"),
    }


@pytest.fixture
def signed():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    raw = snapshot()
    return key, public, raw, credential(key, raw)


def verify(raw, attestation=None, roots=None, day=DAY, **kwargs):
    return verify_source_attestation(
        day, raw, attestation, trusted_public_keys=roots, **kwargs
    )


def test_external_trust_anchor_binds_full_snapshot_and_claims(signed):
    _, public, raw, document = signed
    result = verify(
        raw,
        document,
        {"source-a": public},
        trusted_as_of=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )
    assert result["status"] == "VERIFIED"
    assert result["identity_status"] == "VERIFIED"
    assert result["issuer_id"] == "source-a"
    assert result["business_date"] == DAY
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["event_count"] == 1
    assert result["issued_at_claim"] == ISSUED
    assert result["historical_single_day_frozen"] is False
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert "READY" not in result.values()


def test_no_external_root_or_missing_credential_is_unverified(signed):
    _, public, raw, document = signed
    for roots, attestation in [
        (None, document),
        ({}, document),
        ({"source-a": public}, None),
    ]:
        result = verify(raw, attestation, roots)
        assert result["status"] == result["identity_status"] == "UNVERIFIED"
        assert result["historical_single_day_frozen"] is False
    self_declared = dict(document, public_key=base64.b64encode(public).decode("ascii"))
    assert verify(raw, self_declared)["status"] == "UNVERIFIED"


def test_self_filled_hash_or_forged_signer_cannot_become_trusted(signed):
    _, public, raw, document = signed
    fake = Ed25519PrivateKey.generate()
    assert (
        verify(raw, credential(fake, raw), {"source-a": public})["status"] == "REJECTED"
    )
    forged = credential(fake, raw)
    forged["public_key"] = base64.b64encode(
        fake.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode("ascii")
    assert verify(raw, forged, {"source-a": public})["status"] == "REJECTED"
    assert verify(raw + b"\n", document, {"source-a": public})["status"] == "REJECTED"
    assert (
        verify(raw, {"claims": document["claims"]}, {"source-a": public})["status"]
        == "REJECTED"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("business_date", "20261015"),
        ("source_sha256", "0" * 64),
        ("event_count", 2),
        ("event_count", True),
        ("issued_at", "2026-10-15T23:59:59Z"),
        ("issued_at", "2026-10-17"),
        ("issuer_id", "source-b"),
        ("schema_version", 2),
    ],
)
def test_signed_wrong_claims_rejected(signed, field, value):
    key, public, raw, _ = signed
    document = credential(key, raw)
    document["claims"][field] = value
    payload = json.dumps(
        document["claims"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    document["signature"] = base64.b64encode(key.sign(payload)).decode("ascii")
    assert verify(raw, document, {"source-a": public})["status"] == "REJECTED"


def test_unsigned_claim_mutation_and_replayed_day_rejected(signed):
    key, public, raw, document = signed
    mutated = {
        "claims": dict(document["claims"], event_count=0),
        "signature": document["signature"],
    }
    assert verify(raw, mutated, {"source-a": public})["status"] == "REJECTED"
    assert (
        verify(raw, credential(key, raw), {"source-a": public}, day="20261017")[
            "status"
        ]
        == "REJECTED"
    )


def test_signed_empty_batch_is_not_historical_freeze(signed):
    key, public, _, _ = signed
    result = verify(b"", credential(key, b"", count=0), {"source-a": public})
    assert result["status"] == "VERIFIED"
    assert result["event_count"] == 0
    assert result["historical_single_day_frozen"] is False
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"


def test_future_signed_timestamp_rejected_against_independent_time(signed):
    _, public, raw, document = signed
    assert (
        verify(
            raw,
            document,
            {"source-a": public},
            trusted_as_of=datetime(2026, 10, 16, tzinfo=timezone.utc),
        )["status"]
        == "REJECTED"
    )


def test_multiday_rows_never_promoted_to_single_day_freeze(signed):
    key, public, raw, _ = signed
    mixed = raw + snapshot("2026-10-15")
    doc = credential(key, mixed, count=2)
    assert verify(mixed, doc, {"source-a": public})["status"] == "REJECTED"


def test_malformed_or_noncanonical_snapshot_rejected(signed):
    key, public, raw, _ = signed
    bad = (
        b'{"detect_date":"2026-10-16","detect_date":"2026-10-16"}\n',
        b'{"detect_date":"2026-10-16"}\n\n',
        b'{"detect_date":"2026-10-16"}\n' + b"\xff",
    )
    for content in bad:
        assert (
            verify(content, credential(key, content), {"source-a": public})["status"]
            == "REJECTED"
        )


def test_bad_signature_encoding_and_unknown_issuer_rejected(signed):
    _, public, raw, document = signed
    assert (
        verify(raw, dict(document, signature="###"), {"source-a": public})["status"]
        == "REJECTED"
    )
    assert verify(raw, document, {"someone-else": public})["status"] == "REJECTED"
    assert verify(raw, document, {"source-a": b"bad"})["status"] == "REJECTED"


def test_bytes_input_does_not_touch_files_or_spawn_jobs(signed, monkeypatch):
    import builtins
    import subprocess
    from pathlib import Path

    _, public, raw, document = signed

    def forbidden(*args, **kwargs):
        raise AssertionError("不得读写文件或启动子进程")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert verify(raw, document, {"source-a": public})["status"] == "VERIFIED"


def test_extra_claims_field_is_rejected_even_if_reshigned(signed):
    key, public, raw, _ = signed
    document = credential(key, raw)
    document["claims"]["extra"] = "pinned"
    payload = json.dumps(
        document["claims"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    document["signature"] = base64.b64encode(key.sign(payload)).decode("ascii")
    result = verify(
        raw,
        document,
        {"source-a": public},
        trusted_as_of=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )
    assert result["status"] == "REJECTED"
