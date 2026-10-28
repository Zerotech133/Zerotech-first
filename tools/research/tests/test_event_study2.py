# -*- coding: utf-8 -*-
"""event_study2 纯函数单测（行业中性映射 / stats / 分类调用）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.event_study2 as es  # noqa: E402


class TestEventStudy2(unittest.TestCase):
    def test_neutral_maps_industry(self):
        dates = ["20260101"]
        rets = {"20260101": {}}
        industry = {"000001.SZ": "银行", "600000.SH": "银行", "000002.SZ": "地产"}
        for i in range(12):
            code = "6000%02d.SH" % i
            industry[code] = "银行"
            rets["20260101"][code] = 2.0 + i * 0.1
        rets["20260101"]["000002.SZ"] = -5.0
        industry["000001.SZ"] = "银行"
        rets["20260101"]["000001.SZ"] = 1.0
        market = {"20260101": 1.5}
        neutral = es.neutral_maps(dates, rets, market, industry, min_group=5)
        self.assertIn("600000.SH", neutral["20260101"])
        # 地产组只有 1 只（<min_group）→ 回落市场均值
        self.assertAlmostEqual(neutral["20260101"]["000002.SZ"], 1.5)
        self.assertIn("银行", set(industry.values()))

    def test_stats(self):
        s = es.stats([1.0, 2.0, 3.0])
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["mean"], 2.0)
        self.assertAlmostEqual(s["win_rate"], 1.0)

    def test_stats_insufficient(self):
        self.assertEqual(es.stats([1.0])["n"], 1)
        self.assertEqual(es.stats([])["n"], 0)

    def test_code_to_ts(self):
        self.assertEqual(es.code_to_ts("600519"), "600519.SH")
        self.assertEqual(es.code_to_ts("000001"), "000001.SZ")
        self.assertEqual(es.code_to_ts("830799"), "830799.BJ")


if __name__ == "__main__":
    unittest.main()
