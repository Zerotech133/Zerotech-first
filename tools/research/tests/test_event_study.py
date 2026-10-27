# -*- coding: utf-8 -*-
"""event_study 纯函数单测（合成行情，不读真实数据）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.event_study as es  # noqa: E402


class TestEventStudy(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(es.classify_title("某公司:业绩预告"), "业绩预告")
        self.assertEqual(es.classify_title("某公司:关于股东减持股份的公告"), "增减持")
        self.assertEqual(es.classify_title("某公司:关于回购股份的方案"), "回购")
        self.assertEqual(es.classify_title("某公司:董事会决议公告"), "高管变动")
        self.assertEqual(es.classify_title("完全不相关标题"), "其他")

    def test_code_to_ts(self):
        self.assertEqual(es.code_to_ts("600519"), "600519.SH")
        self.assertEqual(es.code_to_ts("000001"), "000001.SZ")
        self.assertEqual(es.code_to_ts("300750"), "300750.SZ")
        self.assertEqual(es.code_to_ts("830799"), "830799.BJ")

    def test_stats(self):
        s = es.stats([1.0, 2.0, 3.0])
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["mean"], 2.0)
        self.assertAlmostEqual(s["median"], 2.0)
        self.assertAlmostEqual(s["win_rate"], 1.0)

    def test_compute_car(self):
        dates = ["20260101", "20260102", "20260103", "20260104"]
        rets = {
            "20260102": {"600000.SH": 2.0, "000001.SZ": 1.0},
            "20260103": {"600000.SH": 3.0, "000001.SZ": 0.0},
            "20260104": {"600000.SH": -1.0, "000001.SZ": 1.0},
        }
        market = {"20260102": 1.5, "20260103": 1.5, "20260104": 0.0}
        car = es.compute_event_car(1, "600000.SH", dates, rets, market, horizon=2)
        self.assertIsNotNone(car)
        self.assertAlmostEqual(car["ar0"], 0.5)
        self.assertAlmostEqual(car["car1"], 0.5 + 1.5)
        self.assertAlmostEqual(car["car5"], 0.5 + 1.5 + (-1.0))

    def test_compute_car_missing_data(self):
        dates = ["20260101", "20260102"]
        car = es.compute_event_car(1, "600000.SH", dates, {}, {}, horizon=5)
        self.assertIsNone(car)


if __name__ == "__main__":
    unittest.main()
