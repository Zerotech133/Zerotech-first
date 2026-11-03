# -*- coding: utf-8 -*-
"""H2 NL→DSL 规则解析测试（10 例真实表达）。"""
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import screen_parse  # noqa: E402
from analysis import screener  # noqa: E402


def _fields(spec):
    return {f["field"]: (f["op"], f["value"]) for f in spec["filters"]}


class TestParse(unittest.TestCase):
    def test_full_sentence(self):
        r = screen_parse.parse("轻工制造里，PS 历史分位低于 50%、营收同比转正、剔除 ST，按质量分排序，最多 20 只")
        self.assertTrue(r["ok"], r)
        spec = r["spec"]
        f = _fields(spec)
        self.assertIn("industry_l1", f)
        self.assertEqual(f["ps_pct_hist"], ("<=", 50.0))
        self.assertEqual(f["revenue_yoy"], (">=", 0.0))
        self.assertEqual(f["flag_st"], ("=", False))
        self.assertEqual(spec["limit"], 20)
        self.assertEqual(spec["rank_by"][0]["field"], "quality_score")

    def test_hs300_quality(self):
        r = screen_parse.parse("沪深300 里 ROE 大于 10%、资产负债率低于 60%，按质量分排序，取前 10 只")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["spec"]["universe"], "HS300")
        f = _fields(r["spec"])
        self.assertAlmostEqual(f["roe"][1], 0.10)
        self.assertAlmostEqual(f["debt_ratio"][1], 0.60)
        self.assertEqual(r["spec"]["limit"], 10)

    def test_watchlist_drawdown(self):
        r = screen_parse.parse("观察池里近 20 日跌幅超过 15% 的")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["spec"]["universe"], "WATCHLIST")
        f = _fields(r["spec"])
        self.assertEqual(f["ret_20"][0], "<=")
        self.assertAlmostEqual(f["ret_20"][1], -0.15)

    def test_revenue_threshold(self):
        r = screen_parse.parse("A股 营收同比大于 20%、净利润同比转正")
        self.assertTrue(r["ok"], r)
        f = _fields(r["spec"])
        self.assertAlmostEqual(f["revenue_yoy"][1], 0.20)
        self.assertEqual(f["net_profit_yoy"], (">=", 0.0))

    def test_industry_only(self):
        r = screen_parse.parse("帮我看看机械设备行业的股票，剔除 ST")
        self.assertTrue(r["ok"], r)
        f = _fields(r["spec"])
        self.assertIn("industry_l1", f)
        self.assertEqual(f["industry_l1"][1], "机械设备")

    def test_ps_low_asc(self):
        r = screen_parse.parse("PS 分位低于 30%，按 PS 分位从低到高排序，最多 30 只")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["spec"]["rank_by"][0], {"field": "ps_pct_hist", "order": "asc"})
        self.assertEqual(r["spec"]["limit"], 30)

    def test_empty(self):
        r = screen_parse.parse("")
        self.assertFalse(r["ok"])

    def test_no_condition(self):
        r = screen_parse.parse("今天天气不错")
        self.assertFalse(r["ok"])

    def test_unmatched_recorded(self):
        r = screen_parse.parse("轻工制造 PS 分位低于 50%，另外帮我看看最近有什么新闻")
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("新闻" in u for u in r["unmatched"]))

    def test_parsed_spec_passes_screener_validate(self):
        r = screen_parse.parse("轻工制造里，PS 分位低于 50%、营收同比转正、剔除 ST，最多 20 只")
        self.assertTrue(r["ok"])
        v = screener.validate(r["spec"])
        self.assertTrue(v["ok"], v["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
