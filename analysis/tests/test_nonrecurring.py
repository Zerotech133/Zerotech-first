# -*- coding: utf-8 -*-
"""P0L：非经常性损益识别测试（一次性收益不得抬高评级）。"""
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import verdict  # noqa: E402


def make_feat(nr=None):
    fnd = {
        "net_profit_latest_yi": 9.83, "revenue_latest_yi": 22.50,
        "revenue_yoy": -15.45, "net_profit_yoy": 898.01, "gross_margin": 19.1,
        "debt_ratio": 28.2, "roe": 24.9, "latest_end": "20260930",
    }
    if nr:
        fnd["nonrecurring"] = nr
    return {
        "technical": {"price": 53.11, "ma20": 46.77, "ma60": 50.15, "pct_rank_250d": 32.0,
                      "ret_20d": 9.8, "levels": []},
        "valuation": {"pe_ttm": 20.85, "pb": 4.15, "ps_ttm": 6.94, "peer_percentile": 55.0},
        "fundamental": fnd,
        "moneyflow": {"main_net_20d_wan": -9385.8, "main_net_5d_wan": -1953.9},
        "chips": {"winner_rate": 81.9},
        "extras": {"forecast": {"type": "预增", "summary": "预计净利 9.8 亿"}},
    }


SNAP = {"as_of": "2026-10-29", "name": "五洲新春", "calendar": [], "industry": {}, "domains": {}}

NR = {"extra_item_yi": 9.12, "ratio": 0.93, "profit_dedt_yi": 0.70, "dt_yoy": -22.9}


class TestNonRecurring(unittest.TestCase):
    def test_score_penalty_and_risk(self):
        plain = verdict.build_verdict(SNAP, make_feat(), [])
        flagged = verdict.build_verdict(SNAP, make_feat(NR), [])
        # -1.0 盈利加分抵消 + -0.3 预增给分被抑制
        self.assertAlmostEqual(plain["score"] - flagged["score"], 1.3, places=1)
        self.assertTrue(any("非经常性" in r or "扣非" in r for r in flagged["risks"]),
                        flagged["risks"])
        self.assertTrue(any("非经常性" in s for s in flagged["key_signals"]), flagged["key_signals"])
        self.assertIn("非经常性损益", flagged["thesis"])
        self.assertIsNotNone(flagged.get("nonrecurring"))
        self.assertEqual(flagged["nonrecurring"]["profit_dedt_yi"], 0.70)

    def test_forecast_bonus_gated(self):
        # 预增给分在有非经常性时被抑制
        plain = verdict.build_verdict(SNAP, make_feat(), [])
        flagged = verdict.build_verdict(SNAP, make_feat(NR), [])
        self.assertGreater(plain["score"], flagged["score"])

    def test_small_nr_no_penalty(self):
        small = {"extra_item_yi": 0.1, "ratio": 0.02, "profit_dedt_yi": 0.6, "dt_yoy": 5.0}
        a = verdict.build_verdict(SNAP, make_feat(), [])
        b = verdict.build_verdict(SNAP, make_feat(small), [])
        self.assertAlmostEqual(a["score"], b["score"], places=1)
        self.assertFalse(any("非经常性损益占比" in x for x in b["risks"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
