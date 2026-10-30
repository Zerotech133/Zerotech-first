# -*- coding: utf-8 -*-
"""P0R 事件研究/组合回测 单元测试（合成数据）。"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import event_backtest as eb  # noqa: E402


def synth():
    # 300 个连续交易日（2023-01-01 起，跨月）
    dates = pd.bdate_range("2023-01-02", periods=300).strftime("%Y%m%d").tolist()
    rng = np.random.default_rng(1)
    codes = ["A.SZ", "B.SZ", "C.SZ"]
    pix = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, size=(len(dates), len(codes))), axis=0)
    piv = pd.DataFrame(pix, index=dates, columns=codes)
    bench = np.cumprod(1 + rng.normal(0.0002, 0.01, size=len(dates)))
    events = pd.DataFrame([
        {"ts_code": "A.SZ", "ann_date": dates[100], "category": "回购", "title": "回购进展公告"},
        {"ts_code": "B.SZ", "ann_date": dates[150], "category": "业绩", "title": "年度报告"},
        {"ts_code": "C.SZ", "ann_date": dates[200], "category": "诉讼", "title": "诉讼进展公告"},
    ])
    return dates, codes, piv, bench, events


class TestEventStudy(unittest.TestCase):
    def test_event_study_returns_excess(self):
        dates, codes, piv, bench, events = synth()
        dates_int = np.asarray([int(x) for x in dates], dtype=np.int64)
        ev, agg = eb.event_study(events, piv, bench, dates, dates_int, piv.to_numpy())
        self.assertIn("ex20", ev.columns)
        self.assertIn("period", ev.columns)
        self.assertGreater(len(ev), 0)
        r = agg(ev[ev["category"] == "回购"])
        self.assertGreater(r["n"], 0)
        self.assertIn("t20", r)

    def test_portfolio_keys(self):
        dates, codes, piv, bench, events = synth()
        hist_pct = pd.DataFrame(50.0, index=dates, columns=codes)
        out = eb.portfolio(events, piv, bench, dates, hist_pct)
        for k in ("event_only", "cheap_only", "event_cheap", "baseline"):
            self.assertIn(k, out)
            self.assertIn("train", out[k])
            self.assertIn("val", out[k])


if __name__ == "__main__":
    unittest.main(verbosity=2)
