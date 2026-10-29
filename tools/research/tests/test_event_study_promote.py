# -*- coding: utf-8 -*-
"""event_study v2（晋级三关）纯函数单测：市值分组中性 + 成本后净 CAR 口径。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.event_study as es  # noqa: E402


class TestPromoteFunctions(unittest.TestCase):
    def test_size_bucket_means(self):
        # 25 只股票，市值递增，收益随市值递减 → 小组均值应大于大组
        rets_day = {}
        mv_day = {}
        for i in range(25):
            code = "%06d.SZ" % i
            mv_day[code] = float(i + 1)
            rets_day[code] = 5.0 - i * 0.1  # 5.0..2.6
        means = es.size_bucket_means("20260101", rets_day, mv_day, market_mean=0.0)
        self.assertEqual(len(means), 25)
        small = means["%06d.SZ" % 0]
        big = means["%06d.SZ" % 24]
        self.assertGreater(small, big)

    def test_size_bucket_too_few(self):
        rets_day = {"000001.SZ": 1.0}
        mv_day = {"000001.SZ": 100.0}
        self.assertEqual(es.size_bucket_means("d", rets_day, mv_day, 0.0), {})

    def test_compute_neutral_car(self):
        dates = ["20260101", "20260102", "20260103"]
        rets = {
            "20260102": {"600000.SH": 2.0},
            "20260103": {"600000.SH": 1.0},
        }
        bm = {
            "20260102": {"600000.SH": 1.0},
            "20260103": {"600000.SH": 0.5},
        }
        car = es.compute_neutral_car(1, "600000.SH", dates, rets, {}, bm, horizon=1)
        self.assertIsNotNone(car)
        # (2-1) + (1-0.5) = 1.5
        self.assertAlmostEqual(car["car"], 1.5)

    def test_compute_neutral_car_missing_bucket(self):
        dates = ["20260101", "20260102"]
        rets = {"20260102": {"600000.SH": 1.0}}
        car = es.compute_neutral_car(1, "600000.SH", dates, rets, {}, {}, horizon=1)
        self.assertIsNone(car)


if __name__ == "__main__":
    unittest.main()
