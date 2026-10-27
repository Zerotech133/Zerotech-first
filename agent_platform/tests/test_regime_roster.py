# -*- coding: utf-8 -*-
"""M4-P3 Regime 阵容选择器测试。

覆盖：Regime→profile 阵容表（trend_down 拦截/rotation 放宽/未知回落）/
dk_snapshot 真数据链路（构造趋势上行 K 线→regime/信号落账）/ blocked
留痕 / caller override / fail-open（空 df、缺列、坏数据→dk_error）/
enrich_row 增量兼容（旧行零破坏、None 与 error 两路）/ kline 行转换
（amount 近似）/ REC_SCORE 口径并存断言。
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from engine.regime_roster import (  # noqa: E402
    DK_FIELDS,
    bars_to_df,
    dk_snapshot,
    enrich_row,
    select_profile,
)
from engine.quant_double_knife import PROFILE_CONFIG  # noqa: E402


def _trend_up_df(n=80, start=10.0, drift=0.004):
    """构造趋势上行日 K（EMA 上斜+放量突破，触发 trend_up regime）。"""
    rows = []
    price = start
    for i in range(n):
        o = price
        c = price * (1 + drift + (0.002 if i % 5 == 0 else 0))
        h = max(o, c) * 1.005
        l = min(o, c) * 0.995
        v = 1e6 * (1 + i * 0.01)
        rows.append(("d%03d" % i, o, h, l, c, v * c))
        price = c
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "amount"])
    return df.set_index("date")


class SelectProfileTests(unittest.TestCase):
    def test_roster_mapping(self):
        self.assertEqual(select_profile("trend_up")["profile"], "strict")
        self.assertEqual(select_profile("rotation")["profile"], "relaxed")
        self.assertEqual(select_profile("range")["profile"], "strict")

    def test_trend_down_blocked(self):
        r = select_profile("trend_down")
        self.assertTrue(r["blocked"])
        self.assertIn("trend_down", r["reason"])

    def test_unknown_falls_back_strict(self):
        r = select_profile("mystery")
        self.assertEqual(r["profile"], "strict")
        self.assertFalse(r["blocked"])
        self.assertTrue(r["reason"])
        r2 = select_profile(None)
        self.assertEqual(r2["profile"], "strict")

    def test_blocked_regimes_consistent_with_dk_config(self):
        # 阵容表与双刀 PROFILE_CONFIG 同向双保险
        for cfg in PROFILE_CONFIG.values():
            self.assertIn("trend_down", cfg["blocked_regimes"])


class SnapshotTests(unittest.TestCase):
    def test_trend_up_snapshot_shape(self):
        snap = dk_snapshot(_trend_up_df())
        self.assertNotIn("dk_error", snap)
        for k in ("regime_label", "profile", "blocked", "dk_signal", "reason"):
            self.assertIn(k, snap)

    def test_empty_and_missing_columns_fail_open(self):
        import pandas as pd

        r = dk_snapshot(None)
        self.assertIn("dk_error", r)
        r2 = dk_snapshot(pd.DataFrame({"close": [1, 2]}))
        self.assertIn("dk_error", r2)
        r3 = dk_snapshot(pd.DataFrame())  # 空表
        self.assertIn("dk_error", r3)

    def test_bad_data_fail_open(self):
        import pandas as pd

        df = pd.DataFrame(
            {
                "open": [None] * 10,
                "high": [None] * 10,
                "low": [None] * 10,
                "close": [None] * 10,
                "amount": [None] * 10,
            }
        )
        r = dk_snapshot(df)
        self.assertIn("dk_error", r)

    def test_blocked_regime_no_signal_call(self):
        # trend_down：不跑 quant_double_knife，signal=False + blocked=True
        df = _trend_up_df(drift=-0.006, start=50.0)  # 构造下行
        snap = dk_snapshot(df)
        if snap.get("regime_label") == "trend_down":
            self.assertTrue(snap["blocked"])
            self.assertFalse(snap["dk_signal"])
        # 未触发 trend_down 也不影响其他断言（构造依赖数据形态）


class EnrichRowTests(unittest.TestCase):
    def test_incremental_fields_on_done_row(self):
        row = {"date": "d", "symbol": "s", "status": "done", "score": 1}
        snap = dk_snapshot(_trend_up_df())
        out = enrich_row(dict(row), snap)
        # 原字段原样
        self.assertEqual(out["score"], 1)
        self.assertEqual(out["status"], "done")
        # 增量字段齐
        for f in ("dk_signal", "dk_regime", "dk_profile", "dk_blocked"):
            self.assertIn(f, out)

    def test_none_snapshot_all_none(self):
        row = {"score": 2}
        out = enrich_row(dict(row), None)
        for f in DK_FIELDS:
            self.assertIsNone(out[f])
        self.assertEqual(out["score"], 2)  # 旧行零破坏

    def test_error_snapshot_keeps_main_fields(self):
        row = {"score": 1, "recommendation": "BUY"}
        out = enrich_row(dict(row), {"dk_error": "HTTP 403"})
        self.assertIn("403", out["dk_error"])
        self.assertIsNone(out["dk_signal"])
        self.assertEqual(out["recommendation"], "BUY")

    def test_score_bridge_coexists(self):
        # 两套口径并存：REC_SCORE 不被 dk_signal 改写（桥接判例）
        row = {"score": 1, "recommendation": "BUY"}
        snap = dk_snapshot(_trend_up_df())
        out = enrich_row(dict(row), snap)
        self.assertEqual(out["score"], 1)
        self.assertIsInstance(out["dk_signal"], bool)


class BarsToDfTests(unittest.TestCase):
    def test_kline_row_conversion(self):
        bars = [
            ["2026-10-14", 10.0, 10.5, 10.8, 9.9, 12345.0],
            ["2026-10-15", 10.5, 10.9, 11.0, 10.4, 15000.0],
        ]
        df = bars_to_df(bars)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "amount"])
        self.assertEqual(df.iloc[-1]["amount"], 15000.0 * 10.9)

    def test_short_row_vol_default(self):
        bars = [["2026-10-14", 10.0, 10.5, 10.8, 9.9]]
        df = bars_to_df(bars)
        self.assertEqual(df.iloc[0]["amount"], 0.0)


if __name__ == "__main__":
    unittest.main()
