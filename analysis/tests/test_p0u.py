# -*- coding: utf-8 -*-
"""P0U：事件旧类别清洗测试（临时 DB，不碰生产）。"""
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import reclass  # noqa: E402


class TestReclass(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.td.name, "t.db")
        c = reclass._connect(self.db)
        rows = [
            ("e1", "600337.SH", "2026-09-29", "退市/ST", "关于与财务投资人签署《重整投资协议》的公告", 1),
            ("e2", "600337.SH", "2026-08-31", "其他", "2026年半年度报告", 1),
            ("e3", "603667.SH", "2026-10-28", "业绩", "2026年第三季度报告", 0),
        ]
        for eid, code, date, cat, title, rev in rows:
            c.execute("INSERT INTO events(event_id,code,date,category,title,url,text_len,confidence,reviewed,facts)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (eid, code, date, cat, title, "", 10, 0.8, rev, "{}"))
        c.commit()
        c.close()

    def tearDown(self):
        self.td.cleanup()

    def test_plan_detects_stale(self):
        diffs = reclass.plan(path=self.db)
        m = {d["event_id"]: d for d in diffs}
        self.assertIn("e1", m)              # 退市/ST → 重整
        self.assertEqual(m["e1"]["new"], "重整")
        self.assertIn("e2", m)              # 其他 → 业绩（当前规则）
        self.assertEqual(m["e2"]["new"], "业绩")

    def test_apply_updates_category_keeps_reviewed(self):
        diffs = reclass.plan(path=self.db)
        r = reclass.apply(diffs, path=self.db, backup=False)
        self.assertEqual(r["updated"], len(diffs))
        import sqlite3
        c = sqlite3.connect(self.db)
        cat, rev = c.execute("SELECT category, reviewed FROM events WHERE event_id='e1'").fetchone()
        c.close()
        self.assertEqual(cat, "重整")
        self.assertEqual(rev, 1)            # 人工复核状态保留
        self.assertEqual(reclass.plan(path=self.db), [])  # 已一致，无剩余差异


if __name__ == "__main__":
    unittest.main(verbosity=2)
