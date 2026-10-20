"""签准网关本人身份核验测试（离线签名，老田 2026-10-20 裁定）。

裁定口径：「离线签名」——本人持私钥签 (item_id + 材料指纹 + 动作 + ts + nonce)，
网关持公钥验签。本文件核验的是**这张凭据不能被绕过**，而不是密码学库本身：

A) fail-closed：未配置 verifier 时一律拒绝，principal_proof 自报无效；
B) 语义绑定：签名绑 item_id / 材料指纹 / 动作三者，挪用到别处必失效；
C) 防重放：过期、未来、nonce 复用一律拒绝；
D) 不落盘：任何校验失败都不得追加账本条目。

测试密钥在本进程内临时生成，不落盘、不代表任何真实身份；账本落 tmp_path。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from roles.approval_gate import (
    SIG_SCHEME,
    ApprovalGate,
    PrincipalVerifier,
    signature_payload,
)

DUMMY_KW = {"report_to": "principal", "bus_url": "http://127.0.0.1:1",
            "metrics_port": 59998}

# 本人密钥（测试用）与"冒充者"密钥：后者用来自证签名不可伪造。
OWNER_KEY = Ed25519PrivateKey.generate()
IMPOSTOR_KEY = Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(encoding=Encoding.Raw,
                                         format=PublicFormat.Raw)


def make_gate(tmp_path: Path, *, configure: bool = True,
              key: Ed25519PrivateKey = OWNER_KEY) -> ApprovalGate:
    verifier = PrincipalVerifier(public_bytes(key)) if configure else None
    return ApprovalGate(ledger_path=tmp_path / "ledger.jsonl",
                        verifier=verifier, **DUMMY_KW)


def sign(item_id: str, fingerprint: str, *, action: str = "approve",
         nonce: str = "a" * 32, ts: float | None = None,
         key: Ed25519PrivateKey = OWNER_KEY) -> dict:
    signed_ts = time.time() if ts is None else float(ts)
    payload = signature_payload(item_id=item_id, fingerprint=fingerprint,
                                action=action, ts=signed_ts, nonce=nonce)
    return {"scheme": SIG_SCHEME, "ts": signed_ts, "nonce": nonce,
            "sig": key.sign(payload).hex()}


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]


def pending_item(gate: ApprovalGate, item: dict) -> tuple[str, str]:
    item_id = gate.submit(item)
    return item_id, ApprovalGate.fingerprint(item)


# ==================== A) fail-closed ====================

def test_without_verifier_refuses_and_writes_nothing(tmp_path: Path) -> None:
    """未配置 verifier：拒绝签准，账本只有 pending 一条（不降级为仅留痕）。"""
    gate = make_gate(tmp_path, configure=False)
    item_id, fp = pending_item(gate, {"item_id": "X1", "action": "trade"})
    res = gate.approve(item_id, fp, {"by": "laotian", "confirmed": True},
                       signature=sign(item_id, fp))
    assert res == {"ok": False, "err": "principal verification not configured"}
    entries = read_lines(gate.ledger_path)
    assert len(entries) == 1 and entries[0]["status"] == "pending"


def test_self_asserted_proof_cannot_substitute_signature(tmp_path: Path) -> None:
    """自报署名不构成认证：带 by/confirmed/USER 等字段但无签名 → 拒绝。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "X2", "action": "deploy"})
    res = gate.approve(item_id, fp,
                       {"by": "laotian", "USER": "laotian",
                        "confirmed": True, "integrity": "valid"})
    assert res["ok"] is False
    assert res["err"] == "missing signature"
    assert len(read_lines(gate.ledger_path)) == 1


# ==================== B) 语义绑定 ====================

