"""GOVAI-3 判例草稿签名链测试（WO-LEGION-GOVAI3-20261026）。

端到端（全离线）：draft JSON → emit payload → 用**一次性测试私钥**签名 →
配置公钥 env → apply 入库 → 断言 verified=True。
另覆盖：未配公钥拒写、缺签名、payload 篡改、entry_id 漂移、只读域、非 draft。

不生成 principal 密钥、不碰 _vault、不联网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
_PLATFORM = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_PLATFORM)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gov_ai_case_bridge as B  # noqa: E402
from engine import experience_store as S  # noqa: E402
from engine import signature_guard as SG  # noqa: E402

_DRAFT_ITEMS = [
    {"cue": "取消码不应计失败", "lesson": "非失败码必须单列，避免训练成噪声",
     "evidence": ["bundle:E1", "bundle:E2"], "confidence": 0.7},
    {"cue": "上游拒跑不是失败", "lesson": "upstream rc=3 单列，不计入失败率",
     "evidence": ["bundle:E3"], "confidence": 0.65},
]


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, pub_hex


def _draft(tmp_path: Path, items=None, status="draft", action="case_draft") -> Path:
    p = tmp_path / "case_draft.json"
    p.write_text(json.dumps({
        "schema": "governance_advice/v1", "action": action, "status": status,
        "items": items if items is not None else _DRAFT_ITEMS,
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _sign_payload(payload: dict, priv) -> dict:
    canonical = SG.canonical_payload(
        kind=payload["kind"], item_id=payload["item_id"],
        fingerprint=payload["fingerprint"], action=payload["action"],
        ts=payload["ts"], nonce=payload["nonce"])
    return {"scheme": SG.SIG_SCHEME, "sig": priv.sign(canonical).hex(),
            "ts": float(payload["ts"]), "nonce": payload["nonce"]}


def _emit_and_sign(tmp_path: Path, *, sign=True, pub_hex=None, items=None,
                   monkeypatch=None, experience_root=None):
    draft = _draft(tmp_path, items=items)
    out = tmp_path / "req"
    payloads = B.emit(draft, out, experience_root=experience_root)
    priv, pub = _keypair()
    if monkeypatch is not None:
        monkeypatch.setenv(SG.ENV_PUBKEY_HEX, pub_hex or pub)
        monkeypatch.setenv(SG.ENV_STRICT, "1")
    if sign:
        for p in payloads:
            sig = _sign_payload(p, priv)
            out.joinpath(p["item_id"] + ".signature.json").write_text(
                json.dumps(sig), encoding="utf-8")
    return out, payloads


# ---------------------------------------------------------------- emit

def test_emit_builds_payloads_with_canonical(tmp_path):
    draft = _draft(tmp_path)
    out = tmp_path / "req"
    payloads = B.emit(draft, out)
    assert [p["item_id"] for p in payloads] == ["EXP-SUPERVISION-001",
                                                "EXP-SUPERVISION-002"]
    p0 = payloads[0]
    assert p0["kind"] == B.KIND == "experience_write"
    assert p0["action"] == "write" and p0["domain"] == "supervision"
    expected_fp = SG.fingerprint_of({
        "domain": "supervision", "cue": _DRAFT_ITEMS[0]["cue"],
        "lesson": _DRAFT_ITEMS[0]["lesson"],
        "evidence": "; ".join(_DRAFT_ITEMS[0]["evidence"]),
        "confidence": 0.7})
    assert p0["fingerprint"] == expected_fp
    canonical = SG.canonical_payload(kind=p0["kind"], item_id=p0["item_id"],
                                     fingerprint=p0["fingerprint"], action=p0["action"],
                                     ts=p0["ts"], nonce=p0["nonce"])
    assert p0["canonical_payload_sha256"] == __import__("hashlib").sha256(canonical).hexdigest()
    assert (out / "EXP-SUPERVISION-001.payload.json").is_file()
    assert (out / "README.md").is_file()


def test_emit_rejects_readonly_domain(tmp_path):
    draft = _draft(tmp_path)
    with pytest.raises(B.BridgeError):
        B.emit(draft, tmp_path / "req", domain="riskguard")


def test_emit_rejects_non_draft_status(tmp_path):
    draft = _draft(tmp_path, status="failed")
    with pytest.raises(B.BridgeError):
        B.emit(draft, tmp_path / "req")


def test_emit_rejects_missing_evidence(tmp_path):
    items = [{"cue": "c", "lesson": "l", "evidence": [], "confidence": 0.5}]
    draft = _draft(tmp_path, items=items)
    with pytest.raises(B.BridgeError):
        B.emit(draft, tmp_path / "req")


# ---------------------------------------------------------------- apply 端到端

def test_apply_end_to_end_verified(tmp_path, monkeypatch):
    out, payloads = _emit_and_sign(tmp_path, monkeypatch=monkeypatch)
    lib = tmp_path / "library"
    entries = B.apply(out, experience_root=lib)
    assert [e["entry_id"] for e in entries] == ["EXP-SUPERVISION-001",
                                                "EXP-SUPERVISION-002"]
    assert all(e["verified"] is True for e in entries)
    exp_text = (lib / "supervision" / "EXPERIENCE.md").read_text(encoding="utf-8")
    assert "EXP-SUPERVISION-001" in exp_text and "verified" in exp_text


def test_apply_requires_public_key(tmp_path, monkeypatch):
    monkeypatch.delenv(SG.ENV_PUBKEY_HEX, raising=False)
    monkeypatch.delenv(SG.ENV_PUBKEY_FILE, raising=False)
    out, _ = _emit_and_sign(tmp_path, monkeypatch=None)
    with pytest.raises(B.BridgeError) as ei:
        B.apply(out, experience_root=tmp_path / "lib")
    assert "公钥" in str(ei.value)


def test_apply_missing_signature_fails(tmp_path, monkeypatch):
    out, payloads = _emit_and_sign(tmp_path, monkeypatch=monkeypatch)
    # 删掉第二张签名
    out.joinpath(payloads[1]["item_id"] + ".signature.json").unlink()
    with pytest.raises(B.BridgeError) as ei:
        B.apply(out, experience_root=tmp_path / "lib")
    assert "缺少签名" in str(ei.value)


def test_apply_tampered_payload_rejected(tmp_path, monkeypatch):
    out, payloads = _emit_and_sign(tmp_path, monkeypatch=monkeypatch)
    p = out / (payloads[0]["item_id"] + ".payload.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["cue"] = "被篡改的内容"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SG.SignatureError):
        B.apply(out, experience_root=tmp_path / "lib")


def test_apply_entry_id_drift_rejected(tmp_path, monkeypatch):
    out, payloads = _emit_and_sign(tmp_path, monkeypatch=monkeypatch)
    lib = tmp_path / "library"
    monkeypatch.delenv(SG.ENV_STRICT, raising=False)  # 预置条目不需要签名
    S.write_entry(domain="supervision", cue="已有条目", lesson="l",
                  evidence="e", confidence=0.5, signed_by="老田", root=lib)
    with pytest.raises(B.BridgeError) as ei:
        B.apply(out, experience_root=lib)
    assert "漂移" in str(ei.value)


def test_apply_unknown_domain_rejected(tmp_path, monkeypatch):
    items = [dict(_DRAFT_ITEMS[0])]
    draft = _draft(tmp_path, items=items)
    out = tmp_path / "req"
    payloads = B.emit(draft, out)
    priv, pub = _keypair()
    monkeypatch.setenv(SG.ENV_PUBKEY_HEX, pub)
    monkeypatch.setenv(SG.ENV_STRICT, "1")
    for p in payloads:
        out.joinpath(p["item_id"] + ".signature.json").write_text(
            json.dumps(_sign_payload(p, priv)), encoding="utf-8")
    # 篡改 domain 为未知域：id 漂移预检 / 写库白名单 都必须拒绝，不得落库
    p = out / (payloads[0]["item_id"] + ".payload.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["domain"] = "not_a_domain"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((S.StoreError, SG.SignatureError, B.BridgeError)):
        B.apply(out, experience_root=tmp_path / "lib")
    assert not (tmp_path / "lib").exists()


def test_emit_offset_reserves_ids_for_presigned_cards(tmp_path):
    """7 卡预分配 001-007 时，case 批次 offset=7 应从 008 起（防 id 撞车）。"""
    draft = _draft(tmp_path)
    out = tmp_path / "req"
    payloads = B.emit(draft, out, offset=7)
    assert [p["item_id"] for p in payloads] == ["EXP-SUPERVISION-008",
                                                "EXP-SUPERVISION-009"]
    # offset 不影响内容指纹，仅影响 item_id/canonical
    assert payloads[0]["fingerprint"] == SG.fingerprint_of({
        "domain": "supervision", "cue": _DRAFT_ITEMS[0]["cue"],
        "lesson": _DRAFT_ITEMS[0]["lesson"],
        "evidence": "; ".join(_DRAFT_ITEMS[0]["evidence"]),
        "confidence": 0.7})


def test_offset_apply_matches_after_cards_applied(tmp_path, monkeypatch):
    """先入库 7 条（模拟 7 卡），再 offset=7 的 case payload 应能顺利 apply。"""
    lib = tmp_path / "library"
    for i in range(7):
        S.write_entry(domain="supervision", cue="预置判例 %d" % i, lesson="l",
                      evidence="e", confidence=0.5, signed_by="老田", root=lib)
    draft = _draft(tmp_path)
    out = tmp_path / "req"
    # 7 条已入库后 base=7；无需再传 offset（offset 只用于"尚未入库的预分配"）
    payloads = B.emit(draft, out, experience_root=lib)
    assert [p["item_id"] for p in payloads] == ["EXP-SUPERVISION-008",
                                                "EXP-SUPERVISION-009"]
    priv, pub = _keypair()
    monkeypatch.setenv(SG.ENV_PUBKEY_HEX, pub)
    monkeypatch.setenv(SG.ENV_STRICT, "1")
    for p in payloads:
        out.joinpath(p["item_id"] + ".signature.json").write_text(
            json.dumps(_sign_payload(p, priv)), encoding="utf-8")
    entries = B.apply(out, experience_root=lib)
    assert [e["entry_id"] for e in entries] == ["EXP-SUPERVISION-008",
                                                "EXP-SUPERVISION-009"]
    assert all(e["verified"] is True for e in entries)
