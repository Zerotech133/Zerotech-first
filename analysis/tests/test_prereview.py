# -*- coding: utf-8 -*-
"""事件 AI 预审清单测试（临时 SQLite，不碰生产库；只读建议，不写复核状态）。"""
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import gates, prereview, review  # noqa: E402


class TestPrereview(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "t.db")
        con = review.connect(self.db)
        rows = [
            ("e1", "重整", "关于与财务投资人签署《重整投资协议》的公告"),
            ("e2", "退市/ST", "关于股份回购进展公告"),
            ("e3", "其他", "董事会审计委员会实施细则"),
            ("e4", "业绩", "2026年半年度报告"),
        ]
        for i, (eid, cat, title) in enumerate(rows):
            con.execute("INSERT INTO events(event_id,code,date,category,title,url,text_len,confidence,reviewed,facts)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (eid, "600337.SH", "2026-10-0%d" % (i + 1), cat, title, "http://x/%s" % eid, 10, 0.8, 0, "{}"))
        con.commit()
        con.close()

    def tearDown(self):
        self.td.cleanup()

    def test_suggest(self):
        self.assertEqual(prereview.suggest("关于与财务投资人签署《重整投资协议》的公告")[0], "重整")
        self.assertEqual(prereview.suggest("关于股份回购进展公告")[0], "回购")
        cat, forced, _ = prereview.suggest("董事会审计委员会实施细则")
        self.assertEqual(cat, "治理/程序")
        self.assertEqual(forced, "reject")

    def test_build_actions(self):
        rows = prereview.build(["600337.SH"], path=self.db)
        self.assertEqual(len(rows), 4)
        acts = {r["event_id"]: r["action"] for r in rows}
        self.assertEqual(acts["e2"], "reclassify")   # 退市/ST → 回购
        self.assertEqual(acts["e3"], "reject")       # 程序性
        self.assertEqual(acts["e4"], "approve")      # 业绩 → 业绩

    def test_outputs_and_no_db_write(self):
        rows = prereview.build(["600337.SH"], path=self.db)
        out = prereview.write_outputs(rows, self.td.name)
        self.assertTrue(os.path.exists(out["csv"]))
        self.assertTrue(os.path.exists(out["md"]))
        # 预审不改变复核状态
        st = review.stats(code="600337.SH", path=self.db)
        self.assertEqual(st["pending"], 4)
        self.assertEqual(st["approved"], 0)

    def test_g1_coverage_target_warn(self):
        snap = {"required_missing": [], "coverage": 85.0,
                "domains": {"chips": {}, "margin": {}}, "last_trade_date": "2026-10-23"}
        g = gates.g1_data(snap)
        self.assertTrue(g["passed"], "85% 应过硬门槛，仅告警")
        self.assertTrue(any("目标" in i for i in g["issues"]))
        snap["coverage"] = 70.0
        g2 = gates.g1_data(snap)
        self.assertFalse(g2["passed"])
        self.assertTrue(any("硬门槛" in i for i in g2["issues"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