def test_valid_signature_approves_and_marks_verified(tmp_path: Path) -> None:
    """正确签名：通过并落盘，verification/verified 字段如实标注。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "OK1", "action": "deploy"})
    res = gate.approve(item_id, fp, {"note": "本人确认"},
                       signature=sign(item_id, fp))
    assert res["ok"] is True
    entry = res["entry"]
    assert entry["status"] == "approved"
    assert entry["verified"] is True
    assert entry["verification"] == SIG_SCHEME
    assert entry["signature"]["nonce"] == "a" * 32
    assert len(read_lines(gate.ledger_path)) == 2


def test_signature_from_other_key_is_invalid(tmp_path: Path) -> None:
    """冒充者私钥签名：验签不通过（公钥只有本人那把）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "F1", "action": "trade"})
    res = gate.approve(item_id, fp, None,
                       signature=sign(item_id, fp, key=IMPOSTOR_KEY))
    assert res == {"ok": False, "err": "signature invalid"}
    assert len(read_lines(gate.ledger_path)) == 1


def test_approve_signature_cannot_be_reused_for_reject(tmp_path: Path) -> None:
    """动作绑定：approve 的签名不能拿来 reject（载荷含 action）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "ACT1", "action": "trade"})
    sig = sign(item_id, fp, action="approve")
    res = gate.reject(item_id, fp, None, signature=sig)
    assert res == {"ok": False, "err": "signature invalid"}


def test_signature_bound_to_item_id(tmp_path: Path) -> None:
    """事项绑定：A 事项的合法签名挪到 B 事项 → 失效。"""
    gate = make_gate(tmp_path)
    a_id, a_fp = pending_item(gate, {"item_id": "IT-A", "action": "trade"})
    b_id, b_fp = pending_item(gate, {"item_id": "IT-B", "action": "trade"})
    sig_for_a = sign(a_id, a_fp, nonce="b" * 32)
    # 用 A 的签名去签 B（指纹也换成 B 的，模拟"顺手挪一份签名"）
    res = gate.approve(b_id, b_fp, None, signature=sig_for_a)
    assert res == {"ok": False, "err": "signature invalid"}


def test_signature_bound_to_fingerprint(tmp_path: Path) -> None:
    """材料绑定：材料内容被换（指纹变），原签名失效。

    先按旧材料签名，再把待签事项换成新材料重新登记，用旧签名提交。
    """
    gate = make_gate(tmp_path)
    old_item = {"item_id": "FP-1", "action": "deploy", "version": "v1"}
    old_fp = ApprovalGate.fingerprint(old_item)
    sig = sign("FP-1", old_fp, nonce="c" * 32)
    # 材料被替换后重新登记（同一 item_id，新指纹）
    new_item = {"item_id": "FP-1", "action": "deploy", "version": "v2"}
    gate.submit(new_item)
    new_fp = ApprovalGate.fingerprint(new_item)
    assert new_fp != old_fp
    # 旧指纹提交 → 指纹冲突（材料已换，先于验签拦截）
    assert gate.approve("FP-1", old_fp, None, signature=sig) == \
        {"ok": False, "err": "fingerprint conflict"}
    # 新指纹 + 旧签名 → 指纹对上了但签名绑的是旧材料，验签不通过
    res = gate.approve("FP-1", new_fp, None, signature=sig)
    assert res == {"ok": False, "err": "signature invalid"}


# ==================== C) 防重放 ====================

def test_expired_timestamp_rejected(tmp_path: Path) -> None:
    """过期签名：ts 超出 ±max_skew_s 窗口 → 拒绝。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "T1", "action": "trade"})
    stale = sign(item_id, fp, ts=time.time() - 3600, nonce="d" * 32)
    res = gate.approve(item_id, fp, None, signature=stale)
    assert res == {"ok": False, "err": "signature expired"}
    assert len(read_lines(gate.ledger_path)) == 1


def test_future_timestamp_rejected(tmp_path: Path) -> None:
    """未来时间戳同样超窗拒绝（防"签一张长期有效票据"）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "T2", "action": "trade"})
    future = sign(item_id, fp, ts=time.time() + 3600, nonce="e" * 32)
    assert gate.approve(item_id, fp, None, signature=future) == \
        {"ok": False, "err": "signature expired"}


def test_nonce_replay_rejected(tmp_path: Path) -> None:
    """重放：同一 nonce 的签名用过即废，第二次拒绝。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "R1", "action": "trade"})
    sig = sign(item_id, fp, nonce="f" * 32)
    assert gate.approve(item_id, fp, None, signature=sig)["ok"] is True
    # 事项已 approved，二次提交先被 item not pending 拦；换新事项复用同一 nonce
    item_id2, fp2 = pending_item(gate, {"item_id": "R2", "action": "trade"})
    replay = sign(item_id2, fp2, nonce="f" * 32)
    res = gate.approve(item_id2, fp2, None, signature=replay)
    assert res == {"ok": False, "err": "signature replayed"}


