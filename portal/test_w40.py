# -*- coding: utf-8 -*-
"""W40 收口单测：卡片签名/幂等、操作人配对、超时升级、日报每日一次。"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_sign  # noqa: E402
import notification_decision as nd  # noqa: E402
import push_daily_digest as dg  # noqa: E402


# ── 卡片签名 / 幂等 ─────────────────────────────────────
def test_card_sign_verify_and_tamper(monkeypatch, tmp_path):
    monkeypatch.setattr(card_sign, "WORK", str(tmp_path))
    monkeypatch.setattr(card_sign, "SECRET_PATH", str(tmp_path / "s.txt"))
    monkeypatch.setattr(card_sign, "LEDGER", str(tmp_path / "req.jsonl"))
    v = card_sign.sign({"action_id": "dispatch_nudge", "dispatch_id": "D1"})
    assert v["rid"] and v["_sig"] and v["_ts"]
    assert card_sign.verify(v) == (True, "ok")
    assert card_sign.verify(dict(v, dispatch_id="D2"))[0] is False
    assert card_sign.verify({"action_id": "x"}) == (False, "unsigned")


def test_card_sign_expired(monkeypatch, tmp_path):
    monkeypatch.setattr(card_sign, "SECRET_PATH", str(tmp_path / "s.txt"))
    v = card_sign.sign({"a": 1})
    v["_ts"] = v["_ts"] - 10000
    assert card_sign.verify(v, ttl=600) == (False, "expired")


def test_card_idempotency(monkeypatch, tmp_path):
    monkeypatch.setattr(card_sign, "WORK", str(tmp_path))
    monkeypatch.setattr(card_sign, "SECRET_PATH", str(tmp_path / "s.txt"))
    monkeypatch.setattr(card_sign, "LEDGER", str(tmp_path / "req.jsonl"))
    v = {"rid": "rid-1", "action_id": "x"}
    assert card_sign.guard(v) == (True, "ok")
    assert card_sign.guard(v) == (False, "duplicate")


# ── 操作人配对 / 待确认升级 ─────────────────────────────
def test_owner_paired():
    ok, why = nd.owner_paired("田峰")
    assert ok is True, why
    ok2, why2 = nd.owner_paired("不存在的人")
    assert ok2 is False and "授权主表" in why2
    # 非 owner 角色（若有 member 账户）也必须拒绝
    ok3, _ = nd.owner_paired("田峰", operator_open_id="ou_not_in_list")
    assert ok3 is False


def test_stale_approvals_shape():
    rows = nd.stale_approvals(30)
    assert isinstance(rows, list)
    for r in rows:
        assert r.get("id") and r.get("category") == "announcement"


def test_precheck_execute_switch_off(monkeypatch):
    monkeypatch.delenv("PUSH_DECISION_EXECUTE", raising=False)
    r = nd.precheck_execute("no-such-decision")
    assert r["ok"] is False
    assert r["checks"]["switch"] is False


# ── 日报每日一次 ───────────────────────────────────────
def test_digest_once(monkeypatch, tmp_path):
    monkeypatch.setattr(dg, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(dg, "build_card", lambda: {})
    monkeypatch.setattr(dg, "send", lambda c: (True, "om_test"))
    ok, why = dg.send_once()
    assert ok is True and why == "om_test"
    ok2, why2 = dg.send_once()
    assert ok2 is True and why2 == "already_sent"
    st = json.load(open(dg.STATE, encoding="utf-8"))
    assert st["message_id"] == "om_test"
