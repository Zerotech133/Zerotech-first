"""WO-QT-20261020-015：合成接收方密钥与纯内存研究简报的离线反例。"""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from strategy_layer.r0_receipt_attestation import verify_receipt_attestation


UTC = timezone.utc
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
BATCH = "20261016"
SOURCE = "a" * 64
RECEIVER = "independent-review-desk"
FIELDS = ("brief_sha256", "batch_id", "source_identity", "receiver_identity",
          "received_at", "message_id")
DOMAIN = b"r0-research-brief-receipt-v1\x00"


def _brief():
    return json.dumps({"scope": "RESEARCH_ONLY_NOT_PRODUCTION",
                       "cutover_verdict": "BLOCK_CUTOVER", "trade_date": BATCH,
                       "source": {"sha256": SOURCE}, "receipt_status": "NOT_VERIFIED"},
                      sort_keys=True).encode("utf-8")


def _public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _receipt(key, brief, **changes):
    payload = {"brief_sha256": hashlib.sha256(brief).hexdigest(), "batch_id": BATCH,
               "source_identity": SOURCE, "receiver_identity": RECEIVER,
               "received_at": "2026-10-20T08:59:00+00:00", "message_id": "unique-message-01"}
    payload.update(changes)
    signed = DOMAIN + json.dumps({field: payload[field] for field in FIELDS},
                                 ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
    return {**payload, "signature": base64.b64encode(key.sign(signed)).decode("ascii")}


def _verify(brief, receipt, key, *, seen=None, **changes):
    arguments = {"trusted_receiver_public_key": _public(key),
                 "expected_batch_id": BATCH, "expected_source_identity": SOURCE,
                 "expected_receiver_identity": RECEIVER, "seen_message_ids": seen,
                 "now": NOW}
    arguments.update(changes)
    return verify_receipt_attestation(brief, receipt, **arguments)


def test_valid_receipt_is_only_attestation_not_production_or_source_upgrade():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    seen = set()
    receipt = _receipt(key, brief)
    before = brief[:], receipt.copy()
    assert _verify(brief, receipt, key, seen=seen) == {"status": "VERIFIED_RECEIPT_ONLY"}
    assert receipt["message_id"] in seen
    assert (brief, receipt) == before
    assert json.loads(brief)["receipt_status"] == "NOT_VERIFIED"


def test_no_independent_root_never_trusts_self_declared_key_or_status():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    receipt["receiver_public_key"] = base64.b64encode(_public(key)).decode()
    receipt["status"] = "VERIFIED"
    assert _verify(brief, receipt, key, trusted_receiver_public_key=None,
                   seen=set())["status"] == "NOT_VERIFIED"
    embedded = json.loads(brief)
    embedded["receiver_public_key"] = receipt["receiver_public_key"]
    untrusted_brief = json.dumps(embedded).encode()
    assert _verify(untrusted_brief, _receipt(key, untrusted_brief), key,
                   trusted_receiver_public_key=None, seen=set())["status"] == "NOT_VERIFIED"
    assert _verify(brief, receipt, key, expected_receiver_identity="",
                   seen=set())["status"] == "NOT_VERIFIED"


def test_impersonation_even_with_self_declared_attacker_key():
    real = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(attacker, brief)
    receipt["receiver_public_key"] = base64.b64encode(_public(attacker)).decode()
    assert _verify(brief, receipt, real, seen=set())["status"] == "NOT_VERIFIED"
    signed = _receipt(real, brief)
    signed["receiver_identity"] = "other-desk"
    assert _verify(brief, signed, real, seen=set())["status"] == "NOT_VERIFIED"


def test_wrong_artifact_and_tampered_signed_fields():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    assert _verify(brief + b" ", receipt, key, seen=set())["status"] == "NOT_VERIFIED"
    for field, value in (("brief_sha256", "b" * 64), ("batch_id", "20261017"),
                         ("source_identity", "b" * 64),
                         ("received_at", "2026-10-20T08:58:00+00:00"),
                         ("message_id", "changed-id")):
        tampered = {**receipt, field: value}
        assert _verify(brief, tampered, key, seen=set())["status"] == "NOT_VERIFIED"


def test_wrong_batch_source_and_receiver_from_caller_or_brief():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    for changes in ({"expected_batch_id": "20261017"},
                    {"expected_source_identity": "b" * 64},
                    {"expected_receiver_identity": "other-desk"}):
        assert _verify(brief, receipt, key, seen=set(), **changes)["status"] == "NOT_VERIFIED"
    for change in ({"trade_date": "20261017"}, {"source": {"sha256": "b" * 64}}):
        modified = {**json.loads(brief), **change}
        changed_brief = json.dumps(modified, sort_keys=True).encode()
        assert _verify(changed_brief, _receipt(key, changed_brief), key,
                       seen=set())["status"] == "NOT_VERIFIED"


def test_future_or_naive_receipt_time_is_rejected_even_if_signed():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    for received_at in ((NOW + timedelta(seconds=1)).isoformat(), "2026-10-20T08:59:00",
                        "invalid-time"):
        assert _verify(brief, _receipt(key, brief, received_at=received_at), key,
                       seen=set())["status"] == "NOT_VERIFIED"


def test_replay_rejected_and_failed_receipt_does_not_consume_message_id():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    seen = set()
    assert _verify(brief + b" ", receipt, key, seen=seen)["status"] == "NOT_VERIFIED"
    assert not seen
    assert _verify(brief, receipt, key, seen=seen)["status"] == "VERIFIED_RECEIPT_ONLY"
    assert _verify(brief, receipt, key, seen=seen)["status"] == "NOT_VERIFIED"
    assert _verify(brief, _receipt(key, brief, message_id=""), key,
                   seen=set())["status"] == "NOT_VERIFIED"
    assert _verify(brief, receipt, key, seen=None)["status"] == "NOT_VERIFIED"


def test_malformed_inputs_fail_closed():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    receipt = _receipt(key, brief)
    assert _verify(brief, {**receipt, "signature": "not-base64!!!"}, key,
                   seen=set())["status"] == "NOT_VERIFIED"
    assert _verify(b"not-json", receipt, key, seen=set())["status"] == "NOT_VERIFIED"
    assert _verify(brief, receipt, key, trusted_receiver_public_key=b"invalid",
                   seen=set())["status"] == "NOT_VERIFIED"

def test_timezone_offset_cannot_launder_pre_business_day_receipt():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    # +14:00 local date enters business day, but real UTC time is before it
    receipt = _receipt(key, brief, received_at='2026-10-16T00:30:00+14:00')
    assert _verify(brief, receipt, key, seen=set())['status'] == 'NOT_VERIFIED'


def test_westward_offset_with_valid_utc_time_is_accepted():
    key = Ed25519PrivateKey.generate()
    brief = _brief()
    # -12:00 local date looks pre-day, real UTC time is inside the business day
    receipt = _receipt(key, brief, received_at='2026-10-15T22:00:00-12:00')
    assert _verify(brief, receipt, key, seen=set()) == {'status': 'VERIFIED_RECEIPT_ONLY'}


def test_duplicate_key_or_nan_brief_rejected_by_strict_parse():
    key = Ed25519PrivateKey.generate()
    raw = _brief()
    dup = raw.replace(b'"receipt_status": "NOT_VERIFIED"',
                      b'"receipt_status": "NOT_VERIFIED", "receipt_status": "NOT_VERIFIED"}')
    assert dup != raw
    assert _verify(dup, _receipt(key, dup), key, seen=set())['status'] == 'NOT_VERIFIED'
    nan_brief = raw.replace(b'"receipt_status": "NOT_VERIFIED"', b'"receipt_status": NaN}')
    assert _verify(nan_brief, _receipt(key, nan_brief), key, seen=set())['status'] == 'NOT_VERIFIED'
