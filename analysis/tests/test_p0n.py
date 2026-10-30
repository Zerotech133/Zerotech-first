# -*- coding: utf-8 -*-
"""P0N：五维 100 分制打分 v2 测试。"""
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import config, scoring  # noqa: E402


def feat_good():
    return {
        "technical": {"price": 60.0, "ma20": 50.0, "ma60": 48.0, "pct_rank_250d": 80.0,
                      "ret_20d": 12.0, "levels": []},
        "valuation": {"peer_percentile": 20.0, "ps_pct_hist": 18.0, "hist_temp": 22.0},
        "fundamental": {"roe": 22.0, "gross_margin": 45.0, "ocf_annual_yi": 10.0,
                        "net_profit_latest_yi": 5.0, "debt_ratio": 30.0,
                        "revenue_yoy": 35.0, "net_profit_yoy": 60.0, "latest_end": "20260630"},
        "moneyflow": {"main_net_20d_wan": 12000},
        "extras": {"forecast": {"type": "预增"}},
    }


def feat_bad():
    return {
        "technical": {"price": 40.0, "ma20": 50.0, "ma60": 55.0, "pct_rank_250d": 10.0,
                      "ret_20d": -15.0, "levels": []},
        "valuation": {"peer_percentile": 90.0, "ps_pct_hist": 92.0, "hist_temp": 90.0},
        "fundamental": {"roe": 2.0, "gross_margin": 8.0, "ocf_annual_yi": 0.1,
                        "net_profit_latest_yi": 1.0, "debt_ratio": 78.0,
                        "revenue_yoy": -20.0, "net_profit_yoy": -35.0, "latest_end": "20260630"},
        "moneyflow": {"main_net_20d_wan": -20000},
        "extras": {"forecast": {"type": "预亏"}},
    }


class TestScoring(unittest.TestCase):
    def test_weights_sum_one(self):
        self.assertAlmostEqual(sum(config.SCORE_WEIGHTS.values()), 1.0, places=6)

    def test_good_beats_bad(self):
        a = scoring.score_v2({}, feat_good())
        b = scoring.score_v2({}, feat_bad())
        self.assertGreater(a["total"], b["total"] + 20)
        self.assertGreaterEqual(a["factors"]["quality"], 80)
        self.assertLessEqual(b["factors"]["quality"], 40)
        self.assertEqual(a["rating"], "买入")
        self.assertEqual(b["rating"], "回避")

    def test_nonrecurring_penalty(self):
        f = feat_good()
        plain_q = scoring.quality_score(f["fundamental"])
        f["fundamental"]["nonrecurring"] = {"ratio": 0.9, "dt_yoy": -20.0}
        self.assertEqual(scoring.quality_score(f["fundamental"]), max(0, plain_q - 20))
        # 成长用扣非同比（负增长）→ 成长分下降
        self.assertLess(scoring.growth_score(f["fundamental"], f["extras"]), 55)

    def test_event_override(self):
        r, h = scoring.rating_from_score(95.0, tags=["ST"])
        self.assertEqual(r, "买入")  # rating_from_score 只按分数；ST 覆盖在 verdict
        self.assertGreaterEqual(scoring.event_score([], ["ST"], {}), 0)
        self.assertLess(scoring.event_score([], ["ST"], {}), 50)

    def test_rating_cuts(self):
        cuts = config.SCORE_CUTS
        self.assertEqual(scoring.rating_from_score(cuts["buy"] + 1)[0], "买入")
        self.assertEqual(scoring.rating_from_score(cuts["observe_up"])[0], "观察偏多")
        self.assertEqual(scoring.rating_from_score(cuts["observe"])[0], "观察")
        self.assertEqual(scoring.rating_from_score(cuts["avoid"] - 5)[0], "回避")


if __name__ == "__main__":
    unittest.main(verbosity=2)
