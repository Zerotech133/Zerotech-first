# -*- coding: utf-8 -*-
"""HQ-02：任务板只读模型 + 原子领取契约测试。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_task_contract as hc  # noqa: E402
import hq_tasks as ht  # noqa: E402


def _sample():
    return {
        "T-1": {"status": "parked", "owner": "", "desc": "demo", "revision": 0},
        "T-2": {"status": "doing", "owner": "OTHER", "desc": "busy"},
        "T-3": {"status": "done", "owner": "X", "desc": "done"},
    }


def test_board_real_shape():
    b = ht.build_board()
    s = b["summary"]
    assert s["total"] >= 2000
    assert s["wip"] == len(b["board"]["doing"]) + len(b["board"]["review"]) <= 10
    assert "done" in s["by_status"]
    assert isinstance(b["stale"], list)


def test_board_json_cli_pure(capsys):
    rc = ht.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["summary"]["total"] >= 2000


def test_claim_success_and_immutable_input():
    tasks = _sample()
    before = json.dumps(tasks, ensure_ascii=False, sort_keys=True)
    r = hc.claim(tasks, "T-1", "D1", now=datetime(2026, 11, 2, 23, 0), lease_minutes=60)
    assert r["ok"] and r["code"] == "claimed"
    assert r["task"]["status"] == "doing"
    assert r["task"]["owner"] == "D1"
    assert r["task"]["revision"] == 1
    assert json.dumps(tasks, ensure_ascii=False, sort_keys=True) == before  # 不修改入参


def test_claim_busy_and_terminal():
    tasks = _sample()
    assert hc.claim(tasks, "T-2", "D1")["code"] == "busy"
    assert hc.claim(tasks, "T-3", "D1")["code"] == "terminal"
    assert hc.claim(tasks, "T-2", "OTHER")["code"] == "renewed"


def test_release_and_submit():
    tasks = _sample()
    c = hc.claim(tasks, "T-1", "D1")
    tasks["T-1"] = c["task"]
    rev = hc.submit_review(tasks, "T-1", "D1")
    assert rev["task"]["status"] == "review"
    tasks["T-1"] = rev["task"]
    rel = hc.release(tasks, "T-1", "D1")
    assert rel["ok"] and rel["task"]["status"] == "parked"
    assert rel["task"]["owner"] == ""


def test_lease_expiry_helper():
    t = {"lease_until": (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")}
    assert hc.is_lease_active(t) is False
    t2 = {"lease_until": (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")}
    assert hc.is_lease_active(t2) is True
