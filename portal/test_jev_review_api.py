# -*- coding: utf-8 -*-
"""JEV 低置信复核 API 回归：列表筛选/排序/裁决写回。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))
import api_jev_review as R  # noqa: E402


def _db(tmp_path, monkeypatch):
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute("""CREATE TABLE jev_review_queue (
        id TEXT PRIMARY KEY, queued_at TEXT, conf REAL, bias TEXT, title TEXT,
        stocks TEXT, original_jev TEXT, status TEXT DEFAULT 'pending',
        reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)""")
    rows = [
        ("a", "2026-10-30 10:00", 0.3, "bull", "A"),
        ("b", "2026-10-30 10:01", 0.6, "bear", "B"),
        ("c", "2026-10-30 10:02", 0.5, "bull", "C"),
    ]
    con.executemany(
        "INSERT INTO jev_review_queue (id,queued_at,conf,bias,title) VALUES (?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    monkeypatch.setattr(R, "NEWS_DB", str(p))
    return p


def test_query_sort_filter_paging(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    d = R.query(sort="conf_asc")
    assert [x["id"] for x in d["items"]] == ["a", "c", "b"]
    assert d["summary"]["pending"] == 3
    d = R.query(bias="bull")
    assert {x["id"] for x in d["items"]} == {"a", "c"}
    d = R.query(limit=1, offset=1)
    assert d["returned"] == 1


def test_resolve_writes_back(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    r = R.resolve("c", "correct", "老田", "维持")
    assert r["ok"] and r["item"]["status"] == "resolved"
    assert r["item"]["reviewer"] == "老田"
    assert r["item"]["decision"] == "correct"
    assert (r["item"]["reviewed_at"] or "")
    assert not R.resolve("c", "wrong", "老田")["ok"]
    try:
        R.resolve("a", "bad", "老田")
    except ValueError:
        pass
    else:
        raise AssertionError("非法 decision 应拒绝")


# ── WO-INTEL-REVIEW-20261031：复核预算 50/200 + unreviewed_legacy 口径 ──────


def _ids(rows):
    return {x["id"] for x in rows}


def _insert(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO jev_review_queue "
        "(id,queued_at,conf,bias,title,status,reviewer,reviewed_at,decision,note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()


def test_summary_budgets_and_tier_counts(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    d = R.summary()
    assert d["human_budget"] == 50 and d["machine_budget"] == 200
    assert d["pending_budget"] == 250
    assert d["pending"] == 3 and d["resolved"] == 0
    assert d["unreviewed_legacy"] == 0
    t = d["tier_counts"]
    assert (t["human"], t["machine"], t["over_budget"]) == (3, 0, 0)
    assert t["total"] == 3 and t["pending"] == 3

    # 预算结构：50 人工 + 200 机器 + 其余 over_budget（尚未归档）
    con = sqlite3.connect(str(p))
    _insert(con, [("p%03d" % i, "2026-10-30 11:00", 0.5, "bull", "P%d" % i,
                   "pending", None, None, None, None) for i in range(300)])
    con.close()
    d = R.summary()
    assert d["pending"] == 303
    t = d["tier_counts"]
    assert (t["human"], t["machine"], t["over_budget"]) == (50, 200, 53)
    assert t["total"] == 303
    # queue 也透出预算与 tier_counts
    q = R.query(limit=1)
    assert q["human_budget"] == 50 and q["machine_budget"] == 200
    assert q["tier_counts"]["over_budget"] == 53
    assert q["summary"]["tier_counts"] == q["tier_counts"]


def test_legacy_not_counted_resolved_and_filterable(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    con = sqlite3.connect(str(p))
    con.execute("UPDATE jev_review_queue SET status='unreviewed_legacy', "
                "note='archived' WHERE id='a'")
    con.execute("UPDATE jev_review_queue SET status='resolved', reviewer='老田', "
                "decision='correct', reviewed_at='2026-10-31 10:00' WHERE id='b'")
    con.commit()
    con.close()
    d = R.summary()
    assert d["total"] == 3
    assert d["pending"] == 1
    assert d["resolved"] == 1          # 只算真 resolved
    assert d["unreviewed_legacy"] == 1  # legacy 不混入 resolved
    assert d["tier_counts"]["unreviewed_legacy"] == 1
    assert d["tier_counts"]["resolved"] == 1
    leg = R.query(status="unreviewed_legacy")
    assert leg["total"] == 1 and _ids(leg["items"]) == {"a"}
    allq = R.query(status="all")
    assert allq["total"] == 3
    # 未知 status 仍回落 pending（不 500）
    assert R.query(status="bogus")["filters"]["status"] == "pending"


def test_resolve_only_pending_and_legacy_reopen(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    con = sqlite3.connect(str(p))
    con.execute("UPDATE jev_review_queue SET status='unreviewed_legacy' "
                "WHERE id='a'")
    con.commit()
    con.close()
    # legacy 不可被 resolve 当 pending（不能伪装成已复核）
    r = R.resolve("a", "correct", "老田")
    assert r["ok"] is False and r["error"] == "not_pending"
    con = sqlite3.connect(str(p))
    assert con.execute("SELECT status FROM jev_review_queue WHERE id='a'"
                       ).fetchone()[0] == "unreviewed_legacy"
    con.close()
    # legacy 可重开：原文/裁决字段保留，回到 pending
    r = R.reopen("a", "老田", "补看")
    assert r["ok"] and r["item"]["status"] == "pending"
    assert "reopened by 老田" in (r["item"]["note"] or "")
    # 已非 legacy，二次重开拒绝；重开后可以正常 resolve
    assert R.reopen("a", "老田")["error"] == "not_legacy"
    assert R.resolve("a", "uncertain", "老田")["ok"] is True
    # resolved 也不可重开（避免覆盖既有结论）
    assert R.reopen("a", "老田")["error"] == "not_legacy"
    try:
        R.reopen("", "老田")
    except ValueError:
        pass
    else:
        raise AssertionError("缺 id 应拒绝")
    try:
        R.reopen("a", "")
    except ValueError:
        pass
    else:
        raise AssertionError("缺 reviewer 应拒绝")

