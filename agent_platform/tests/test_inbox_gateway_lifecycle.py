# -*- coding: utf-8 -*-
"""OSLINK-P4 answered/delivered 生命周期测试（隔离树·待签）。

对应合同：《2026-10-23_P4_传输层answered_delivered语义合同v1_待签.md》。
全部用临时目录注入（set_store），零真实消息、零真目录写入。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    inbox_gateway.set_store(tmp_path)
    return tmp_path


def _submit(**kw):
    payload = {"intent": "stock_consult", "code": "605100"}
    payload.update(kw.pop("payload", {}))
    env = inbox_gateway.submit("task_request", payload,
                               source="feishu_comm", **kw)
    assert env["ok"] is True
    return env["data"]["id"]


# ---- 1) submit → inbox，answered/delivered 双 false ----
def test_submit_is_inbox_with_no_answer_stamps(store):
    mid = _submit()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "inbox"
    assert r["answered"] is False and r["delivered"] is False


# ---- 2) consume → processed + 三类戳记 + answer_ref 原样 ----
def test_consume_stamps_answer(store):
    mid = _submit()
    env = inbox_gateway.consume(mid, answered_by="kanze_comm",
                                answer_ref="reply:om_abc123")
    assert env["ok"] is True and env["data"]["replayed"] is False
    rec = env["data"]["record"]
    assert rec["state"] == "processed"
    assert rec["answered_by"] == "kanze_comm"
    assert rec["answer_ref"] == "reply:om_abc123"
    assert rec["answered_at"]
    # 物理迁移：inbox 空、processed 有
    assert not (store / "inbox" / ("%s.json" % mid)).exists()
    assert (store / "processed" / ("%s.json" % mid)).exists()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "processed" and r["answered"] is True
    assert r["delivered"] is False


# ---- 3) 重复 consume 幂等：戳记与首次完全一致 ----
def test_consume_is_idempotent(store):
    mid = _submit()
    first = inbox_gateway.consume(mid, answered_by="kanze_comm",
                                  answer_ref="reply:1")["data"]["record"]
    second = inbox_gateway.consume(mid, answered_by="someone_else",
                                   answer_ref="reply:2")["data"]
    assert second["replayed"] is True
    assert second["record"] == first  # 首次获胜，无第二答复
    assert second["record"]["answer_ref"] == "reply:1"


# ---- 4) 未答复直接 deliver → not_answered，且不产生任何戳记 ----
def test_deliver_before_answer_is_rejected(store):
    mid = _submit()
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="m:1")
    assert env["ok"] is False
    assert env["error"]["code"] == "not_answered"
    rec = inbox_gateway.receipt(mid)["data"]["record"]
    assert "delivered_at" not in rec
    assert rec.get("state") == "inbox"


# ---- 5) deliver 后 delivered=true；重复 deliver 幂等 ----
def test_deliver_after_answer_and_idempotent(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="msg:om_x")
    assert env["ok"] is True and env["data"]["replayed"] is False
    rec = env["data"]["record"]
    assert rec["channel"] == "feishu" and rec["delivered_ref"] == "msg:om_x"
    assert rec["delivered_at"]
    r = inbox_gateway.receipt(mid)["data"]
    assert r["answered"] is True and r["delivered"] is True
    again = inbox_gateway.deliver(mid, channel="feishu",
                                  delivered_ref="msg:other")["data"]
    assert again["replayed"] is True
    assert again["record"]["delivered_ref"] == "msg:om_x"  # 首次获胜


# ---- 6) consume/deliver 不存在 id → not_found，不造文件 ----
def test_actions_on_missing_id_are_not_found(store):
    c = inbox_gateway.consume("lg-doesnotexist1", answered_by="x",
                              answer_ref="y")["data"]
    assert c["state"] == "not_found" and c["record"] == {}
    d = inbox_gateway.deliver("lg-doesnotexist1", channel="feishu",
                              delivered_ref="z")["data"]
    assert d["state"] == "not_found" and d["record"] == {}
    assert list((store / "processed").glob("*.json")) == []


# ---- 7) submit 幂等重放（既有行为）；已答复后重放 state=processed ----
def test_submit_replay_after_answer_reports_processed(store):
    mid = _submit(idempotency_key="feishu-om_x100b62b330fa0538dea87ac9b8cd39e")
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    env = inbox_gateway.submit(
        "task_request", {"intent": "stock_consult", "code": "605100"},
        source="feishu_comm",
        idempotency_key="feishu-om_x100b62b330fa0538dea87ac9b8cd39e")
    assert env["ok"] is True
    assert env["data"]["replayed"] is True
    assert env["data"]["id"] == mid
    assert env["data"]["state"] == "processed"


# ---- 8) 双份残留自愈：consume 清理 inbox 残留并回放既有戳记 ----
def test_crash_remnant_self_heals(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    # 模拟崩溃残留：inbox 又出现同 id 文件
    remnant = store / "inbox" / ("%s.json" % mid)
    remnant.write_text(json.dumps({"id": mid, "state": "inbox"}),
                       encoding="utf-8")
    env = inbox_gateway.consume(mid, answered_by="other",
                                answer_ref="reply:2")["data"]
    assert env["replayed"] is True
    assert env["record"]["answer_ref"] == "reply:1"  # 既有戳记不被覆盖
    assert not remnant.exists()  # 残留已自愈


# ---- 9) 老式消费（手动移 processed、无戳记）→ answered=false，不得推断已答复 ----
def test_legacy_move_without_stamps_is_not_answered(store):
    mid = _submit()
    src = store / "inbox" / ("%s.json" % mid)
    dst = store / "processed" / ("%s.json" % mid)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    src.unlink()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "processed"
    assert r["answered"] is False and r["delivered"] is False
    # 老式记录上的 deliver 仍被拒（无答复证据不得回传）
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="m:1")
    assert env["ok"] is False and env["error"]["code"] == "not_answered"
    # 显式 consume 后补戳记（真实答复动作）
    env2 = inbox_gateway.consume(mid, answered_by="kanze_comm",
                                 answer_ref="reply:late")["data"]
    assert env2["record"]["answer_ref"] == "reply:late"
    r2 = inbox_gateway.receipt(mid)["data"]
    assert r2["answered"] is True


# ---- 10) 原子写不留 tmp 残留 ----
def test_atomic_write_leaves_no_tmp(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    leftovers = [p.name for p in (store / "processed").iterdir()
                 if ".tmp-" in p.name]
    assert leftovers == []


# ---- 11) 既有回归：三值词表与坏 id 错误码不变 ----
def test_legacy_receipt_vocabulary_unchanged(store):
    mid = _submit()
    assert inbox_gateway.receipt(mid)["data"]["state"] == "inbox"
    assert inbox_gateway.receipt("lg-nope")["data"]["state"] == "not_found"
    bad = inbox_gateway.receipt("")
    assert bad["ok"] is False and bad["error"]["code"] == "bad_id"
    bad_kind = inbox_gateway.submit("not_a_kind", {})
    assert bad_kind["ok"] is False and bad_kind["error"]["code"] == "bad_kind"
