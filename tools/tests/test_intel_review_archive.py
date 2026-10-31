# -*- coding: utf-8 -*-
"""intel_review_archive 分层归档回归：规则/只读 dry-run/备份/幂等（临时 DB，不碰生产）。

WO-INTEL-REVIEW-20261031（执行拍板 2/4）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import intel_review_archive as A  # noqa: E402

NOW = "2026-10-31T12:00:00+08:00"

Q_SCHEMA = """CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT, title TEXT,
    stocks TEXT, original_jev TEXT, status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)"""
N_SCHEMA = ("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
            "title TEXT, jev TEXT)")


def _mkdb(tmp_path) -> Path:
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute(Q_SCHEMA)
    con.execute(N_SCHEMA)
    con.commit()
    con.close()
    return p


def _add(con, rid, *, queued_at="2026-10-30 10:00", conf=0.5, impact=None,
         orig_raw=None, source="sina", ts="2026-09-01 00:00",
         news_impact=None, status="pending", title=None):
    orig = orig_raw if orig_raw is not None else json.dumps(
        {"impact": impact} if impact else {})
    nj = json.dumps({"impact": news_impact}) if news_impact else "{}"
    con.execute(
        "INSERT OR REPLACE INTO jev_review_queue "
        "(id,queued_at,conf,bias,title,stocks,original_jev,status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rid, queued_at, conf, "bull", title or ("T-" + rid), "[]", orig, status))
    if source is not None:
        con.execute("INSERT OR REPLACE INTO news (id,source,ts,title,jev) "
                    "VALUES (?,?,?,?,?)", (rid, source, ts, title or rid, nj))


def _seed(tmp_path) -> Path:
    """6 条待分层：覆盖高影响/低置信/新来源/近7天/坏 JSON/旧队列。"""
    p = _mkdb(tmp_path)
    con = sqlite3.connect(str(p))
    _add(con, "hi", impact="high", conf=0.9,
         queued_at="2026-10-30 09:00")                      # 40 +0 +20 = 60
    _add(con, "medium", orig_raw="{}", news_impact="medium",
         conf=0.55, queued_at="2026-10-30 09:01")           # 20 +8 +20 = 48（news.jev 回退）
    _add(con, "novel", source="newsrc", ts="2026-10-29 08:00",
         conf=0.55, queued_at="2026-10-30 09:02")           #  0 +8 +20 +10 = 38
    _add(con, "lowconf", conf=0.3,
         queued_at="2026-10-30 09:03")                      #  0 +15 +20 = 35
    _add(con, "stale", impact="medium", conf=0.5,
         queued_at="2026-09-01 09:00")                      # 20 +8 +0 = 28
    _add(con, "malformed", orig_raw="not-a-json", source=None, conf=None,
         queued_at="2026-10-30 09:04")                      #  0 +0 +20 = 20（坏 JSON 不崩）
    con.commit()
    con.close()
    return p


def _plan(db, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("human_budget", 2)
    kw.setdefault("machine_budget", 2)
    kw.setdefault("samples", 10)
    return A.build_plan(db_path=db, **kw)


def _counts(db):
    con = sqlite3.connect(str(db))
    try:
        rows = dict(con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status").fetchall())
        return rows
    finally:
        con.close()


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_plan_rules_and_counts(tmp_path):
    db = _seed(tmp_path)
    plan = _plan(db)
    c = plan["counts"]
    assert c["pending_before"] == 6
    assert c["keep"] == 4 and c["pending_after"] == 4
    assert c["legacy_to_mark"] == 2
    assert c["legacy_after"] == 2
    # 规则优先级：高影响 > 中影响(news.jev 回退) > 新来源 > 低置信；旧队列/坏 JSON 出局
    assert plan["keep_ids"] == ["hi", "medium", "novel", "lowconf"]
    assert set(plan["legacy_ids"]) == {"stale", "malformed"}
    assert plan["impact"] == {"keep": {"high": 1, "medium": 1, "none": 2},
                              "legacy": {"medium": 1, "none": 1}}
    assert plan["novel_sources_kept"] == 1
    t = plan["tier_counts"]
    assert (t["human"], t["machine"], t["over_budget"]) == (2, 2, 0)
    assert t["unreviewed_legacy"] == 2 and t["total"] == 6
    # 样本可核（含 impact/score），没有丢原文
    assert plan["samples"]["keep"][0]["id"] == "hi"
    assert plan["samples"]["keep"][0]["impact"] == "high"
    assert len(plan["samples"]["legacy"]) == 2


def test_dry_run_is_read_only(tmp_path):
    db = _seed(tmp_path)
    before = _sha(db)
    before_counts = _counts(db)
    plan = _plan(db)
    assert plan["ok"] and _counts(db) == before_counts
    assert _sha(db) == before
    # dry-run 不写备份、不建目录
    assert not any(Path(tmp_path).glob("bak-*"))
    assert not any(Path(tmp_path).glob("news.db.bak-*"))


def test_apply_backs_up_and_marks_legacy(tmp_path):
    db = _seed(tmp_path)
    bak = tmp_path / "bak"
    plan = _plan(db)
    res = A.apply_plan(plan, db_path=db, backup_dir=bak)
    assert res["ok"] and res["applied"] and res["marked_legacy"] == 2
    # 备份=apply 前一致性快照（全部仍 pending）+ 可用 + manifest
    backup = Path(res["backup"])
    assert backup.is_file() and backup.parent == bak
    con = sqlite3.connect(str(backup))
    try:
        assert con.execute("SELECT COUNT(*) FROM jev_review_queue"
                           ).fetchone()[0] == 6
        assert con.execute("SELECT COUNT(*) FROM jev_review_queue "
                           "WHERE status='pending'").fetchone()[0] == 6
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
    manifest = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert manifest["counts"]["marked_legacy"] == 2
    # 线上库：预算内 pending 保留，其余 legacy；原文/裁决字段不动
    assert _counts(db) == {"pending": 4, "unreviewed_legacy": 2}
    con = sqlite3.connect(str(db))
    try:
        row = dict(zip(
            ("title", "original_jev", "stocks", "reviewer", "decision"),
            con.execute("SELECT title, original_jev, stocks, reviewer, decision "
                        "FROM jev_review_queue WHERE id='stale'").fetchone()))
    finally:
        con.close()
    assert row["title"] == "T-stale"
    assert json.loads(row["original_jev"])["impact"] == "medium"
    assert row["reviewer"] is None and row["decision"] is None


def test_apply_idempotent_second_run_noop(tmp_path):
    db = _seed(tmp_path)
    bak = tmp_path / "bak"
    assert A.apply_plan(_plan(db), db_path=db, backup_dir=bak)["marked_legacy"] == 2
    backups = sorted(bak.glob("news.db.bak-*"))
    plan2 = _plan(db)  # 预算内只剩 4 条 pending
    assert plan2["counts"]["legacy_to_mark"] == 0
    res2 = A.apply_plan(plan2, db_path=db, backup_dir=bak)
    assert res2["ok"] and res2["applied"] is False and res2["marked_legacy"] == 0
    assert _counts(db) == {"pending": 4, "unreviewed_legacy": 2}
    assert sorted(bak.glob("news.db.bak-*")) == backups  # 没多备份


def test_cli_dry_run_json_and_errors(tmp_path, capsys):
    db = _seed(tmp_path)
    rc = A.main(["--db", str(db), "--dry-run", "--human-budget", "2",
                 "--machine-budget", "2", "--now", NOW, "--json", "--samples", "3"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["counts"]["keep"] == 4
    assert payload["counts"]["legacy_to_mark"] == 2
    assert _counts(db) == {"pending": 6}
    # 缺库 → 退出码 1（fail-visible）
    rc = A.main(["--db", str(tmp_path / "missing.db"), "--dry-run"])
    assert rc == 1
    # 坏 --now → 退出码 1
    rc = A.main(["--db", str(db), "--dry-run", "--now", "not-a-time"])
    assert rc == 1
    # 非法 budget
    with pytest.raises(ValueError):
        A.build_plan(db_path=db, now=NOW, human_budget=-1)


def test_cli_apply_creates_backup(tmp_path):
    db = _seed(tmp_path)
    bak = tmp_path / "bak"
    rc = A.main(["--db", str(db), "--apply", "--backup-dir", str(bak),
                 "--human-budget", "2", "--machine-budget", "2",
                 "--now", NOW, "--json"])
    assert rc == 0
    assert _counts(db) == {"pending": 4, "unreviewed_legacy": 2}
    backups = [p for p in bak.glob("news.db.bak-*")
               if not p.name.endswith(".manifest.json")]
    assert len(backups) == 1
    assert len(list(bak.glob("*.manifest.json"))) == 1
