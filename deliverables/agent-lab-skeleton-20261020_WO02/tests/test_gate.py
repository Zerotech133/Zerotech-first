"""S4-D 守门工位测试：approval_gate / risk_guard。

账本路径经 tmp_path 注入，全程不发起任何真实网络请求
（bus_url 指向不可达地址，且用例不触发 report_up）。

注意：BaseAgent 在 prometheus 全局注册表注册固定名称指标，
同一进程内二次实例化会 DuplicateTimeseries；而 base_agent.py
归另一工位维护、本工位不得修改。因此本模块对每个类只实例化
一次（module 级 fixture），账本路径按用例注入（路径注入本是
ApprovalGate 的设计接口）。
"""
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from roles.approval_gate import ApprovalGate, PrincipalVerifier, signature_payload
from roles.risk_guard import RiskGuard

# 指向不可达回环端口；BaseAgent 构造需要该参数，但本组用例
# 不会调用任何上行汇报，因此不产生真实网络。
DUMMY_KW = {"report_to": "principal", "bus_url": "http://127.0.0.1:1",
            "metrics_port": 59999}

# 测试用密钥对（仅本进程内生成，不落盘、不代表任何真实身份）。
TEST_KEY = Ed25519PrivateKey.generate()
TEST_PUB = TEST_KEY.public_key().public_bytes(
    encoding=Encoding.Raw, format=PublicFormat.Raw)


def sign(item_id: str, fingerprint: str, action: str = "approve",
         nonce: str = "n" * 32, ts: float | None = None) -> dict:
    """按网关同款载荷签名，产出可提交的 signature 对象。"""
    import time
    signed_ts = time.time() if ts is None else ts
    payload = signature_payload(item_id=item_id, fingerprint=fingerprint,
                                action=action, ts=signed_ts, nonce=nonce)
    return {"scheme": "ed25519-v1", "ts": signed_ts, "nonce": nonce,
            "sig": TEST_KEY.sign(payload).hex()}


@pytest.fixture(scope="module")
def gate(tmp_path_factory):
    return ApprovalGate(
        ledger_path=tmp_path_factory.mktemp("gate") / "ledger.jsonl",
        verifier=PrincipalVerifier(TEST_PUB),
        **DUMMY_KW)


@pytest.fixture(scope="module")
def guard():
    return RiskGuard(**DUMMY_KW)


def read_lines(path):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def use_ledger(gate, tmp_path):
    """为本用例注入独立账本路径（只追加日志，用例间互不可见）。"""
    gate.ledger_path = tmp_path / "ledger.jsonl"


def test_fingerprint_conflict_no_append(gate, tmp_path):
    """a) 指纹冲突返回 conflict，且账本无新条目。"""
    use_ledger(gate, tmp_path)
    item = {"item_id": "i1", "action": "trade", "amount": 1}
    gate.submit(item)
    res = gate.approve("i1", "0" * 64, {"by": "principal"})
    assert res == {"ok": False, "err": "fingerprint conflict"}
    entries = read_lines(gate.ledger_path)
    assert len(entries) == 1  # 仅 pending 一条，冲突不落盘
    assert entries[0]["status"] == "pending"


def test_approve_appends_after_pending(gate, tmp_path):
    """b) 指纹匹配 + 验签通过时追加 approved 条目，且账本先有 pending。"""
    use_ledger(gate, tmp_path)
    item = {"item_id": "i2", "action": "deploy", "version": "v1"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    proof = {"by": "tian", "note": "本人确认"}
    res = gate.approve("i2", fp, proof,
                       signature=sign("i2", fp, nonce="n" * 40))
    assert res["ok"] is True
    entries = read_lines(gate.ledger_path)
    assert len(entries) == 2
    assert entries[0]["status"] == "pending"
    assert entries[1]["status"] == "approved"
    assert entries[1]["fingerprint"] == fp
    assert entries[1]["principal_proof"] == proof  # 原样透传留痕
    # 认证结论来自验签，不是 principal_proof
    assert entries[1]["verified"] is True
    assert entries[1]["verification"] == "ed25519-v1"


def test_approve_missing_item_errors(gate, tmp_path):
    """c) 对不存在的 item approve 返回明确错误。"""
    use_ledger(gate, tmp_path)
    res = gate.approve("no-such-item", "0" * 64, {"by": "principal"})
    assert res["ok"] is False
    assert res["err"] == "item not found"
    assert not gate.ledger_path.exists()


def test_ledger_is_append_only(gate, tmp_path):
    """d) 账本只追加：两次 submit 产生两条 pending 记录。"""
    use_ledger(gate, tmp_path)
    gate.submit({"item_id": "a", "x": 1})
    gate.submit({"item_id": "b", "x": 2})
    entries = read_lines(gate.ledger_path)
    assert len(entries) == 2
    assert all(e["status"] == "pending" for e in entries)
    assert [e["item_id"] for e in entries] == ["a", "b"]


def test_threshold_and_bypass_alerts(guard):
    """e) 阈值越界产出预警；bypass_attempt 立即高 severity。"""
    alerts = guard.evaluate({"task_backlog_total": 999, "unknown_metric": 999})
    assert len(alerts) == 1  # 键不匹配的 unknown_metric 不比较
    a = alerts[0]
    assert a["metric"] == "task_backlog_total"
    assert a["value"] == 999
    assert a["threshold"] == 50
    assert a["severity"] == "medium"

    bypass = guard.evaluate({}, event={"type": "bypass_attempt"})
    assert len(bypass) == 1
    assert bypass[0]["severity"] == "high"

    # 预警同时追加进 events
    assert len(guard.events) == 2
