# -*- coding: utf-8 -*-
"""P0R-2 事件精选组合 单元测试（合成数据）。"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import event_v2  # noqa: E402


def synth():
    dates = pd.bdate_range("2023-01-02", periods=520).strftime("%Y%m%d").tolist()
    rng = np.random.default_rng(7)
    codes = [f"S{i:02d}.SZ" for i in range(30)]
    pix = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, size=(len(dates), len(codes))), axis=0)
    piv = pd.DataFrame(pix, index=dates, columns=codes)
    ev_rows = []
    for i in range(10):
        ev_rows.append({"ts_code": "S%02d.SZ" % i, "ann_date": dates[200],
                        "category": "股权激励", "title": "股权激励计划"})
        ev_rows.append({"ts_code": "S%02d.SZ" % i, "ann_date": dates[300],
                        "category": "重组并购", "title": "重大资产重组"})
        ev_rows.append({"ts_code": "S%02d.SZ" % i, "ann_date": dates[400],
                        "category": "经营/订单", "title": "重大合同公告"})
    ev = pd.DataFrame(ev_rows)
    db = pd.DataFrame([{"ts_code": c, "trade_date": d, "total_mv": 1e6}
                       for d in dates for c in codes])
    imap = {c: "L1" for c in codes}
    names = {c: "测试" for c in codes}
    return codes, piv, dates, ev, db, imap, names


class TestEventV2(unittest.TestCase):
    def test_seg_of(self):
        self.assertEqual(event_v2.seg_of("20200105"), "s1")
        self.assertEqual(event_v2.seg_of("20230105"), "s2")
        self.assertEqual(event_v2.seg_of("20250105"), "val")
        self.assertIsNone(event_v2.seg_of("20270101"))

    def test_run_shape(self):
        codes, piv, dates, ev, db, imap, names = synth()
        res = event_v2.run(codes, piv, dates, ev, db, imap, names)
        self.assertIn("segments", res)
        self.assertIn("by_type", res)
        self.assertIn("verdict", res)
        for seg in ("s1", "s2", "val"):
            self.assertIn(seg, res["segments"])
        self.assertIn("passed", res["verdict"])
        self.assertEqual(res["cost"], 0.002)


if __name__ == "__main__":
    unittest.main(verbosity=2)