def test_short_nonce_rejected(tmp_path: Path) -> None:
    """nonce 太短（可撞库）→ 判非法编码。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "N1", "action": "trade"})
    res = gate.approve(item_id, fp, None,
                       signature=sign(item_id, fp, nonce="abc"))
    assert res == {"ok": False, "err": "bad signature encoding"}


def test_unsupported_scheme_rejected(tmp_path: Path) -> None:
    """方案不符：换 scheme 标识即拒绝（防旧方案签名被误用）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "S1", "action": "trade"})
    sig = sign(item_id, fp, nonce="g" * 32)
    sig["scheme"] = "rsa-v1"
    assert gate.approve(item_id, fp, None, signature=sig) == \
        {"ok": False, "err": "unsupported scheme"}


def test_bad_hex_signature_rejected(tmp_path: Path) -> None:
    """签名串非 hex：判非法编码，不抛未捕获异常。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "H1", "action": "trade"})
    sig = sign(item_id, fp, nonce="h" * 32)
    sig["sig"] = "zzzz-not-hex"
    assert gate.approve(item_id, fp, None, signature=sig) == \
        {"ok": False, "err": "bad signature encoding"}


# ==================== D) 状态与顺序 ====================

def test_non_pending_item_rejected_before_verification(tmp_path: Path) -> None:
    """已决事项再签：先报 item not pending，不进入验签（不消费 nonce）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "D1", "action": "trade"})
    assert gate.approve(item_id, fp, None,
                        signature=sign(item_id, fp, nonce="i" * 32))["ok"] is True
    res = gate.approve(item_id, fp, None,
                       signature=sign(item_id, fp, nonce="j" * 32))
    assert res == {"ok": False, "err": "item not pending"}


def test_failed_verification_does_not_consume_nonce(tmp_path: Path) -> None:
    """失败不烧凭据：冒充签名失败后，同一 nonce 的真签名仍可用。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "NC1", "action": "trade"})
    bad = sign(item_id, fp, nonce="k" * 32, key=IMPOSTOR_KEY)
    assert gate.approve(item_id, fp, None, signature=bad)["ok"] is False
    good = sign(item_id, fp, nonce="k" * 32, key=OWNER_KEY)
    assert gate.approve(item_id, fp, None, signature=good)["ok"] is True


def test_reject_requires_signature_too(tmp_path: Path) -> None:
    """否决也是本人动作：无签名不得否决（防他人代你否决）。"""
    gate = make_gate(tmp_path)
    item_id, fp = pending_item(gate, {"item_id": "RJ1", "action": "trade"})
    assert gate.reject(item_id, fp, {"by": "someone"}) == \
        {"ok": False, "err": "missing signature"}
    ok = gate.reject(item_id, fp, None,
                     signature=sign(item_id, fp, action="reject",
                                    nonce="l" * 32))
    assert ok["ok"] is True
    assert ok["entry"]["status"] == "rejected"


def test_verifier_from_public_key_file_roundtrip(tmp_path: Path) -> None:
    """公钥文件装载：hex 文件可被 verifier 读入并正常验签。"""
    pub_path = tmp_path / "public.hex"
    pub_path.write_text(public_bytes(OWNER_KEY).hex() + "\n", encoding="utf-8")
    gate = ApprovalGate(ledger_path=tmp_path / "ledger.jsonl",
                        verifier=PrincipalVerifier.from_public_key_file(pub_path),
                        **DUMMY_KW)
    item_id, fp = pending_item(gate, {"item_id": "PK1", "action": "trade"})
    assert gate.approve(item_id, fp, None,
                        signature=sign(item_id, fp, nonce="m" * 32))["ok"] is True
