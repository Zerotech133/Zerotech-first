# -*- coding: utf-8 -*-
"""P0 RFC 方案② R1/R2 单测：tdx_read.read_info 的 until/after SQL 游标分页。

钉子：
- 缺省（until/after 均 None）路径逐字不变（DESC + 无 _src_rowid 列）；
- after 游标：升序三键（date, COALESCE(_fetched_at,''), rowid）+ 严格大于——
  同秒并列组由 rowid 防跨页丢行（并列组 > 页宽场景）；
- NULL _fetched_at 行不因 COALESCE 丢行。
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tdx_read as tdx


def _make_db(tmp_path, rows):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE stock_news(ts_code TEXT, title TEXT, pub_date TEXT, "
        "_fetched_at TEXT)"
    )
    conn.executemany("INSERT INTO stock_news VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    marker = tmp_path / ".warehouse-publication" / "pending"
    marker.mkdir(parents=True, exist_ok=True)
    return str(db)


def _monkey_info_db(monkeypatch, path):
    monkeypatch.setattr(tdx, "INFO_DB", path)


def test_default_path_unchanged(tmp_path, monkeypatch):
    """after/until 缺省：仍 DESC、无 _src_rowid 列（既有调用方零影响）。"""
    base = "2026-11-06"
    rows = [("000001.SZ", "a", base, base + " 10:00:00"),
            ("000002.SZ", "b", base, base + " 09:00:00")]
    _monkey_info_db(monkeypatch, _make_db(tmp_path, rows))
    got = tdx.read_info("stock_news", limit=10)
    assert {r["title"] for r in got} == {"a", "b"}  # 只验集合（同日并列序未定义）
    assert "_src_rowid" not in got[0]


def test_until_filters_inclusive(tmp_path, monkeypatch):
    base = "2026-11-0%d"
    rows = [("000001.SZ", "a", base % 1, "2026-11-01 10:00:00"),
            ("000002.SZ", "b", base % 2, "2026-11-02 10:00:00"),
            ("000003.SZ", "c", base % 3, "2026-11-03 10:00:00")]
    _monkey_info_db(monkeypatch, _make_db(tmp_path, rows))
    got = tdx.read_info("stock_news", limit=10, since="20261101", until="20261102")
    assert {r["title"] for r in got} == {"a", "b"}


def test_after_cursor_paginates_tie_groups_without_loss(tmp_path, monkeypatch):
    """10 行同秒并列 + 5 行下一秒，页宽 3：分页走完 15/15 无重无漏。"""
    base = "2026-11-06"
    rows = [("0000%02d.SZ" % i, "t%02d" % i, base, base + " 10:00:00")
            for i in range(10)]
    rows += [("0001%02d.SZ" % i, "u%02d" % i, base, base + " 10:00:01")
             for i in range(5)]
    _monkey_info_db(monkeypatch, _make_db(tmp_path, rows))
    seen, cur, pages = [], {"date": "20261105", "sub": "", "rid": 0}, 0
    while pages < 20:
        got = tdx.read_info("stock_news", limit=3, since="20261101", after=cur)
        if not got:
            break
        seen.extend(r["title"] for r in got)
        last = got[-1]
        cur = {"date": "20261106", "sub": last["_fetched_at"],
               "rid": last["_src_rowid"]}
        pages += 1
    assert len(seen) == 15 and len(set(seen)) == 15


def test_after_cursor_null_fetched_at_no_loss(tmp_path, monkeypatch):
    base = "2026-11-06"
    rows = [("000001.SZ", "n0", base, None),
            ("000002.SZ", "t1", base, base + " 10:00:00"),
            ("000003.SZ", "t2", base, base + " 10:00:00")]
    _monkey_info_db(monkeypatch, _make_db(tmp_path, rows))
    seen, cur, pages = [], {"date": "20261105", "sub": "", "rid": 0}, 0
    while pages < 10:
        got = tdx.read_info("stock_news", limit=1, since="20261101", after=cur)
        if not got:
            break
        seen.extend(r["title"] for r in got)
        last = got[-1]
        cur = {"date": "20261106", "sub": last.get("_fetched_at") or "",
               "rid": last["_src_rowid"]}
        pages += 1
    assert sorted(seen) == ["n0", "t1", "t2"]


def test_real_info_db_default_regression():
    """真库只读回归：默认路径仍可读、DESC 序（不写库）。"""
    if not os.path.exists(tdx.INFO_DB):
        return
    got = tdx.read_info("stock_news", limit=3)
    assert len(got) <= 3
    if len(got) == 3:
        d = [r["pub_date"] for r in got]
        assert d == sorted(d, reverse=True)
