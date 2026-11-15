# -*- coding: utf-8 -*-
"""Merge 窗口批处理测试（2026-11-16 WO-MERGE-WINDOW）。

原则：**绝不真调 devtask merge / git rebase**——用假函数记录调用序列；
所有产物写到 tmp（不污染 deliverables）。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "merge_window_run_t", REPO / "tools" / "merge_window_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mw = _load()


def _at(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(2026, 11, 16, hh, mm, 0)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(mw, "LOG", tmp_path / "log.txt")
    monkeypatch.setattr(mw, "OUT_DIR", tmp_path / "out")


def _fake_ok():
    def fake_run_merge(tid, timeout):
        return {"tid": tid, "rc": 0, "seconds": 0.1, "tail": "fake", "ok": True}
    return fake_run_merge


# ── 窗口判定 ────────────────────────────────────────────────
def test_in_window_boundaries():
    assert mw.in_window(_at(16, 29))[0] is False
    assert mw.in_window(_at(16, 30))[0] is True
    assert mw.in_window(_at(17, 0))[0] is True
    assert mw.in_window(_at(17, 1))[0] is False
    assert mw.in_window(_at(21, 39))[0] is False
    assert mw.in_window(_at(21, 40))[0] is True
    assert mw.in_window(_at(22, 10))[0] is True
    assert mw.in_window(_at(22, 11))[0] is False


def test_in_window_reason_mentions_both_windows():
    ok, why = mw.in_window(_at(12, 0))
    assert ok is False and "16:30-17:00" in why and "21:40-22:10" in why


# ── 窗口外：dry-run 可跑、真跑必拒 ───────────────────────────
def test_dry_run_ok_outside_window(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    assert mw.main(["--tids", "A,B", "--dry-run"]) == 0
    doc = json.loads(list((tmp_path / "out").rglob("plan_*.json"))[0].read_text(encoding="utf-8"))
    assert doc["window_ok"] is False  # 计划里如实记"窗口外"


def test_refuse_outside_window(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    assert mw.main(["--tids", "A"]) == 2  # 窗口外拒绝，不写任何结果 JSON
    assert not list((tmp_path / "out").rglob("merge_*.json"))


def test_now_rejected_without_dry_run(monkeypatch, tmp_path):
    """--now 只能与 --dry-run 同用，防伪造时间绕过窗口守卫。"""
    _isolate(monkeypatch, tmp_path)
    assert mw.main(["--tids", "A", "--now", "16:31"]) == 2


# ── 计划落盘与顺序同源 ───────────────────────────────────────
def test_dry_run_in_window_writes_plan(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert mw.main(["--tids", "A,B", "--dry-run", "--now", "16:31"]) == 0
    files = list((tmp_path / "out").rglob("plan_*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["dry_run"] is True and doc["window_ok"] is True
    assert doc["planned"][0]["kinds"] == ["rebase", "merge"]
    assert doc["planned"][0]["steps"][0]["cmd"].endswith("rebase master")
    assert doc["planned"][0]["steps"][-1]["cmd"].endswith("merge A")
    assert len(doc["planned"]) == 2


def test_plan_matches_execution(monkeypatch, tmp_path):
    """核心验收：dry-run 的步骤序列 == 实际执行的步骤序列（同一 plan_for 来源）。"""
    _isolate(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_run_merge(tid, timeout):
        calls.append((mw.STEP_MERGE, tid))
        return {"tid": tid, "rc": 0, "seconds": 0.1, "tail": "fake", "ok": True}

    def fake_rebase(tid, timeout=600):
        calls.append((mw.STEP_REBASE, tid))
        return {"ok": True, "tail": "rebased"}

    monkeypatch.setattr(mw, "run_merge", fake_run_merge)
    monkeypatch.setattr(mw, "rebase_branch", fake_rebase)

    assert mw.main(["--tids", "A,B", "--dry-run", "--now", "16:31"]) == 0
    plan_doc = json.loads(list((tmp_path / "out").rglob("plan_*.json"))[0].read_text(encoding="utf-8"))
    planned = [(k, p["tid"]) for p in plan_doc["planned"] for k in p["kinds"]]

    calls.clear()
    monkeypatch.setattr(mw, "_now", lambda: _at(16, 31))
    assert mw.main(["--tids", "A,B"]) == 0

    assert calls == planned, "演练顺序与执行顺序不一致：%s vs %s" % (calls, planned)
    assert planned == [("rebase", "A"), ("merge", "A"), ("rebase", "B"), ("merge", "B")]


# ── 执行分支 ───────────────────────────────────────────────
def test_force_bypasses_window_but_marks(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    monkeypatch.setattr(mw, "run_merge", lambda tid, timeout: (
        calls.append(tid) or {"tid": tid, "rc": 0, "seconds": 0.1, "tail": "fake", "ok": True}))
    monkeypatch.setattr(mw, "rebase_branch", lambda tid, timeout=600: {"ok": True, "tail": "rebased"})
    assert mw.main(["--tids", "A", "--force"]) == 0
    assert calls == ["A"]
    doc = json.loads(list((tmp_path / "out").rglob("merge_*.json"))[0].read_text(encoding="utf-8"))
    assert doc["forced"] is True and doc["ok"] is True
    assert doc["results"][0]["plan"] == ["rebase", "merge"]
    assert doc["results"][0]["steps"][0] == {"kind": "rebase", "ok": True, "tail": "rebased"}


def test_inside_window_batch_partial_failure(monkeypatch, tmp_path):
    """批量部分失败：一条失败不连锁，整体 rc=1。"""
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mw, "_now", lambda: _at(16, 31))
    monkeypatch.setattr(mw, "run_merge", lambda tid, timeout: (
        calls.append(tid) or {"tid": tid, "rc": 1, "seconds": 0.1, "tail": "boom", "ok": False}))
    monkeypatch.setattr(mw, "rebase_branch", lambda tid, timeout=600: {"ok": True, "tail": "rebased"})
    assert mw.main(["--tids", "A,B"]) == 1
    assert calls == ["A", "B"]
    doc = json.loads(list((tmp_path / "out").rglob("merge_*.json"))[0].read_text(encoding="utf-8"))
    assert [r["ok"] for r in doc["results"]] == [False, False]
    assert doc["results"][0]["steps"][-1]["tail"] == "boom"  # 尾行可追


def test_rebase_failure_skips_merge(monkeypatch, tmp_path):
    """rebase 冲突 → 该条跳过 merge（step 列表只有 rebase，长度 != plan）。"""
    _isolate(monkeypatch, tmp_path)
    merged = []
    monkeypatch.setattr(mw, "_now", lambda: _at(16, 31))
    monkeypatch.setattr(mw, "run_merge", lambda tid, timeout: (
        merged.append(tid) or {"tid": tid, "rc": 0, "seconds": 0.1, "tail": "fake", "ok": True}))
    monkeypatch.setattr(mw, "rebase_branch",
                        lambda tid, timeout=600: {"ok": False, "tail": "rebase_conflict（已 abort，未改 master）"})
    assert mw.main(["--tids", "A"]) == 1
    assert merged == []  # 没调 merge
    r0 = json.loads(list((tmp_path / "out").rglob("merge_*.json"))[0].read_text(encoding="utf-8"))["results"][0]
    assert r0["ok"] is False and r0["plan"] == ["rebase", "merge"]
    assert len(r0["steps"]) < len(r0["plan"]), "中途停止必须体现为 steps 短于 plan"
    assert len(r0["steps"]) == 1 and r0["steps"][0]["kind"] == "rebase" and r0["steps"][0]["ok"] is False
