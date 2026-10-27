# -*- coding: utf-8 -*-
"""jev_attribution 单测（临时 sqlite 夹具；aw_decision 走外部不测）。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.jev_attribution as ja  # noqa: E402


class TestJevAttribution(unittest.TestCase):
    def _make_db(self, td: str) -> Path:
        p = Path(td) / "audit.db"
        con = sqlite3.connect(str(p))
        con.execute("""CREATE TABLE dec_audit (
            seq INTEGER PRIMARY KEY, news_id TEXT, ts TEXT, trade_day TEXT, source TEXT,
            bias TEXT, conf REAL, event_type TEXT, companies TEXT, model TEXT,
            prompt_version TEXT, ret REAL, bench REAL, excess REAL, hit INTEGER,
            posterior_at TEXT, updated_at TEXT)""")
        rows = [
            ("bullish", "earnings", "eastmoney", 0.03, 0.02, 1),
            ("bullish", "earnings", "sina", -0.01, -0.02, 0),
            ("bearish", "policy", "eastmoney", -0.02, -0.01, 1),
            ("neutral", "other", "sina", 0.005, 0.0, 1),
            ("bullish", "earnings", "eastmoney", None, None, None),
        ]
        for b, e, s, ret, exc, hit in rows:
            con.execute("INSERT INTO dec_audit (bias,event_type,source,ret,excess,hit) "
                        "VALUES (?,?,?,?,?,?)", (b, e, s, ret, exc, hit))
        con.commit()
        con.close()
        return p

    def test_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            res = ja.audit_attribution(db)
            self.assertTrue(res["ok"])
            self.assertEqual(res["total_rows"], 5)
            self.assertEqual(res["labeled_rows"], 4)
            bias = {r["key"]: r for r in res["by_bias"]}
            self.assertEqual(bias["bullish"]["n"], 2)  # 全 None 行被 WHERE 过滤
            self.assertAlmostEqual(bias["bullish"]["ret"]["mean"], 0.01)
            self.assertEqual(bias["bullish"]["hit_rate"], 0.5)

    def test_missing_db(self):
        res = ja.audit_attribution(Path(r"C:\definitely\missing.db"))
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
