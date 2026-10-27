# -*- coding: utf-8 -*-
"""strategist.py 影子模式测试：打分边界与建议规则。"""

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from k_stock.paper import strategist


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE orders("
        " ts TEXT, source TEXT DEFAULT 'ai', strategy TEXT, stock TEXT,"
        " side TEXT, qty INTEGER, status TEXT, plan_date TEXT, fill_date TEXT,"
        " filled_price REAL, fee REAL, reason TEXT, conf REAL, ref TEXT,"
        " created_at TEXT)"
    )
    con.executemany(
        "INSERT INTO orders(strategy,status,reason,created_at) VALUES(?,?,?,?)", rows
    )
    con.commit()
    con.close()


def row(status, reason="", created="2026-10-11 10:00:00"):
    return ("ai_team", status, reason, created)


class TestStrategist(unittest.TestCase):
    def setUp(self):
        import tempfile

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.now = "2026-10-11 12:00:00"

    def tearDown(self):
        os.remove(self.db_path)

    def _review(self, rows, days=7):
        make_db(self.db_path, rows)
        return strategist.review(db_path=self.db_path, days=days, now=self.now)

    def test_no_orders_source_absent(self):
        """无单源不出现（而非满分）。"""
        report = self._review([])
        self.assertEqual(report["sources"], {})

    def test_all_filled_full_score(self):
        rows = [row("filled") for _ in range(10)]
        report = self._review(rows)
        s = report["sources"]["ai_team"]
        self.assertEqual(s["score"], 100.0)
        self.assertEqual(s["grade"], "加权重")

    def test_all_rejected_score_70(self):
        """10 单全 rejected：撮合率 0，扣 30 分。"""
        rows = [row("rejected") for _ in range(10)]
        report = self._review(rows)
        s = report["sources"]["ai_team"]
        self.assertEqual(s["metrics"]["match_rate"], 0.0)
        self.assertEqual(s["score"], 70.0)
        self.assertEqual(s["grade"], "维持")

    def test_stale_block_ratio(self):
        """一半单被陈旧产物拦截且 rejected：-30(新鲜) -30×0.5(撮合) -20×0.5(guard) = -40。"""
        rows = [row("rejected", "陈旧产物，拒绝执行") for _ in range(5)] + [
            row("filled") for _ in range(5)
        ]
        report = self._review(rows)
        self.assertEqual(report["sources"]["ai_team"]["score"], 60.0)
        self.assertEqual(report["sources"]["ai_team"]["grade"], "降权")

    def test_superseded_cancel_ratio(self):
        """三成 superseded 撤销：-20×0.3(撤销) -20×0.3(guard) = -12。"""
        rows = [row("cancelled", "superseded by new signal") for _ in range(3)] + [
            row("filled") for _ in range(7)
        ]
        report = self._review(rows)
        self.assertEqual(report["sources"]["ai_team"]["score"], 88.0)

    def test_guard_density(self):
        """两成涨停拦截：-20×0.2=4（涨停也算 guard 但不算撤销/陈旧）。"""
        rows = [row("cancelled", "涨停拦截，不追高") for _ in range(2)] + [
            row("filled") for _ in range(8)
        ]
        report = self._review(rows)
        self.assertEqual(report["sources"]["ai_team"]["score"], 96.0)

    def test_observation_grade(self):
        """全部陈旧拦截+全 rejected：-30(新鲜) -30(撮合) -20(guard含陈旧) = 20 → 观察。"""
        rows = [row("rejected", "陈旧产物") for _ in range(10)]
        report = self._review(rows)
        s = report["sources"]["ai_team"]
        self.assertEqual(s["score"], 20.0)
        self.assertEqual(s["grade"], "观察")

    def test_days_window_filters_old_orders(self):
        """窗口外旧单不计入。"""
        rows = [row("rejected", created="2026-09-01 10:00:00")]
        report = self._review(rows, days=7)
        self.assertEqual(report["sources"], {})

    def test_manual_excluded(self):
        rows = [
            row("rejected", created="2026-10-01 10:00:00"),
            ("manual", "filled", "", "2026-10-11 10:00:00"),
        ]
        make_db(self.db_path, rows)
        report = strategist.review(db_path=self.db_path, days=7, now=self.now)
        self.assertEqual(report["sources"], {})

    def test_shadow_declaration(self):
        report = self._review([row("filled")])
        self.assertEqual(report["mode"], "shadow")
        self.assertIn("不产生任何调度效果", report["note"])

    def test_save_report_atomic(self):
        import tempfile

        report = self._review([row("filled")])
        fd, out = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(out)
        try:
            path = strategist.save_report(report, out)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["sources"]["ai_team"]["score"], 100.0)
        finally:
            if os.path.exists(out):
                os.remove(out)


if __name__ == "__main__":
    unittest.main()
