# -*- coding: utf-8 -*-
"""task_board_stale：分类与安全 apply（只处理 branch_missing 且 owner 白名单）。"""
import datetime as dt
import json
import sys
from pathlib import Path

import importlib.util

TOOL = Path(__file__).resolve().parents[1] / "task_board_stale.py"


def _load():
    spec = importlib.util.spec_from_file_location("tbs_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tasks():
    return {
        "OLD1": {"owner": "ZHAOYUN", "status": "doing", "created": "2026-10-01 09:00:00"},
        "OLD2": {"owner": "OTHER", "status": "doing", "created": "2026-10-01 09:00:00"},
        "DONE1": {"owner": "ZHAOYUN", "status": "done", "created": "2026-10-29 09:00:00"},
        "AHEAD": {"owner": "DSH-Lead", "status": "doing", "created": "2026-10-28 09:00:00"},
        "STALEIN": {"owner": "DSH-Lead", "status": "doing", "created": "2026-10-01 09:00:00"},
    }


def test_classify():
    m = _load()
    now = dt.datetime(2026, 10, 30, 18, 0, 0)
    board = {"branch_missing": ["OLD1", "OLD2"], "merged_but_open": ["DONE1"],
             "ahead_unmerged": ["AHEAD"]}
    rows = m.classify(_tasks(), board, now=now)
    by = {r["task"]: r for r in rows}
    assert by["OLD1"]["safe"] is True and by["OLD1"]["action"] == "abort"
    assert by["OLD2"]["safe"] is False
    assert by["DONE1"]["kind"] == "merged_but_open" and by["DONE1"]["safe"] is False
    assert by["AHEAD"]["kind"] == "ahead_unmerged"
    assert any(r["kind"] == "stale_in_progress" and r["task"] == "STALEIN" for r in rows)
    assert all(r["task"] != "AHEAD" or r["kind"] == "ahead_unmerged" for r in rows)


def test_apply_safe_only_whitelisted_branch_missing(tmp_path):
    m = _load()
    tp = tmp_path / "tasks.json"
    tp.write_text(json.dumps(_tasks(), ensure_ascii=False), encoding="utf-8")
    board = {"branch_missing": ["OLD1", "OLD2"]}
    rows = m.classify(_tasks(), board, now=dt.datetime(2026, 10, 30, 18, 0, 0))
    changed = m.apply_safe(rows, tp)
    assert changed == ["OLD1"]
    data = json.loads(tp.read_text(encoding="utf-8"))
    assert data["OLD1"]["status"] == "aborted"
    assert data["OLD2"]["status"] == "doing"
