# -*- coding: utf-8 -*-
"""阚泽部派单通道单测（K5.1，WO-FS-148-7）。

覆盖：意图→kind 映射、真投递到 inbox_gateway（临时目录）、
幂等（同 msg_id 二次投递 replayed）、回执查询、
超时扫描（deadline 判定）、失败诚实返回、开关默认关。
"""

import json
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = Path(HERE).parent
sys.path.insert(0, str(ROOT / "agent_platform"))

import comm_dispatch as cd  # noqa: E402


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    from gateway import inbox_gateway as ig

    root = tmp_path / "bridge_inbox"
    ig.set_store(root)
    monkeypatch.setattr(cd, "_inbox", lambda: ig)
    monkeypatch.setenv("COMM_FLAGS_PATH", str(tmp_path / "no_flags.json"))
    # 默认开：本文件除 test_11 外都测「开」路径；test_11 显式关
    monkeypatch.setenv("COMM_DISPATCH_ENABLED", "1")
    return root


def _res(intent="stock_consult", code="605100", trace="abc123def456"):
    return {
        "intent": intent,
        "entities": {"code": code} if code else {},
        "trace_id": trace,
        "confidence": 0.9,
        "channel": "regex",
    }


# ── kind 映射 ────────────────────────────────
def test_01_kind_mapping():
    assert cd.kind_for("stock_consult") == "task_request"
    assert cd.kind_for("watch_setup") == "task_request"
    assert cd.kind_for("complaint") == "feedback"
    assert cd.kind_for("smalltalk") == "question"
    assert cd.kind_for("unclear") == "question"


# ── 真投递 ───────────────────────────────────
def test_02_dispatch_writes_inbox(inbox):
    r = cd.dispatch(_res(), "ou_a", msg_id="M1")
    assert r["ok"] is True
    assert r["dispatch_id"].startswith("lg-")
    files = list((inbox / "inbox").glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["kind"] == "task_request"
    assert rec["payload"]["intent"] == "stock_consult"
    assert rec["payload"]["code"] == "605100"
    assert rec["source"] == "feishu_comm"


def test_03_idempotent_same_msg(inbox):
    a = cd.dispatch(_res(), "ou_b", msg_id="M2")
    b = cd.dispatch(_res(), "ou_b", msg_id="M2")
    assert a["dispatch_id"] == b["dispatch_id"]
    assert b["replayed"] is True
    assert len(list((inbox / "inbox").glob("*.json"))) == 1


def test_04_different_msg_new_ticket(inbox):
    a = cd.dispatch(_res(), "ou_c", msg_id="M3")
    b = cd.dispatch(_res(), "ou_c", msg_id="M4")
    assert a["dispatch_id"] != b["dispatch_id"]


def test_05_complaint_goes_feedback(inbox):
    r = cd.dispatch(_res("complaint", code=None), "ou_d", msg_id="M5")
    files = list((inbox / "inbox").glob("*.json"))
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["kind"] == "feedback"
    assert r["ok"] is True


# ── 回执 ─────────────────────────────────────
def test_06_receipt_inbox_state(inbox):
    r = cd.dispatch(_res(), "ou_e", msg_id="M6")
    rc = cd.check_receipt(r["dispatch_id"])
    assert rc["ok"] is True
    assert rc["state"] == "inbox"


def test_07_receipt_processed_state(inbox):
    r = cd.dispatch(_res(), "ou_f", msg_id="M7")
    src = inbox / "inbox" / ("%s.json" % r["dispatch_id"])
    dst = inbox / "processed" / src.name
    src.rename(dst)
    rc = cd.check_receipt(r["dispatch_id"])
    assert rc["state"] == "processed"


def test_08_receipt_not_found(inbox):
    rc = cd.check_receipt("lg-nonexistent")
    assert rc["ok"] is True and rc["state"] == "not_found"


# ── 超时扫描 ─────────────────────────────────
def test_09_sweep_overdue(inbox):
    r = cd.dispatch(_res(), "ou_g", msg_id="M8", deadline_min=5)
    # 手工把 submitted_at 改到 10 分钟前
    f = inbox / "inbox" / ("%s.json" % r["dispatch_id"])
    rec = json.loads(f.read_text(encoding="utf-8"))
    import time

    rec["submitted_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 10 * 60)
    )
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    over = cd.sweep_overdue()
    assert any(o["dispatch_id"] == r["dispatch_id"] for o in over)


def test_10_sweep_not_overdue_fresh(inbox):
    cd.dispatch(_res(), "ou_h", msg_id="M9", deadline_min=5)
    assert cd.sweep_overdue() == []


def test_10b_sweep_skips_internal_notify_reminders(inbox):
    """ACAD1 安全默认通知/补发提醒是内部通知，不应触发派单进度巡视。"""
    import time

    old = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 20 * 60)
    )
    for i, src in enumerate(sorted(cd.NON_DISPATCH_SOURCES)):
        rec = {
            "id": "lg-internal-%d" % i,
            "kind": "task_request",
            "state": "inbox",
            "source": src,
            "submitted_at": old,
            "payload": {"title": "internal notice"},
        }
        (inbox / "inbox").mkdir(parents=True, exist_ok=True)
        (inbox / "inbox" / ("lg-internal-%d.json" % i)).write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )
    assert cd.sweep_overdue() == []


