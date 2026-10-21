# -*- coding: utf-8 -*-
"""[WO-QT-20261021-099] 数据深度分档标注（depth_bars / depth_tier）。

依据：A 股同股不同窗口对照（400 只 × 12 时点 = 4800 点，88 个带信号）——
仅取最近 130 根 vs ≤500 根全窗，比较「新检出」集合：一致 61.4% /
短窗漏报 26.1% / 短窗虚报 12.5%，且虚报 11 例中 3卖 7、3买 4，
**几乎全集中在第三类买卖点**（中枢残缺导致）。

处置：只标注不拦截。pool 行增 depth_bars（可用根数）与 depth_tier
（deep ≥250 / shallow <250），供下游过滤与展示；拦截属交易口径须另批。

纪律：全部注入合成数据，不读写真实池、不跑 analyze。
运行：仓根 pytest quant_backtest/tests/test_depth_tier.py -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import chanlun_scan as cs  # noqa: E402

SIG = {"type": "3买", "dt": "2026-10-08", "price": 10.0}


class TestDepthTierBoundary(unittest.TestCase):
    def test_exactly_250_is_deep(self):
        self.assertEqual(cs._depth_tier(250), "deep")

    def test_249_is_shallow(self):
        self.assertEqual(cs._depth_tier(249), "shallow")

    def test_zero_is_shallow(self):
        self.assertEqual(cs._depth_tier(0), "shallow")

    def test_none_stays_none(self):
        """未知深度不得猜成 deep 或 shallow——不造数。"""
        self.assertIsNone(cs._depth_tier(None))

    def test_threshold_constant_is_250(self):
        """阈值锁定：改动须同步实验结论（130 根窗口虚报 12.5%）。"""
        self.assertEqual(cs.DEPTH_DEEP_BARS, 250)


class TestMakeRowCarriesDepth(unittest.TestCase):
    def _row(self, depth="__omit__"):
        if depth == "__omit__":
            return cs._make_row("AAA", "n", SIG, "2026-10-16", None, 10.0,
                                None, "scan")
        return cs._make_row("AAA", "n", SIG, "2026-10-16", None, 10.0,
                            None, "scan", depth=depth)

    def test_shallow_row(self):
        r = self._row(137)                 # 美股浅数据典型值
        self.assertEqual(r["depth_bars"], 137)
        self.assertEqual(r["depth_tier"], "shallow")

    def test_deep_row(self):
        r = self._row(270)                 # 美股深数据典型值
        self.assertEqual(r["depth_bars"], 270)
        self.assertEqual(r["depth_tier"], "deep")

    def test_omitted_is_none(self):
        """不传 depth → 两字段 None（历史行/其他调用方向后兼容）。"""
        r = self._row()
        self.assertIsNone(r["depth_bars"])
        self.assertIsNone(r["depth_tier"])

    def test_existing_keys_intact(self):
        """加字段不得动既有 schema（只增不改）。"""
        r = self._row(137)
        for k in ("market", "code", "name", "period", "type", "dt", "price",
                  "detect_date", "lag", "entry_px", "tradable", "trend",
                  "mbei", "bei", "bei2", "vv", "reg", "tbe", "mt", "npv",
                  "div_area", "fv_area", "div_dif", "fv_dif", "pv_state",
                  "pv_dir", "pv_fv", "conf", "n_prior", "gray_v",
                  "source", "first_seen"):
            self.assertIn(k, r, "既有字段 %s 丢失" % k)
        self.assertEqual(r["gray_v"], "v4")

    def test_depth_does_not_alter_signal_fields(self):
        """深度标注不得影响信号本体字段。"""
        a = self._row(137)
        b = self._row(270)
        for k in ("type", "dt", "price", "entry_px", "detect_date"):
            self.assertEqual(a[k], b[k])


if __name__ == "__main__":
    unittest.main()
