# -*- coding: utf-8 -*-
"""weak_track_check 触发判定单测（C3b · 纯函数，零 DB 零网络）。

覆盖 weak_track_check.py §4 关键位判定与 §6 复评触发判定的可测纯层
（stood_on / trig_ex_converge / trig_pool_new_sig / trend_tag），
合成 cases 四类全覆盖：破关键位（跌破/站上）/ 未触发 / 边界相等。
生产函数是行为保持抽取（原 main() 内联条件原样搬移），测试锁定口径：
· 站上关键位：px >= pivot（边界相等=站上）；px < pivot = 破位
· 触发1 超额收敛：ex >= -5pp（边界相等=触发）；None=不可算不触发
· 触发2 池内新信号：detect_date 严格大于 since（相等不算新）
· 日线趋势三线：多/空/缠绕/样本不足

运行：cd quant_backtest && python -m pytest test_weak_track_check_trigger.py -q
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from weak_track_check import (  # noqa: E402
    stood_on,
    trig_ex_converge,
    trig_pool_new_sig,
    trig_pivot_break,
    trend_tag,
    TRIG_EX_TH,
)


class TestStoodOn(unittest.TestCase):
    """关键位判定（破 pivot 上轨/下轨/边界相等 三态）。"""

    def test_break_below_pivot(self):
        """破关键位（下轨）：现价 < pivot → 未站上。"""
        self.assertFalse(stood_on(16.05, 16.12))
        self.assertFalse(stood_on(9.9, 10.0))

    def test_above_pivot(self):
        """站上关键位：现价 > pivot → 已站上。"""
        self.assertTrue(stood_on(17.0, 16.80))

    def test_boundary_equal_counts_as_stood(self):
        """边界相等：px == pivot → 站上（>= 口径）。"""
        self.assertTrue(stood_on(16.12, 16.12))
        self.assertTrue(stood_on(0.0, 0.0))


class TestTrigExConverge(unittest.TestCase):
    """§6 触发1：250 日超额收敛（阈值/边界/未触发/不可算）。"""

    def test_converged_triggers(self):
        self.assertTrue(trig_ex_converge(-3.2))

    def test_boundary_equal_triggers(self):
        """边界相等：ex == -5pp → 触发（>= 口径）。"""
        self.assertTrue(trig_ex_converge(TRIG_EX_TH))
        self.assertTrue(trig_ex_converge(-5.0))

    def test_not_converged_no_trigger(self):
        self.assertFalse(trig_ex_converge(-5.1))
        self.assertFalse(trig_ex_converge(-30.0))

    def test_none_no_trigger(self):
        """超额不可算（None）→ 不触发。"""
        self.assertFalse(trig_ex_converge(None))

    def test_positive_ex_triggers(self):
        """超额转正（个股跑赢）自然收敛 → 触发。"""
        self.assertTrue(trig_ex_converge(2.5))


class TestTrigPoolNewSig(unittest.TestCase):
    """§6 触发2：池内新信号（严格大于 since，边界相等不算新）。"""

    def test_new_signal_after_since(self):
        self.assertTrue(trig_pool_new_sig("2026-10-03", "2026-10-02"))

    def test_boundary_equal_not_new(self):
        """边界相等：detect_date == since → 当日已有，不算新信号。"""
        self.assertFalse(trig_pool_new_sig("2026-10-02", "2026-10-02"))

    def test_before_since_not_new(self):
        self.assertFalse(trig_pool_new_sig("2026-10-01", "2026-10-02"))

    def test_empty_detect_date_not_new(self):
        self.assertFalse(trig_pool_new_sig("", "2026-10-02"))

    def test_datetime_string_compares_lexically(self):
        """池内 detect_date 带时间尾串时仍按字符串比较成立。"""
        self.assertTrue(trig_pool_new_sig("2026-10-02 15:00", "2026-10-02"))


class TestTrigPivotBreak(unittest.TestCase):
    """§6 触发4：破左侧关键位（px < pivot，边界相等=未破，缺失不触发）。"""

    def test_break_triggers(self):
        """现价 < 左侧关键位 → 触发（首例实测：16.06 < 16.12）。"""
        self.assertTrue(trig_pivot_break(16.06, 16.12))

    def test_boundary_equal_not_break(self):
        """边界相等：px == pivot → 未破（>= 站上口径对偶）。"""
        self.assertFalse(trig_pivot_break(16.12, 16.12))

    def test_above_not_break(self):
        self.assertFalse(trig_pivot_break(16.27, 16.12))

    def test_none_px_no_trigger(self):
        """现价不可算（None）→ 不触发。"""
        self.assertFalse(trig_pivot_break(None, 16.12))

    def test_none_pivot_no_trigger(self):
        """关键位缺失（空 pivots）→ 不触发。"""
        self.assertFalse(trig_pivot_break(16.06, None))


class TestTrendTag(unittest.TestCase):
    """日线趋势三线（20/60 双均线）。"""

    def test_bull(self):
        # 末价 50 > ma20(41.45) > ma60(27.48)
        closes = list(range(1, 41)) + [41.0] * 19 + [50.0]
        self.assertEqual(trend_tag(closes), "多")

    def test_bear(self):
        # 末价 0.5 < ma20(0.975) < ma60(13.99)
        closes = list(range(40, 0, -1)) + [1.0] * 19 + [0.5]
        self.assertEqual(trend_tag(closes), "空")

    def test_flat_entangled(self):
        closes = [10.0] * 60
        self.assertEqual(trend_tag(closes), "平（均线缠绕）")

    def test_insufficient_sample(self):
        self.assertEqual(trend_tag([1.0] * 59), "样本不足")
        self.assertEqual(trend_tag([]), "样本不足")


if __name__ == "__main__":
    unittest.main(verbosity=2)
