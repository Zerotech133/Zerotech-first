"""T3 签名工具三件套测试（WO-LEGION-SIGNTOOLS-20261026）。

覆盖：7 条 payload 的 id/fingerprint/canonical 口径、CLI 落盘、
离线签名器 round-trip（签→验）、人类确认门、--apply 缺签名拒绝。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_PLATFORM), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evolve_sign_payloads as G  # noqa: E402
import sign_approval as SA  # noqa: E402
from engine import signature_guard as SG  # noqa: E402


def test_payloads_are_7_with_canonical_hex():
    payloads = G.build_payloads(now=1761430000.0, nonce_prefix="t")
    assert len(payloads) == 7
    assert [p["item_id"] for p in payloads] == [
        "EXP-SUPERVISION-%03d" % i for i in range(1, 8)]
    first = payloads[0]
    canonical = SG.canonical_payload(
        kind=first["kind"], item_id=first["item_id"],
        fingerprint=first["fingerprint"], action=first["action"],
        ts=first["ts"], nonce=first["nonce"])
    assert canonical.hex() == first["canonical_payload_hex"]
    # fingerprint 必须与 Z4 入库口径一致
    expect_fp = SG.fingerprint_of({
        "domain": "supervision", "cue": first["cue"],
        "lesson": first["lesson"], "evidence": first["evidence"],
        "confidence": first["confidence"]})
    assert first["fingerprint"] == expect_fp


def test_cli_writes_payloads_and_readme(tmp_path, capsys):
    rc = G.main(["--out", str(tmp_path), "--now", "1761430000"])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.glob("*.payload.json"))
    assert len(files) == 7
    assert (tmp_path / "README.md").is_file()
    out = capsys.readouterr().out
    assert "未签名" in out


def test_sign_verify_roundtrip(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    key_file = tmp_path / "k.pem"
    key_file.write_bytes(pem)

    payload = {"kind": "experience_write", "item_id": "EXP-SUPERVISION-001",
               "fingerprint": "a" * 64, "action": "write",
               "ts": time.time(), "nonce": "nonce-1"}
    payload_file = tmp_path / "p.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    sig_file = tmp_path / "sig.json"

    rc = SA.main(["--kind", "experience_write",
                  "--payload-file", str(payload_file),
                  "--private-key", str(key_file),
                  "--passphrase-env", "NO_SUCH_ENV",
                  "--out", str(sig_file), "--i-am-human"])
    assert rc == 0
    sig = json.loads(sig_file.read_text(encoding="utf-8"))
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    ok, reason = SG.Verifier(pub_hex).verify(
        kind=payload["kind"], item_id=payload["item_id"],
        fingerprint=payload["fingerprint"], action=payload["action"],
        ts=sig["ts"], nonce=sig["nonce"], sig_hex=sig["sig"], now=time.time())
    assert ok and reason == "ok"


def test_sign_refuses_without_human_flag(tmp_path):
    payload_file = tmp_path / "p.json"
    payload_file.write_text(json.dumps({
        "kind": "experience_write", "item_id": "X", "fingerprint": "f",
        "action": "write", "ts": time.time(), "nonce": "n"}), encoding="utf-8")
    key_file = tmp_path / "k.pem"
    key_file.write_text("not-a-key", encoding="utf-8")
    rc = SA.main(["--kind", "experience_write",
                  "--payload-file", str(payload_file),
                  "--private-key", str(key_file),
                  "--out", str(tmp_path / "s.json")])
    assert rc == 2


def test_apply_requires_signature_dir(tmp_path):
    rc = G.main(["--out", str(tmp_path / "out"), "--apply",
                 "--experience-root", str(tmp_path / "lib")])
    assert rc == 2


def test_apply_rejects_non_empty_supervision(tmp_path, monkeypatch):
    payloads = G.build_payloads(now=1761430000.0)
    root = tmp_path / "lib"
    # 伪造一条 supervision 已有条目：写 _index.jsonl 即可（_check_empty 只读索引）
    root.mkdir(parents=True)
    (root / "_index.jsonl").write_text(json.dumps({
        "entry_id": "EXP-SUPERVISION-001", "domain": "supervision",
        "cue": "x"}), encoding="utf-8")
    with pytest.raises(ValueError):
        G.apply_payloads(payloads, tmp_path / "sig", root, require_empty=True)
