# -*- coding: utf-8 -*-
"""P0 冒烟测试：schema、快照、特征、结论、质量门。"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(HERE))

from analysis import config, gates, snapshot, features, verdict  # noqa: E402


class TestSchema(unittest.TestCase):
    def test_schema_loads(self):
        s = json.load(open(config.SCHEMA_PATH, encoding="utf-8"))
        self.assertIn("properties", s)
        for k in ("meta", "snapshot", "features", "verdict", "sources", "checks"):
            self.assertIn(k, s["properties"])


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = snapshot.load_snapshot("600337.SH", "2026-10-23")
        cls.snap["events"] = []
        cls.feat = features.build_features(cls.snap)
        cls.vd = verdict.build_verdict(cls.snap, cls.feat, [])

    def test_snapshot(self):
        self.assertEqual(self.snap["name"], "ST美克")
        self.assertGreater(self.snap["coverage"], 60)
        self.assertEqual(self.snap["last_trade_date"], "20261023")
        self.assertIn("quote", self.snap["domains"])

    def test_features(self):
        self.assertAlmostEqual(self.feat["technical"]["price"], 3.56, places=2)
        self.assertIsNotNone(self.feat["valuation"].get("ps_ttm"))
        self.assertGreaterEqual(len(self.feat.get("peers") or []), 3)

    def test_verdict(self):
        self.assertIn(self.vd["rating"], config.RATINGS)
        self.assertTrue(0 <= self.vd["confidence"] <= 1)
        self.assertEqual(len(self.vd["scenarios"]), 4)
        self.assertIn("研究分析，不构成投资建议", self.vd["disclaimer"])

    def test_gate_g1(self):
        g = gates.g1_data(self.snap)
        self.assertIn("passed", g)
        self.assertGreaterEqual(g["score"], 0)

    def test_extras_optional(self):
        from analysis import extras
        ex = extras.build(self.snap)
        self.assertIsInstance(ex, dict)
        # 缓存不存在时允许全空；存在时应至少有一项
        self.assertTrue(all(v is None or isinstance(v, (dict, list)) for v in ex.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
