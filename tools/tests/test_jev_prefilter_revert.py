# -*- coding: utf-8 -*-
"""jev_prefilter_revert 单测（BATCH4）：只回退 src=prefilter 行。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

import jev_prefilter_revert as rv  # noqa: E402


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE news (id TEXT PRIMARY KEY, jev TEXT, created_at TEXT, source TEXT)")
    rows = [
        ("p1", json.dumps({"src": "prefilter", "bias": "neutral"}), "2026-10-30 10:00", "sina"),
        ("p2", json.dumps({"src": "prefilter", "bias": "neutral"}), "2026-08-01 10:00", "sina"),
        ("n1", json.dumps({"src": "local", "bias": "bull"}), "2026-10-30 11:00", "sina"),
    ]
    conn.executemany("INSERT INTO news VALUES (?,?,?,?)", rows)
    conn.commit()
    return conn


def test_find_candidates_filters_prefilter_only():
    conn = _db()
    rows = rv.find_candidates(conn, days=30)
    ids = {r[0] for r in rows}
    conn.close()
    # p2 超出 30 天窗口；n1 不是 prefilter
    assert ids == {"p1"}


def test_revert_clears_only_given_ids():
    conn = _db()
    n = rv.revert(conn, ["p1"])
    left = dict(conn.execute("SELECT id, jev FROM news").fetchall())
    conn.close()
    assert n == 1
    assert left["p1"] is None
    assert left["n1"] is not None