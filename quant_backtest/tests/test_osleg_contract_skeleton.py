# -*- coding: utf-8 -*-
"""OSLINK-W4 归口契约实现测试（隔离树 / 合成数据；v1.0 已签认，实施期实跑）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 签认）；归属单 `K-QUANT-20261026-OSLEG-IMPL`。

覆盖契约 §四状态机全状态 + §五关联规则 + §六 N1-N6 负例 + §七无副作用。
运行：python -X utf8 -B -m pytest quant_backtest/tests/test_osleg_contract_skeleton.py -q
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge import osleg_contract as oc
from quant_backtest.agent_bridge.osleg_contract import (
    T_EXPIRE_SEC,
    OslegInbox,
)
from quant_backtest.watch_dispatch.route_outbox import RouteOutbox

T0 = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)
REPORT = "QR-20261026-001"
TRACE = "tr-20261026-001"


class FakeClock:
    """冻结时钟：expire 边界靠它做确定性验证。"""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, sec: float) -> None:
        self.t = self.t + timedelta(seconds=sec)


def _inbox(tmp_path, clock=None) -> OslegInbox:
    return OslegInbox(tmp_path / "osleg", clock=clock)


def _submit(inbox: OslegInbox, **over) -> dict:
    kw = {
        "kind": "stock_consult_result",
        "payload": {"code": "605100", "signal": "3buy"},
        "source": "qs1",
        "report_id": REPORT,
        "trace_id": TRACE,
        "channel": oc.CHANNEL_INBOX,
    }
    kw.update(over)
    return inbox.submit(**kw)


def _to_consumed(inbox: OslegInbox) -> dict:
    env = _submit(inbox)
    assert env["ok"] is True
    mid = env["msg_id"]
    assert inbox.accept(mid, "A0", TRACE)["ok"] is True
    assert inbox.consume(mid, "A0", TRACE)["ok"] is True
    return {"msg_id": mid, "report_id": REPORT, "trace_id": TRACE}


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(T0)


# ---------------------------------------------------------------- 正例 C1-C5
def test_c1_submit_returns_msg_id(tmp_path):
    """§三/§四：submit 落账并返回 msg_id；accepted_by 初始为空、状态 submitted。"""
    inbox = _inbox(tmp_path)
    env = _submit(inbox)
    assert env["ok"] is True and env["state"] == "submitted"
    assert env["replayed"] is False and env["duplicate"] is False
    assert str(env["msg_id"]).startswith("w4-")
    rec = inbox.receipt(msg_id=env["msg_id"])
    assert rec["ok"] is True and rec["status"] == "submitted"
    assert rec["alive"] is True and rec["accepted_by"] == ""
    assert rec["report_id"] == REPORT and rec["trace_id"] == TRACE
    # 落盘在隔离树内（root 之外不写）
    assert len(list((tmp_path / "osleg").glob("*.json"))) == 1


def test_c2_lifecycle_all_states_reachable(tmp_path, clock):
    """§四：submitted→accepted→consumed→answered→delivered 全链 + rejected/expired 可达。"""
    inbox = _inbox(tmp_path, clock)
    env = _submit(inbox)
    mid = env["msg_id"]
    seen = ["submitted"]
    r = inbox.accept(mid, "A0", TRACE)
    assert r["ok"] and r["state"] == "accepted"
    seen.append(r["state"])
    r = inbox.consume(mid, "A0", TRACE)
    assert r["ok"] and r["state"] == "consumed"
    seen.append(r["state"])
    r = inbox.answer(mid, "A0", "3买成立，观察 2 日", REPORT, TRACE)
    assert r["ok"] and r["state"] == "answered" and r["linked"] is True
    seen.append(r["state"])
    r = inbox.deliver(mid, "synthetic", "ref:1", TRACE)
    assert r["ok"] and r["state"] == "delivered"
    seen.append(r["state"])
    assert inbox.receipt(msg_id=mid)["status"] == "delivered"

    rej = _submit(inbox, report_id="QR-REJ", trace_id="tr-rej")
    r = inbox.reject(rej["msg_id"], "不在归口范围", "A0")
    assert r["ok"] and r["state"] == "rejected"
    seen.append(r["state"])

    exp = _submit(inbox, report_id="QR-EXP", trace_id="tr-exp")
    clock.advance(T_EXPIRE_SEC)
    r = inbox.expire()
    assert exp["msg_id"] in r["expired"]
    seen.append("expired")

    assert set(seen) == set(oc.STATES)


def test_c3_answer_must_link_report_id(tmp_path):
    """§五-1：答复 report_id 必须等于原提交；不一致拒收，不写 answered。"""
    inbox = _inbox(tmp_path)
    ctx = _to_consumed(inbox)
    bad = inbox.answer(ctx["msg_id"], "A0", "答复", "QR-OTHER", TRACE)
    assert bad["ok"] is False and bad["linked"] is False
    assert bad["error"] == "report_id_mismatch"
    assert inbox.receipt(msg_id=ctx["msg_id"])["status"] == "consumed"
    good = inbox.answer(ctx["msg_id"], "A0", "答复", REPORT, TRACE)
    assert good["ok"] is True and good["linked"] is True
    assert inbox.receipt(msg_id=ctx["msg_id"])["linked"] is True


def test_c4_trace_id_consistent_across_stages(tmp_path):
    """§五-2：trace_id 贯穿；缺失/不一致 → 该环 status=unknown，不得默认回填。"""
    inbox = _inbox(tmp_path)
    mid = _submit(inbox)["msg_id"]
    assert inbox.accept(mid, "A0", "")["status"] == "unknown"
    assert inbox.accept(mid, "A0", "tr-other")["error"] == "trace_mismatch"
    assert inbox.accept(mid, "A0", TRACE)["ok"] is True
    assert inbox.consume(mid, "A0", "tr-other")["status"] == "unknown"
    assert inbox.consume(mid, "A0", TRACE)["ok"] is True
    assert inbox.answer(mid, "A0", "答复", REPORT, "tr-other")["status"] == "unknown"
    assert inbox.answer(mid, "A0", "答复", REPORT, TRACE)["ok"] is True
    assert inbox.deliver(mid, "synthetic", "r", "")["status"] == "unknown"
    assert inbox.deliver(mid, "synthetic", "r", TRACE)["ok"] is True
    rec = inbox.receipt(msg_id=mid)
    assert rec["trace_id"] == TRACE and rec["linked"] is True


def test_c5_receipt_aggregates_both_channels(tmp_path):
    """§五-3：按 report_id 聚合时同进程 inbox 与 HTTP 两条记录都出现。"""
    inbox = _inbox(tmp_path)
    a = _submit(inbox, channel=oc.CHANNEL_INBOX)
    b = _submit(inbox, channel=oc.CHANNEL_HTTP)
    assert a["msg_id"] != b["msg_id"]
    receipt = inbox.receipt(report_id=REPORT)
    assert receipt["ok"] is True
    assert receipt["total"] == 2
    assert set(receipt["channels"]) == {oc.CHANNEL_INBOX, oc.CHANNEL_HTTP}
    assert {a["msg_id"], b["msg_id"]} <= set(receipt["msg_ids"])
    assert receipt["trace_consistent"] is True
    assert receipt["trace_ids"] == [TRACE]
    assert receipt["linked"] is False  # 尚未答复
    # 一条通道答复后：linked 成立，另一通道记录仍在聚合里
    inbox.accept(a["msg_id"], "A0", TRACE)
    inbox.consume(a["msg_id"], "A0", TRACE)
    inbox.answer(a["msg_id"], "A0", "答复", REPORT, TRACE)
    receipt2 = inbox.receipt(report_id=REPORT)
    assert receipt2["total"] == 2 and receipt2["linked"] is True


# ---------------------------------------------------------------- 负例 N1-N6
def test_n1_expire_without_consumer(tmp_path, clock):
    """§六 N1 + §十：T_expire=30min，≥ 阈值即 expired；已受理不误过期。"""
    assert T_EXPIRE_SEC == 30 * 60
    inbox = _inbox(tmp_path, clock)
    pending = _submit(inbox, report_id="QR-P", trace_id="tr-p")
    accepted = _submit(inbox, report_id="QR-A", trace_id="tr-a")
    assert inbox.accept(accepted["msg_id"], "A0", "tr-a")["ok"] is True

    clock.advance(T_EXPIRE_SEC - 1)
    assert inbox.expire()["expired"] == []
    assert inbox.receipt(msg_id=pending["msg_id"])["alive"] is True

    clock.advance(1)  # 恰好等于阈值 → expired（签认边界：≥ 即过期）
    expired = inbox.expire()["expired"]
    assert pending["msg_id"] in expired
    assert inbox.receipt(msg_id=pending["msg_id"])["status"] == "expired"
    assert inbox.receipt(msg_id=pending["msg_id"])["alive"] is False
    assert inbox.receipt(msg_id=accepted["msg_id"])["status"] == "accepted"

    clock.advance(T_EXPIRE_SEC * 2)
    inbox.expire()
    assert inbox.receipt(msg_id=accepted["msg_id"])["status"] == "accepted"


def test_n2_duplicate_submit_is_idempotent(tmp_path):
    """§六 N2：同通道同体重复提交 → 原 msg_id、duplicate=true、不产生第二条记录。"""
    inbox = _inbox(tmp_path)
    first = _submit(inbox)
    second = _submit(inbox)
    assert second["ok"] is True and second["replayed"] is True
    assert second["duplicate"] is True
    assert second["msg_id"] == first["msg_id"]
    rec = inbox.receipt(msg_id=first["msg_id"])
    assert rec["duplicate"] is True
    assert len(list((tmp_path / "osleg").glob("*.json"))) == 1


def test_n3_rejection_requires_reason_and_resubmit_new_msg(tmp_path):
    """§六 N3 + §十：拒接 reason 必填；重复拒接幂等；重投必须新 msg_id 且不复活旧单。"""
    inbox = _inbox(tmp_path)
    mid = _submit(inbox)["msg_id"]
    assert inbox.reject(mid, "", "A0")["error"] == "reject_reason_required"
    first = inbox.reject(mid, "归口外事项", "A0")
    assert first["ok"] is True and first["state"] == "rejected"
    again = inbox.reject(mid, "另一个原因", "A0")
    assert again["ok"] is True and again["replayed"] is True
    old = inbox.receipt(msg_id=mid)
    assert old["status"] == "rejected"
    assert old["retry_of"] == ""
    # 重投：同 payload/report/trace → 新 msg_id，带 retry_of，旧单不复活
    retry = _submit(inbox)
    assert retry["ok"] is True
    assert retry["msg_id"] != mid
    assert retry["retry_of"] == mid
    assert retry["state"] == "submitted"
    assert inbox.receipt(msg_id=mid)["status"] == "rejected"
    assert inbox.receipt(msg_id=retry["msg_id"])["state"] == "submitted"
    assert len(list((tmp_path / "osleg").glob("*.json"))) == 2


def test_n4_answer_without_report_id_rejected(tmp_path):
    """§六 N4：答复缺 report_id → 拒收，linked=false，不写 answered。"""
    inbox = _inbox(tmp_path)
    ctx = _to_consumed(inbox)
    env = inbox.answer(ctx["msg_id"], "A0", "答复", "", TRACE)
    assert env["ok"] is False and env["linked"] is False
    assert env["error"] == "report_id_missing"
    rec = inbox.receipt(msg_id=ctx["msg_id"])
    assert rec["status"] == "consumed" and rec["linked"] is False
    assert rec["answer_text"] == ""


def test_n5_missing_trace_id_is_contract_violation(tmp_path):
    """§六 N5：缺 trace_id → status=unknown，禁止回填默认值、不落账。"""
    inbox = _inbox(tmp_path)
    env = _submit(inbox, trace_id="")
    assert env["ok"] is False and env["status"] == "unknown"
    assert env["error"] == "trace_id_missing"
    assert list((tmp_path / "osleg").glob("*.json")) == []


def test_n6_channel_mix_distinct_msg_id_same_report_id(tmp_path):
    """§六 N6：同 payload 双通道 → 两个 msg_id、同一 report_id/trace_id。"""
    inbox = _inbox(tmp_path)
    a = _submit(inbox, channel=oc.CHANNEL_INBOX)
    b = _submit(inbox, channel=oc.CHANNEL_HTTP)
    assert a["msg_id"] != b["msg_id"]
    ra = inbox.receipt(msg_id=a["msg_id"])
    rb = inbox.receipt(msg_id=b["msg_id"])
    assert ra["report_id"] == rb["report_id"] == REPORT
    assert ra["trace_id"] == rb["trace_id"] == TRACE
    assert {ra["channel"], rb["channel"]} == {oc.CHANNEL_INBOX, oc.CHANNEL_HTTP}
    assert len(list((tmp_path / "osleg").glob("*.json"))) == 2


# ---------------------------------------------------------------- §七 无副作用
def test_c7_no_side_effects(tmp_path):
    """§七：不触网/不真投递/无审批交易副作用，全部落盘只在隔离树。"""
    assert RouteOutbox().trading_permitted is False
    src = inspect.getsource(oc)
    for forbidden in ("import socket", "import urllib", "import requests",
                      "http.client", "urlopen"):
        assert forbidden not in src, "W4 契约层不得含网络 IO：%s" % forbidden

    inbox = _inbox(tmp_path)
    mid = _submit(inbox)["msg_id"]
    inbox.accept(mid, "A0", TRACE)
    inbox.consume(mid, "A0", TRACE)
    inbox.answer(mid, "A0", "答复", REPORT, TRACE)
    env = inbox.deliver(mid, "synthetic", "ref:synthetic-1", TRACE)
    assert env["ok"] is True and env["delivered_channel"] == "synthetic"
    # 隔离树之外零写入：tmp_path 下只允许出现 osleg 工作目录
    assert {p.name for p in tmp_path.iterdir()} == {"osleg"}
    assert all(p.is_file() for p in (tmp_path / "osleg").glob("*.json"))


# ---------------------------------------------------------------- 契约文档
def test_contract_doc_exists():
    """契约正文必须与实现同仓（防止只写实现不落契约）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    # 2026-11-10 大归档（8aa5a827a）把 372 份历史计划迁入 _archive_20261110/（含本契约，冻结只读）；
    # 测试同步改指归档路径，契约本体与签认标记未动（11-11 修复）。
    doc = (root / "docs" / "plans" / "_archive_20261110" / "2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md")
    assert doc.is_file(), "契约草案缺失：%s" % doc
    text = doc.read_text(encoding="utf-8")
    assert "v1.0" in text and "签认" in text
