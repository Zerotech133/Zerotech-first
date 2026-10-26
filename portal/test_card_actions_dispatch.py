# -*- coding: utf-8 -*-
"""WO-FS-141 · P3.11-lite：card_actions.dispatch 买点订阅/退订迁移单测。

纪律（全桩化，零副作用）：
  - monkeypatch sys.modules 注入假 subscribe_action，严禁触发
    quant_backtest/subscribe_action 真实读 receivers.json / 写订阅文件 / 发卡；
  - LOG_PATH / OVERLAY_PATH 重定向 tmp_path，不写真实 work 文件；
  - 零网络、不重启、不真发。

覆盖：入参转发与 operator、退订 act 归一（防误订阅，本波重点口径差）、
ok/msg 透传、第三返回值 card 不外泄、import 失败与 handler 异常
均 (False, …) 不伪装成功、sys.path 注入幂等、空转分支不再触达、
未迁移老链动作不再返回 True、审计流水、注册表键集不破坏。
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_actions  # noqa: E402

QB_PATH = os.path.join(os.path.dirname(HERE), "quant_backtest")

VAL = {"act": "subscribe_buypoint", "code": "sz000993", "px": 10.52,
       "kind": "buy_point", "label": "回踩不破 10.52"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离审计与覆盖层落盘；测试结束清理 quant_backtest 的 sys.path 注入。"""
    monkeypatch.setattr(card_actions, "LOG_PATH",
                        str(tmp_path / "action_log.jsonl"))
    monkeypatch.setattr(card_actions, "OVERLAY_PATH",
                        str(tmp_path / "card_actions.json"))
    yield tmp_path
    while QB_PATH in sys.path:
        sys.path.remove(QB_PATH)


@pytest.fixture
def fake_sa(monkeypatch):
    """假 subscribe_action 模块：subscribe_from_card 记录入参、返回可控。"""
    calls = []
    ret = {"value": (True, "已订阅", {"type": "raw", "data": {"x": 1}})}

    def _stub(value, operator=None):
        calls.append({"value": dict(value), "operator": operator})
        return ret["value"]

    mod = types.ModuleType("subscribe_action")
    mod.subscribe_from_card = _stub
    monkeypatch.setitem(sys.modules, "subscribe_action", mod)
    return calls, ret


# 1. subscribe_buypoint 正确转发 value 与 operator

def test_subscribe_forwards_value_and_operator(isolated, fake_sa):
    calls, _ = fake_sa
    ok, msg = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t1")
    assert ok is True and msg == "已订阅"
    assert calls[0]["value"] == VAL
    assert calls[0]["operator"] == "ou_t1"


# 2. 退订 act 归一为 "unsubscribe"（防误订阅，本波重点口径差）

def test_unsubscribe_act_normalized(isolated, fake_sa):
    calls, _ = fake_sa
    val = {"act": "unsubscribe_buypoint", "code": "sz000993",
           "kind": "buy_point"}
    ok, _ = card_actions.dispatch("unsubscribe_buypoint", val, "ou_t2")
    assert ok is True
    assert calls[0]["value"]["act"] == "unsubscribe"
    assert calls[0]["value"]["code"] == "sz000993"   # 其余键原样保留
    assert calls[0]["value"]["kind"] == "buy_point"


# 3. value 缺 act 键时退订同样补归一（不依赖调用方写对）

def test_unsubscribe_missing_act_still_normalized(isolated, fake_sa):
    calls, _ = fake_sa
    val = {"code": "sz000993"}
    card_actions.dispatch("unsubscribe_buypoint", val, "ou_t3")
    assert calls[0]["value"]["act"] == "unsubscribe"


# 4. ok=False 时 msg 原样透传，ok 不被翻成 True

def test_failure_msg_passthrough(isolated, fake_sa):
    calls, ret = fake_sa
    ret["value"] = (False, "缺少股票代码", None)
    ok, msg = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t4")
    assert ok is False and msg == "缺少股票代码"
    assert len(calls) == 1


# 5. 第三返回值 card 不外泄（WS 链只消费二元组）

def test_card_third_value_not_leaked(isolated, fake_sa):
    _, ret = fake_sa
    ret["value"] = (True, "已在订阅中", {"secret_card": {"a": [1, 2, 3]}})
    r = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t5")
    assert r == (True, "已在订阅中")          # 二元组，card 丢弃
    assert "secret_card" not in str(r)


