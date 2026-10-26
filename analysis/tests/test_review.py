# -*- coding: utf-8 -*-
"""事件复核队列测试（临时 SQLite，不碰生产库）。"""
import os
import sqlite3
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import review  # noqa: E402


class TestReview(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "t.db")
        con = review.connect(self.db)
        for i, (eid, cat, title) in enumerate([
            ("e1", "重整", "关于重整投资协议的公告"),
            ("e2", "减持/增持", "关于股东减持的公告"),
            ("e3", "业绩", "2026年半年度报告"),
        ]):
            con.execute("INSERT INTO events(event_id,code,date,category,title,url,text_len,confidence,reviewed,facts)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (eid, "600337.SH", "2026-10-0%d" % (i + 1), cat, title, "http://x/%s" % eid, 100, 0.8, 0, "{}"))
        con.commit()
        con.close()

    def tearDown(self):
        self.td.cleanup()

    def test_pending_and_stats(self):
        items = review.pending(code="600337.SH", path=self.db)
        self.assertEqual(len(items), 3)
        st = review.stats(code="600337.SH", path=self.db)
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["pending"], 3)

    def test_approve_with_category(self):
        r = review.apply("e1", "approve", category="重整", note="确认", path=self.db)
        self.assertTrue(r["ok"])
        self.assertEqual(r["reviewed"], 1)
        st = review.stats(code="600337.SH", path=self.db)
        self.assertEqual(st["approved"], 1)
        self.assertEqual(st["pending"], 2)
        items = review.list_events(code="600337.SH", status="approved", path=self.db)
        self.assertEqual(items[0]["category_final"], "重整")
        self.assertEqual(items[0]["reviewed_at"] is not None, True)

    def test_reject(self):
        review.apply("e2", "reject", note="误分类", path=self.db)
        st = review.stats(code="600337.SH", path=self.db)
        self.assertEqual(st["rejected"], 1)

    def test_missing_event(self):
        r = review.apply("nope", "approve", path=self.db)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