# ── 开关与失败诚实 ────────────────────────────
def test_11_dispatch_disabled_by_default(inbox, monkeypatch):
    monkeypatch.delenv("COMM_DISPATCH_ENABLED", raising=False)
    assert cd.enabled() is False
    r = cd.dispatch(_res(), "ou_i", msg_id="M10")
    assert r["ok"] is False and r["reason"] == "disabled"
    assert list((inbox / "inbox").glob("*.json")) == []


def test_12_bad_intent_still_question(inbox):
    r = cd.dispatch(_res("unknown_intent", code=None), "ou_j", msg_id="M11")
    assert r["ok"] is True
    files = list((inbox / "inbox").glob("*.json"))
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["kind"] == "question"


# ── 催办/回复闭环（2026-10-25）─────────────────────────────
def _make_dispatch(inbox):
    r = cd.dispatch(_res(), "ou_cust", msg_id="M-NUDGE")
    assert r["ok"] and r["dispatch_id"]
    return r["dispatch_id"]


def test_nudge_and_escalate(inbox, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path / "commdata")
    monkeypatch.setattr(cd, "NUDGE_PATH", tmp_path / "commdata/nudges.json")
    monkeypatch.setattr(cd, "REPLY_PATH", tmp_path / "commdata/replies.jsonl")
    did = _make_dispatch(inbox)
    assert cd.nudge(did)["n"] == 1
    r2 = cd.nudge(did, by="patrol")
    assert r2["n"] == 2 and r2["escalate"] is True
    assert r2["open_id"] == "ou_cust"
    assert cd.check_receipt(did)["state"] == "inbox"
    info = cd.nudge_info(did)
    assert info["n"] == 2 and info["escalate"] is True


def test_nudge_rejects_done_or_unknown(inbox, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path / "c2")
    monkeypatch.setattr(cd, "NUDGE_PATH", tmp_path / "c2/n.json")
    monkeypatch.setattr(cd, "REPLY_PATH", tmp_path / "c2/r.jsonl")
    r = cd.nudge("lg-doesnotexist")
    assert r["ok"] is False and "不存在" in r["error"]
    did = _make_dispatch(inbox)
    # 模拟已处理：把文件挪到 processed
    import shutil

    f = list((inbox / "inbox").glob("*.json"))[0]
    (inbox / "processed").mkdir(exist_ok=True)
    shutil.move(str(f), str(inbox / "processed" / f.name))
    r2 = cd.nudge(did)
    assert r2["ok"] is False and "已处理" in r2["error"]


def test_reply_store(inbox, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path / "c3")
    monkeypatch.setattr(cd, "NUDGE_PATH", tmp_path / "c3/n.json")
    monkeypatch.setattr(cd, "REPLY_PATH", tmp_path / "c3/r.jsonl")
    did = _make_dispatch(inbox)
    cd.record_reply(did, "已处理，答案如下")
    cd.record_reply(did, "补充一句", by="seat")
    rows = cd.replies(did)
    assert len(rows) == 2 and rows[-1]["by"] == "seat"
    assert cd.requester_open_id(did) == "ou_cust"
    assert cd.waited_min(did) >= 0
