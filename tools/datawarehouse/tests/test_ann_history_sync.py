# -*- coding: utf-8 -*-
"""ann_history_sync 纯本地单测（不联网、不碰生产库）。"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ann_history_sync as m  # noqa: E402


def _mkdb():
    con = sqlite3.connect(":memory:")
    con.executescript("""
CREATE TABLE ann (item_id TEXT PRIMARY KEY, art_code TEXT NOT NULL, title TEXT,
                  notice_date TEXT, columns_txt TEXT, source_type TEXT, fetched_at TEXT);
CREATE TABLE ann_stock (art_code TEXT, stock_code TEXT, short_name TEXT, ann_type TEXT,
                  PRIMARY KEY (art_code, stock_code));
CREATE TABLE stock_watermark (ts_code TEXT, done INTEGER, n_ann INTEGER, finished_at TEXT);
""")
    return con


def _payload(items, page_size=100):
    return {"data": {"list": items, "page_size": page_size}}


class TestParsePage(unittest.TestCase):
    def test_keeps_recent_and_signals_old(self):
        items = [
            {"art_code": "AN1", "notice_date": "2026-10-20 00:00:00", "title": "A"},
            {"art_code": "AN2", "notice_date": "2026-09-01 00:00:00", "title": "B"},
        ]
        keep, done = m.parse_page(_payload(items), "2026-09-15")
        self.assertEqual([x["art_code"] for x in keep], ["AN1"])
        self.assertTrue(done)

    def test_normalizes_rows_and_pairs(self):
        items = [{"art_code": "AN9", "notice_date": "2026-10-26 00:00:00", "title": "T",
                  "columns": [{"column_name": "临时公告"}], "source_type": "331",
                  "codes": [{"stock_code": "000001", "short_name": "平安", "ann_type": "A,SZA"}]}]
        rows, pairs = m.norm_rows(items)
        self.assertEqual(rows[0][0], "ann:AN9")
        self.assertEqual(rows[0][3], "2026-10-26")
        self.assertEqual(pairs, [("AN9", "000001", "平安", "A,SZA")])

    def test_upsert_is_idempotent(self):
        con = _mkdb()
        items = [{"art_code": "AN7", "notice_date": "2026-10-26 00:00:00", "title": "T",
                  "codes": [{"stock_code": "600000", "short_name": "浦发", "ann_type": "A"}]}]
        rows, pairs = m.norm_rows(items)
        n1, _ = m.upsert(con, rows, pairs)
        n2, _ = m.upsert(con, rows, pairs)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        self.assertEqual(con.execute("select count(*) from ann").fetchone()[0], 1)

    def test_since_filter_keeps_boundary(self):
        items = [{"art_code": "AN3", "notice_date": "2026-09-15 00:00:00"}]
        keep, _ = m.parse_page(_payload(items), "2026-09-15")
        self.assertEqual(len(keep), 1)


if __name__ == "__main__":
    unittest.main()
