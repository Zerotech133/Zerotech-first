# -*- coding: utf-8 -*-
"""P0M：行业映射纠偏 / 估值历史分位 / 数据时效门禁 测试。"""
import os
import sys
import unittest

import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import gates, snapshot, verdict  # noqa: E402


def make_cls():
    return pd.DataFrame([
        {"index_code": "801770.SI", "industry_name": "通信", "level": "L1", "parent_code": "0"},
        {"index_code": "801102.SI", "industry_name": "通信设备", "level": "L2", "parent_code": "801770.SI"},
        {"index_code": "801730.SI", "industry_name": "电力设备", "level": "L1", "parent_code": "0"},
    ])


def make_mem():
    return pd.DataFrame([
        {"con_code": "300308.SZ", "index_code": "801730.SI", "is_new": "Y"},
        {"con_code": "000001.SZ", "index_code": "801730.SI", "is_new": "Y"},
    ])


class TestIndustryMap(unittest.TestCase):
    def test_basic_name_overrides_stale_member(self):
        m = snapshot._map_industry("300308.SZ", "通信设备", make_mem(), make_cls())
        self.assertEqual(m["l1_name"], "通信")
        self.assertEqual(m["match"], "stock_basic")

    def test_fallback_to_member(self):
        m = snapshot._map_industry("300308.SZ", "机械基件", make_mem(), make_cls())
        self.assertEqual(m["l1_name"], "电力设备")
        self.assertEqual(m["match"], "sw_l1_member")

    def test_unknown_returns_empty(self):
        m = snapshot._map_industry("999999.SZ", None, make_mem(), make_cls())
        self.assertEqual(m, {})


def make_feat(hist=None, lag=None):
    f = {
        "technical": {"price": 50.0, "ma20": 48.0, "ma60": 49.0, "pct_rank_250d": 40.0, "levels": []},
        "valuation": {"pe_ttm": 30.0, "pb": 3.0, "ps_ttm": 6.0, "peer_percentile": 50.0},
        "fundamental": {"net_profit_latest_yi": 1.0, "revenue_yoy": 5.0, "net_profit_yoy": 5.0,
                        "gross_margin": 25.0, "latest_end": "20260630"},
        "moneyflow": {}, "chips": {}, "extras": {},
    }
    if hist is not None:
        f["valuation"]["ps_pct_hist"] = hist
    snap = {"as_of": "2026-10-29", "name": "测试", "calendar": [], "industry": {}, "domains": {},
            "last_trade_date": "2026-10-29"}
    if lag is not None:
        snap["data_lag"] = {"trade_days": lag, "last_trade_date": "2026-10-29", "today": "2026-10-30"}
    return snap, f


class TestValuationHistory(unittest.TestCase):
    def test_low_history_boost_high_history_penalty(self):
        snap, f_low = make_feat(hist=10)
        _, f_high = make_feat(hist=95)
        a = verdict.build_verdict(snap, f_low, [])
        b = verdict.build_verdict(snap, f_high, [])
        self.assertGreater(a["score"], b["score"])
        self.assertTrue(any("近 5 年低位" in r for r in a["reasons"]), a["reasons"])
        self.assertTrue(any("近 5 年高位" in r for r in b["risks"]), b["risks"])
        self.assertTrue(any("PS 历史分位" in s for s in a["key_signals"]), a["key_signals"])


class TestDataLag(unittest.TestCase):
    def test_g1_lag_warning_does_not_fail(self):
        snap = {"required_missing": [], "coverage": 95.0, "domains": {}, "last_trade_date": "2026-10-23",
                "data_lag": {"trade_days": 2, "last_trade_date": "2026-10-23", "today": "2026-10-30"}}
        g = gates.g1_data(snap)
        self.assertTrue(g["passed"])
        self.assertTrue(any("落后 2 个交易日" in i for i in g["issues"]), g["issues"])

    def test_lag_caps_confidence(self):
        snap, f = make_feat(lag=3)
        v = verdict.build_verdict(snap, f, [])
        self.assertLessEqual(v["confidence"], 0.5)
        self.assertTrue(any("数据时效差" in r for r in v["risks"]), v["risks"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
