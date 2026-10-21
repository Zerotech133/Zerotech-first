# -*- coding: utf-8 -*-
"""FC-C · 入向订阅闸 + 引导卡片单测（P2 波次 2026-10-20）。

运行：python -m pytest portal/test_inbound_subscription.py -q
红线（派工单 2026-10-20 · 通用纪律）：
  - 全程不真发飞书（_send_card 打桩捕获）；
  - 不碰生产账本（_append_ledger 打桩捕获，不落盘）；
  - 不 import 真实 subscription_store 做断言——sys.modules 注入桩
    （FC-B 契约桩测：is_entitled(open_id, kind) -> (bool, reason)）。
覆盖：owner 豁免、observe 不拦截但留痕、enforce 拦截回引导卡、
import 失败/异常降级放行、subscription_guide 模板结构、kind 未注册
原样（FB-L 语义回归）、handle_inbound 主流程零回归。

说明：现有 handle_inbound 的身份闸（is_allowed）在 classify 前即拒绝
非白名单，闸的非 owner 分支按设计服务于 M3 后 customer 域放宽身份闸
的场景。端到端用例以「首次调用放行（身份闸）/再次调用拒绝（闸内
owner 豁免检查）」的桩模拟该形态，其余用例直接桩测 subscription_gate。
"""
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))

import inbound_gateway as gw  # noqa: E402
import card_templates as ct   # noqa: E402

ANON = "ou_anon_subtest_0001"   # 非 owner 白名单（闸内豁免检查为 False）
LAO = "ou_lao_subtest_0002"     # owner 白名单（is_allowed=True）

STUB_CALLS = []   # 桩 is_entitled 收到的 (open_id, kind) 调用记录


def _make_stub(entitled=False, reason=None, raise_exc=None):
    """构造 FC-B 契约桩模块（不 import 真实 subscription_store）。"""
    mod = types.ModuleType("subscription_store")

    def is_entitled(open_id, kind, now=None):
        STUB_CALLS.append((open_id, kind))
        if raise_exc is not None:
            raise raise_exc
        if reason is not None:
            return (entitled, reason)
        return (entitled,
                "stub_entitled" if entitled else "stub_no_subscription")

    mod.is_entitled = is_entitled
    return mod


def _identity_then_gate_stub(monkeypatch):
    """身份闸首次放行、闸内 owner 豁免检查返回 False 的桩
    （模拟 M3 后 customer 域：消息可入，但非 owner 仍受订阅闸约束）。"""
    calls = {"n": 0}

    def fake_allowed(oid):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(gw, "is_allowed", fake_allowed)


def _gate_rows(rows):
    return [r for r in rows if r.get("route") == "subscription_gate"]


@pytest.fixture()
def gate_env(monkeypatch):
    """打桩环境：发送/账本全捕获，模式复位 observe，账本咨询清零。"""
    sent, rows = [], []
    STUB_CALLS.clear()
    monkeypatch.setattr(gw, "SUBSCRIPTION_GATE_MODE", "observe")
    monkeypatch.setattr(
        gw, "_send_card",
        lambda card, oid: (sent.append((card, oid))
                           or (True, "stub_sent", "om_stub")))
    monkeypatch.setattr(gw, "_append_ledger",
                        lambda path, row: rows.append(row))
    yield {"sent": sent, "rows": rows}


# ── 一、subscription_gate 决策函数 ────────────────────────────

def test_owner_exempt_no_store_consult(gate_env, monkeypatch):
    """owner 白名单直接豁免：不咨询账本、不留闸记录。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: oid == LAO)
    monkeypatch.setitem(sys.modules, "subscription_store", _make_stub())
    assert gw.subscription_gate(LAO, "ticker") == (True, "owner_exempt")
    assert STUB_CALLS == []
    assert _gate_rows(gate_env["rows"]) == []


def test_gate_result_shape(gate_env, monkeypatch):
    """返回值契约：2 元组 (bool, str)。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: oid == LAO)
    res = gw.subscription_gate(LAO, "url")
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] is True and isinstance(res[1], str)