# 6. subscribe_action import 失败 → (False, 明确错误)，绝不 True

def test_import_failure_returns_false(isolated, monkeypatch):
    monkeypatch.setitem(sys.modules, "subscribe_action", None)  # ImportError
    ok, msg = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t6")
    assert ok is False
    assert "订阅模块不可用" in msg


# 7. handler 抛异常 → (False, 明确错误)，绝不 True

def test_handler_exception_returns_false(isolated, fake_sa):
    _, ret = fake_sa

    def _boom(value, operator=None):
        raise RuntimeError("boom")

    ret["value"] = None  # 占位防误用；下面直接替换模块函数
    import sys as _sys
    _sys.modules["subscribe_action"].subscribe_from_card = _boom
    ok, msg = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t7")
    assert ok is False
    assert "boom" in msg and "订阅动作执行异常" in msg


# 8. sys.path 注入幂等：重复 dispatch 不产生重复路径项

def test_sys_path_injection_idempotent(isolated, fake_sa):
    for _ in range(3):
        card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t8")
    assert sys.path.count(QB_PATH) == 1


# 9. 两个动作不再触达 :311-313 空转分支（不再出现「由老链处理」文案）

def test_buypoint_actions_not_hitting_legacy_branch(isolated, fake_sa):
    ok1, msg1 = card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t9")
    ok2, msg2 = card_actions.dispatch("unsubscribe_buypoint",
                                      {"act": "unsubscribe_buypoint",
                                       "code": "sz000993"}, "ou_t9")
    assert "由老链处理" not in msg1 and "由老链处理" not in msg2
    assert ok1 is True and ok2 is True


# 10. 未迁移老链动作不再返回 True 伪装成功（fail-open 缺陷已治）
#     jev_ack 已于 W3 迁入统一分发（2026-10-24），改用仍未迁移的 er:list 验证口径。

def test_unmigrated_legacy_no_fail_open(isolated):
    ok, msg = card_actions.dispatch("er:list", {}, "")
    assert ok is False
    assert "老链动作未迁移" in msg and "8091" in msg


# 11. 审计：log_event 被调用且 result 非空

def test_audit_logged_with_result(isolated, fake_sa):
    card_actions.dispatch("subscribe_buypoint", dict(VAL), "ou_t11")
    events = card_actions.recent_events(1)
    assert events and events[0]["action"] == "subscribe_buypoint"
    assert events[0]["who"] == "ou_t11"
    assert events[0]["result"]


# 12. 既有注册表语义不破坏：ACTIONS 键集不变

def test_actions_keys_unchanged():
    assert set(card_actions.DEFAULT_ACTIONS) == {
        "tune_approve", "tune_reject",
        "decision_approve", "decision_reject",
        "er:list", "er:help", "er:choice", "er:set", "er:clear",
        "er:clear2", "er:noop",
        "ctl:sub_toggle", "ctl:param", "ctl:alert_ack", "ctl:alert_ignore",
        "ctl:alert_ack_all", "ctl:tasks", "ctl:board",
        "jev_ack", "jev_ignore",
        "subscribe_buypoint", "unsubscribe_buypoint", "dispatch_nudge",
        "subscribe_add", "picks_share", "beacon_view",
        "cmd:tune", "cmd:approve", "cmd:chanlun", "cmd:control",
        "cmd:stock", "cmd:detail", "cmd:tpsl", "cmd:help",
    }


def test_dispatch_nudge_registered_and_calls_ticket_loop(monkeypatch, isolated):
    info = card_actions.lookup("dispatch_nudge")
    assert info and info["enabled"] and info["kind"] == "card"
    import types
    calls = []
    fake = types.ModuleType("ticket_loop")
    fake.nudge = lambda did, by="customer": (
        calls.append((did, by)) or
        {"ok": True, "n": 2, "dispatch_id": did, "escalate": False})
    monkeypatch.setitem(__import__("sys").modules, "ticket_loop", fake)
    ok, msg = card_actions.dispatch("dispatch_nudge",
                                    {"dispatch_id": "lg-abc123456"}, "ou_u")
    assert ok is True and "第 2 次" in msg
    assert calls and calls[0][0] == "lg-abc123456"
