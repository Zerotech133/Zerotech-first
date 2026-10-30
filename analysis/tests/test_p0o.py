# -*- coding: utf-8 -*-
"""P0O：评级回测统计/输出 单元测试（合成数据，不读仓库）。"""
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import backtest  # noqa: E402


def synth():
    rng = np.random.default_rng(0)
    rows = []
    for m in pd.date_range("2020-01-31", periods=6, freq="ME"):
        dt = m.strftime("%Y%m%d")
        for i in range(40):
            code = "S%03d" % i
            total = rng.uniform(20, 90)
            ex20 = (total - 50) / 1000 + rng.normal(0, 0.02)
            rows.append({"date": dt, "code": code, "total": total,
                         "rating": "买入" if total >= 82 else ("观察" if total >= 50 else "回避"),
                         "quality": total, "growth": total, "valuation": total, "trend": total,
                         "fwd20": ex20 + 0.01, "fwd60": ex20, "b20": 0.01, "b60": 0.01})
    df = pd.DataFrame(rows)
    df["ex20"] = df["fwd20"] - df["b20"]
    df["ex60"] = df["fwd60"] - df["b60"]
    return df


class TestBacktest(unittest.TestCase):
    def test_analyze_shape(self):
        res = backtest.analyze(synth())
        self.assertIn("summary", res)
        self.assertIn("by_rating", res)
        self.assertIn("by_quantile", res)
        s = res["summary"]
        self.assertEqual(s["dates"], 6)
        self.assertIsNotNone(s["ic_mean"])
        for k in ("buy", "observe_up", "observe", "avoid"):
            self.assertIn(k, s["suggested_cuts"])

    def test_write_out(self):
        with tempfile.TemporaryDirectory() as td:
            res = backtest.analyze(synth())
            out = backtest.write_out(res, synth(), td)
            self.assertTrue(os.path.exists(out["json"]))
            self.assertTrue(os.path.exists(out["md"]))
            md = open(out["md"], encoding="utf-8").read()
            self.assertIn("按当月分位映射", md)
            self.assertIn("建议分档", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