def test_observe_not_entitled_passes_with_record(gate_env, monkeypatch):
    """observe 模式未订阅：放行 + 一条 observe_pass 诚实留痕。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(reason="no_active_subscription"))
    allowed, reason = gw.subscription_gate(ANON, "ticker")
    assert allowed is True and reason == "observe:no_active_subscription"
    assert STUB_CALLS == [(ANON, "ticker")]
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1
    assert grows[0]["status"] == "observe_pass"
    assert grows[0]["evidence"] == "observe:no_active_subscription"
    assert grows[0]["detail"] == {"kind": "ticker", "mode": "observe"}


def test_observe_entitled_allowed(gate_env, monkeypatch):
    """observe 模式已订阅：放行，留痕 allowed（带账本 reason）。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(entitled=True, reason="sub_active"))
    allowed, reason = gw.subscription_gate(ANON, "url")
    assert allowed is True and reason == "sub_active"
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1 and grows[0]["status"] == "allowed"
    assert grows[0]["evidence"] == "sub_active"


def test_enforce_not_entitled_blocked(gate_env, monkeypatch):
    """enforce 模式未订阅：拦截（allowed=False），留痕 blocked。"""
    monkeypatch.setattr(gw, "SUBSCRIPTION_GATE_MODE", "enforce")
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(reason="no_active_subscription"))
    allowed, reason = gw.subscription_gate(ANON, "ticker")
    assert allowed is False and reason == "no_active_subscription"
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1 and grows[0]["status"] == "blocked"
    assert grows[0]["detail"]["mode"] == "enforce"


def test_enforce_entitled_passes(gate_env, monkeypatch):
    """enforce 模式已订阅：照常放行。"""
    monkeypatch.setattr(gw, "SUBSCRIPTION_GATE_MODE", "enforce")
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(entitled=True))
    allowed, reason = gw.subscription_gate(ANON, "text")
    assert allowed is True and reason == "stub_entitled"


def test_import_failure_fail_open(gate_env, monkeypatch):
    """import 失败（模块缺失）：降级放行 + 诚实留痕 fail_open。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store", None)  # import 即失败
    allowed, reason = gw.subscription_gate(ANON, "ticker")
    assert allowed is True and reason == "store_unavailable_fail_open"
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1 and grows[0]["status"] == "allowed"
    assert grows[0]["evidence"] == "store_unavailable_fail_open"


def test_import_failure_fail_open_enforce(gate_env, monkeypatch):
    """enforce 模式下 import 失败仍降级放行（fail-open 优先，不误伤）。"""
    monkeypatch.setattr(gw, "SUBSCRIPTION_GATE_MODE", "enforce")
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store", None)
    allowed, reason = gw.subscription_gate(ANON, "ticker")
    assert allowed is True and reason == "store_unavailable_fail_open"
    assert not any(g["status"] == "blocked"
                   for g in _gate_rows(gate_env["rows"]))


def test_is_entitled_raises_fail_open(gate_env, monkeypatch):
    """is_entitled 调用抛异常：降级放行，不向主流程抛错。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(raise_exc=RuntimeError("db locked")))
    allowed, reason = gw.subscription_gate(ANON, "text")
    assert allowed is True and reason == "store_unavailable_fail_open"


# ── 二、handle_inbound 端到端（桩测，零回归） ─────────────────

def test_handle_inbound_observe_pass(gate_env, monkeypatch):
    """observe 端到端：不拦截（ticker 照常受理），闸留痕一条。"""
    _identity_then_gate_stub(monkeypatch)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(reason="no_active_subscription"))
    r = gw.handle_inbound(ANON, "600519", "ob1")
    assert r["status"] == "routed" and r["detail"]["code"] == "600519"
    assert STUB_CALLS == [(ANON, "ticker")]
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1 and grows[0]["status"] == "observe_pass"


def test_handle_inbound_enforce_blocks_with_guide_card(gate_env, monkeypatch):
    """enforce 端到端：拦截、只回灰卡引导（业务受理卡未发）。"""
    monkeypatch.setattr(gw, "SUBSCRIPTION_GATE_MODE", "enforce")
    _identity_then_gate_stub(monkeypatch)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(reason="no_active_subscription"))
    r = gw.handle_inbound(ANON, "600519", "en1")
    assert r["status"] == "blocked_subscription"
    assert r["gate"] == "no_active_subscription"
    assert STUB_CALLS == [(ANON, "ticker")]
    assert len(gate_env["sent"]) == 1
    card = gate_env["sent"][0][0]
    assert card["header"]["template"] == "grey"
    assert "订阅服务" in card["header"]["title"]["content"]
    grows = _gate_rows(gate_env["rows"])
    assert len(grows) == 1 and grows[0]["status"] == "blocked"


