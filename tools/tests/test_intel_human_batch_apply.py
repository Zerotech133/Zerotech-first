# -*- coding: utf-8 -*-
"""人工复核 CSV 校验/回填器离线测试（WO-INTEL-HUMANAPPLY）。"""
from __future__ import annotations

import csv
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "intel_human_batch_apply.py"


def _load():
    spec = importlib.util.spec_from_file_location("_human_apply", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(tmp_path):
    p = tmp_path / "news.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE jev_review_queue(
        id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT,
        title TEXT, stocks TEXT, original_jev TEXT,
        status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT, reviewed_at TEXT,
        decision TEXT, note TEXT, review_type TEXT NOT NULL DEFAULT '')""")
    con.executemany(
        "INSERT INTO jev_review_queue(id,queued_at,status) VALUES(?,?,?)",
        [("a1", "2026-10-31", "pending"), ("a2", "2026-10-31", "pending"),
         ("done", "2026-10-31", "resolved")])
    con.commit()
    con.close()
    return p


def _csv(tmp_path, rows):
    p = tmp_path / "filled.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "decision", "reviewer", "note"])
        w.writeheader()
        w.writerows(rows)
    return p


def test_validate_and_dry_run_zero_write(tmp_path):
    m = _load()
    db = _db(tmp_path)
    p = _csv(tmp_path, [{"id": "a1", "decision": "correct", "reviewer": "老田", "note": ""},
                        {"id": "a2", "decision": "uncertain", "reviewer": "老田", "note": "证据不足"}])
    items, errors = m.validate(m.load_csv(p), db)
    assert not errors and len(items) == 2
    out = m.apply_items(items, db, tmp_path, tmp_path / "manifest.json", dry_run=True)
    assert out["dry_run"] and out["would_write"] == 2
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'").fetchone()[0] == 2
    con.close()


def test_apply_human_only_pending(tmp_path):
    m = _load()
    db = _db(tmp_path)
    p = _csv(tmp_path, [{"id": "a1", "decision": "correct", "reviewer": "老田", "note": ""},
                        {"id": "a2", "decision": "wrong", "reviewer": "老田", "note": ""}])
    items, errors = m.validate(m.load_csv(p), db)
    assert not errors
    out = m.apply_items(items, db, tmp_path, tmp_path / "manifest.json", dry_run=False)
    assert out["written"] == 2 and out["pending_after"] == 0
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT id,status,decision,reviewer,review_type FROM jev_review_queue "
        "WHERE id IN ('a1','a2') ORDER BY id").fetchall()
    con.close()
    assert rows == [("a1", "resolved", "correct", "老田", "human"),
                    ("a2", "resolved", "wrong", "老田", "human")]


def test_reject_ai_reviewer_and_non_pending(tmp_path):
    m = _load()
    db = _db(tmp_path)
    p = _csv(tmp_path, [{"id": "a1", "decision": "correct", "reviewer": "N1-AI", "note": ""},
                        {"id": "done", "decision": "correct", "reviewer": "老田", "note": ""},
                        {"id": "a2", "decision": "maybe", "reviewer": "老田", "note": ""}])
    items, errors = m.validate(m.load_csv(p), db)
    assert not items
    codes = {e["error"] for e in errors}
    assert {"ai_reviewer_forbidden", "not_pending", "bad_decision"} <= codes
