# -*- coding: utf-8 -*-
"""P0T：龙虎榜席位净额单位修复测试（元→万元）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import extras  # noqa: E402


def fake_load_daily(name):
    if name == "top_list":
        return pd.DataFrame([
            {"trade_date": "20260827", "ts_code": "600337.SH", "net_amount": 3203628.97},
        ])
    if name == "top_inst":
        return pd.DataFrame([
            {"trade_date": "20260827", "ts_code": "600337.SH", "exalter": "A", "buy": 16794351.0,
             "sell": 13590722.03, "net_buy": 3203628.97, "side": "0"},
        ])
    return None


def fake_load_code(name, code):
    return None


class TestLhbUnit(unittest.TestCase):
    def test_seat_net_wan_is_wan(self):
        snap = {"code": "600337.SH", "as_of": "2026-10-29"}
        with mock.patch.object(extras.sync, "load_daily", side_effect=fake_load_daily), \
             mock.patch.object(extras.sync, "load_code", side_effect=fake_load_code):
            out = extras.build(snap)
        lhb = out.get("lhb") or {}
        self.assertEqual(lhb.get("count_90d"), 1)
        # 3,203,628.97 元 = 320.4 万元（修复前会显示 3203628.97 "万"）
        self.assertAlmostEqual(lhb.get("seat_net_wan"), 320.4, places=1)

    def test_no_top_inst_fallback(self):
        def only_tl(name):
            if name == "top_list":
                return pd.DataFrame([{"trade_date": "20260827", "ts_code": "600337.SH",
                                      "net_amount": 3203628.97}])
            return None
        snap = {"code": "600337.SH", "as_of": "2026-10-29"}
        with mock.patch.object(extras.sync, "load_daily", side_effect=only_tl), \
             mock.patch.object(extras.sync, "load_code", side_effect=fake_load_code):
            out = extras.build(snap)
        # top_inst 缺失 → 不写 seat_net_wan（保持 None 语义）
        self.assertIsNone((out.get("lhb") or {}).get("seat_net_wan"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