def test_handle_inbound_owner_zero_regression(gate_env, monkeypatch):
    """主流程零回归（owner 白名单）：ticker 受理回执照发、无闸留痕。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: oid == LAO)
    monkeypatch.setitem(sys.modules, "subscription_store", _make_stub())
    r = gw.handle_inbound(LAO, "600519", "own1")
    assert r["status"] == "routed" and r["detail"]["code"] == "600519"
    assert _gate_rows(gate_env["rows"]) == []
    body = json.dumps(gate_env["sent"], ensure_ascii=False, default=str)
    assert "策略环（W44）尚未接线" in body, "B4 诚实留痕语义不得变化"


def test_handle_inbound_unclear_skips_gate(gate_env, monkeypatch):
    """澄清路由不经闸：零账本咨询、零闸留痕。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: oid == LAO)
    monkeypatch.setitem(sys.modules, "subscription_store",
                        _make_stub(reason="never"))
    r = gw.handle_inbound(LAO, "在吗", "uc1")
    assert r["status"] == "clarified"
    assert STUB_CALLS == []
    assert _gate_rows(gate_env["rows"]) == []


def test_handle_inbound_identity_reject_unchanged(gate_env, monkeypatch):
    """身份闸拒绝路径零回归：白名单外仍留痕零反馈（原始行为）。"""
    monkeypatch.setattr(gw, "is_allowed", lambda oid: False)
    monkeypatch.setitem(sys.modules, "subscription_store", _make_stub())
    r = gw.handle_inbound(ANON, "600519", "rej1")
    assert r == {"consumed": True, "rejected": True}
    assert STUB_CALLS == [] and gate_env["sent"] == []
    assert _gate_rows(gate_env["rows"]) == []
    # 身份闸拒绝留痕仍走原机制
    assert gate_env["rows"] and gate_env["rows"][0]["status"] == "rejected"


# ── 三、card_templates · subscription_guide 注册 ─────────────

def test_subscription_guide_registered():
    assert "subscription_guide" in ct.KIND_CARD_TEMPLATES


def test_subscription_guide_structure():
    card, msg_type = ct.apply_card_template(
        "subscription_guide", {"text": "x"}, "interactive")
    assert msg_type == "interactive"
    assert isinstance(card, dict)
    assert card["header"]["template"] == "grey"
    assert card["header"]["title"]["content"] == "🔒 订阅服务"
    assert card["elements"], "引导文案不得为空"
    dumped = json.dumps(card, ensure_ascii=False)
    assert "联系开通" in dumped
    assert "盯盘" in dumped and "预警" in dumped
    # M3 前无收银台：不放任何购买链接
    assert "http://" not in dumped and "https://" not in dumped


def test_subscription_guide_ignores_injected_content():
    """固定文案：调用方 content 不注入卡片（防塞购买链接）。"""
    card, _ = ct.apply_card_template(
        "subscription_guide",
        {"text": "点此购买 https://pay.example.com/buy"}, "interactive")
    dumped = json.dumps(card, ensure_ascii=False)
    assert "pay.example.com" not in dumped


def test_unregistered_kind_passthrough_fbL():
    """FB-L 语义回归：未注册 kind 原样返回。"""
    content = {"text": {"tag": "plain_text", "content": "keep"}}
    out, mt = ct.apply_card_template("no_such_kind", content, "interactive")
    assert out == content and mt == "interactive"


def test_non_interactive_passthrough():
    """msg_type 非 interactive 原样透传（含已注册 kind）。"""
    content = {"text": "plain"}
    out, mt = ct.apply_card_template("subscription_guide", content, "text")
    assert out == content and mt == "text"


# ── 直跑入口（与既有脚本式测试惯例一致） ──────────────────────
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
