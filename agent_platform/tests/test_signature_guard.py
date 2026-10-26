"""P0-3 签名守卫测试（WO-LEGION-P0FIX-20261026）。

覆盖：验签成功/篡改/过期/严格模式/渐进模式，以及 Z2 decide 与 Z4 write_entry
对 `verified` 的如实记录。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

_PLATFORM = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PLATFORM not in sys.path:
    sys.path.insert(0, _PLATFORM)

from engine import experiment_card as E  # noqa: E402
from engine import experience_store as S  # noqa: E402
from engine import signature_guard as G  # noqa: E402


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key().public_bytes_raw().hex()


def _sign(priv, *, kind, item_id, fingerprint, action, ts=None, nonce="nonce-1"):
    ts = time.time() if ts is None else ts
    payload = G.canonical_payload(kind=kind, item_id=item_id, fingerprint=fingerprint,
                                  action=action, ts=ts, nonce=nonce)
    return {"scheme": G.SIG_SCHEME, "sig": priv.sign(payload).hex(),
            "ts": ts, "nonce": nonce}


def _clean_env(monkeypatch):
    monkeypatch.delenv(G.ENV_PUBKEY_HEX, raising=False)
    monkeypatch.delenv(G.ENV_PUBKEY_FILE, raising=False)
    monkeypatch.delenv(G.ENV_STRICT, raising=False)


def test_verify_ok(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    fp = G.fingerprint_of({"a": 1})
    sig = _sign(priv, kind="k", item_id="i", fingerprint=fp, action="a")
    auth = G.check(kind="k", item_id="i", fingerprint=fp, action="a",
                   signed_by="老田", signature=sig)
    assert auth["verified"] is True and auth["reason"] == "ok"


def test_tampered_payload_rejected(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    fp = G.fingerprint_of({"a": 1})
    sig = _sign(priv, kind="k", item_id="i", fingerprint=fp, action="a")
    with pytest.raises(G.SignatureError):
        G.check(kind="k", item_id="OTHER", fingerprint=fp, action="a",
                signed_by="老田", signature=sig)


def test_expired_signature_rejected(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    fp = G.fingerprint_of({"a": 1})
    old = time.time() - 2 * G.DEFAULT_MAX_AGE_S
    sig = _sign(priv, kind="k", item_id="i", fingerprint=fp, action="a", ts=old)
    with pytest.raises(G.SignatureError) as ei:
        G.check(kind="k", item_id="i", fingerprint=fp, action="a",
                signed_by="老田", signature=sig, now=time.time())
    assert "expired" in str(ei.value)


def test_strict_requires_signature(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv(G.ENV_STRICT, "1")
    with pytest.raises(G.SignatureRequired):
        G.check(kind="k", item_id="i", fingerprint="f", action="a",
                signed_by="老田", signature=None)


def test_strict_requires_public_key(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv(G.ENV_STRICT, "1")
    sig = {"scheme": G.SIG_SCHEME, "sig": "00", "ts": time.time(), "nonce": "n"}
    with pytest.raises(G.SignatureRequired):
        G.check(kind="k", item_id="i", fingerprint="f", action="a",
                signed_by="老田", signature=sig)


def test_legacy_mode_records_unverified(monkeypatch):
    _clean_env(monkeypatch)
    auth = G.check(kind="k", item_id="i", fingerprint="f", action="a",
                   signed_by="老田", signature=None)
    assert auth["verified"] is False
    assert "legacy mode" in auth["reason"]


def test_z2_decide_records_verified(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    card = E.create_card(hypothesis="h", candidate_diff={"a": "b"}, verifier=["v"])
    E.record_metrics(card, "baseline", {"x": 1})
    E.record_metrics(card, "candidate", {"x": 2})
    sig = _sign(priv, kind="experiment_card", item_id=card.card_id,
                fingerprint=E.card_fingerprint(card), action="adopt")
    verdict = E.decide(card, adopt=True, signed_by="老田", reason="r", signature=sig)
    assert verdict["signature"]["verified"] is True
    assert card.status == "adopted"


def test_z2_forged_signature_rejected(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    card = E.create_card(hypothesis="h", candidate_diff={"a": "b"}, verifier=["v"])
    E.record_metrics(card, "baseline", {"x": 1})
    E.record_metrics(card, "candidate", {"x": 2})
    sig = _sign(priv, kind="experiment_card", item_id="another-card",
                fingerprint=E.card_fingerprint(card), action="adopt")
    with pytest.raises(G.SignatureError):
        E.decide(card, adopt=True, signed_by="老田", signature=sig)


def test_z4_write_entry_records_verified(tmp_path, monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv(G.ENV_PUBKEY_HEX, pub)
    root = tmp_path / "lib"
    entry_id = "EXP-SUPERVISION-001"
    content = {"domain": "supervision", "cue": "c", "lesson": "l",
               "evidence": "e", "confidence": 0.6}
    sig = _sign(priv, kind="experience_write", item_id=entry_id,
                fingerprint=G.fingerprint_of(content), action="write")
    entry = S.write_entry(**content, signed_by="老田", root=root, signature=sig)
    assert entry.verified is True

    row = json.loads((root / "_index.jsonl").read_text(encoding="utf-8").strip())
    assert row["verified"] is True
    exp_text = (root / "supervision" / "EXPERIENCE.md").read_text(encoding="utf-8")
    assert "验签" in exp_text and "verified" in exp_text


def test_z4_strict_without_signature_rejected(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv(G.ENV_STRICT, "1")
    with pytest.raises(G.SignatureRequired):
        S.write_entry(domain="supervision", cue="c", lesson="l", evidence="e",
                      confidence=0.5, signed_by="老田", root=tmp_path / "lib")
