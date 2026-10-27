# -*- coding: utf-8 -*-
"""strong_track 信号层金样本单测（D2 第 11 领域卡 · 阶段1 · 纯函数零 DB 零网络）。

对齐 C3b 范式（test_weak_track_check_trigger.py）：unittest + 合成 cases，
四类全覆盖：达标/未达标/边界相等/不可算 + 门槛成员数 + 趋势确认 + 综合判定
三态（板块领涨 / 逆板块强势 / 不构成）。锁定口径：
· 超额达标：ex >= +10pp（边界相等=达标）；None=不可算不达标
· 板块成员门槛：n >= 5（边界相等=达标）；None=不可判不达标
· 趋势多头：last > ma20 > ma60；样本 <60 不可判
· 综合判定三态与 reasons 完整性

运行：cd quant_backtest && python -m pytest test_strong_track_gold.py -q
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from strong_track import (  # noqa: E402
    strong_ex,
    block_quorum,
    trend_bull,
    strong_verdict,
    STRONG_EX_TH,
    MIN_BLOCK_MEMBERS,
)


class TestStrongEx(unittest.TestCase):
    """250 日超额达标判定（阈值/边界/未达标/不可算）。"""

    def test_strong_triggers(self):
        self.assertTrue(strong_ex(15.3))

    def test_boundary_equal_triggers(self):
        """边界相等：ex == +10pp → 达标（>= 口径）。"""
        self.assertTrue(strong_ex(STRONG_EX_TH))
        self.assertTrue(strong_ex(10.0))

    def test_below_threshold_no_trigger(self):
        self.assertFalse(strong_ex(9.9))
        self.assertFalse(strong_ex(0.0))

    def test_negative_ex_no_trigger(self):
        self.assertFalse(strong_ex(-5.0))

    def test_none_no_trigger(self):
        """超额不可算（None）→ 不达标。"""
        self.assertFalse(strong_ex(None))

    def test_custom_threshold(self):
        """自定义阈值生效（阶段2 分层可能用 5pp 宽口径）。"""
        self.assertTrue(strong_ex(6.0, th=5))
        self.assertFalse(strong_ex(4.9, th=5))


class TestBlockQuorum(unittest.TestCase):
    """板块成员数门槛（防小板块噪声）。"""

    def test_enough_members(self):
        self.assertTrue(block_quorum(20))

    def test_boundary_equal_passes(self):
        """边界相等：n == 5 → 达标。"""
        self.assertTrue(block_quorum(MIN_BLOCK_MEMBERS))
        self.assertTrue(block_quorum(5))

    def test_too_few_members(self):
        self.assertFalse(block_quorum(4))
        self.assertFalse(block_quorum(1))

    def test_none_not_pass(self):
        """成员数不可判 → 不过门槛。"""
        self.assertFalse(block_quorum(None))


class TestTrendBull(unittest.TestCase):
    """日线多头三线（必要条件，非板块门槛）。"""

    def test_bull(self):
        # 末价 50 > ma20(41.45) > ma60(27.48)（与 weak 测试同构数据）
        closes = list(range(1, 41)) + [41.0] * 19 + [50.0]
        self.assertTrue(trend_bull(closes))

    def test_bear_not_bull(self):
        closes = list(range(40, 0, -1)) + [1.0] * 19 + [0.5]
        self.assertFalse(trend_bull(closes))

    def test_flat_not_bull(self):
        self.assertFalse(trend_bull([10.0] * 60))

    def test_insufficient_sample(self):
        self.assertFalse(trend_bull([1.0] * 59))
        self.assertFalse(trend_bull([]))


class TestStrongVerdict(unittest.TestCase):
    """综合判定三态：板块领涨 / 逆板块强势 / 不构成。"""

    def test_full_strong_signal(self):
        """超额达标+成员达标+多头 → 板块领涨（强信号）。"""
        is_strong, tag, reasons = strong_verdict(15.0, 20, True)
        self.assertTrue(is_strong)
        self.assertEqual(tag, "板块领涨")
        self.assertEqual(len(reasons), 3)

    def test_counter_block_flow(self):
        """超额达标+成员达标+非多头 → 逆板块强势（观察名单，不推送）。"""
        is_strong, tag, reasons = strong_verdict(15.0, 20, False)
        self.assertTrue(is_strong)
        self.assertEqual(tag, "逆板块强势")
        self.assertTrue(any("人工复核" in r for r in reasons))

    def test_ex_fail_not_strong(self):
        """超额未达标 → 不构成（即使成员与趋势都好）。"""
        is_strong, tag, reasons = strong_verdict(5.0, 20, True)
        self.assertFalse(is_strong)
        self.assertEqual(tag, "不构成")
        self.assertTrue(any("未达标" in r for r in reasons))

    def test_none_ex_not_strong(self):
        is_strong, tag, _ = strong_verdict(None, 20, True)
        self.assertFalse(is_strong)
        self.assertEqual(tag, "不构成")

    def test_small_block_not_strong(self):
        """小板块噪声 → 不构成（即使超额达标+多头）。"""
        is_strong, tag, reasons = strong_verdict(15.0, 3, True)
        self.assertFalse(is_strong)
        self.assertEqual(tag, "不构成")
        self.assertTrue(any("噪声板块" in r for r in reasons))

    def test_custom_params_pass_through(self):
        """自定义阈值/成员数透传（阶段2 分层预备）。"""
        is_strong, tag, _ = strong_verdict(6.0, 3, True, th_ex=5, min_members=3)
        self.assertTrue(is_strong)
        self.assertEqual(tag, "板块领涨")

    def test_mirror_consistency_with_weak(self):
        """镜像一致性：weak 的收敛域（-5pp）与 strong 的达标域（+10pp）
        不重叠——同一超额值不应同时命中两产线的触发。"""
        # 超额 -5pp：weak 触发1 命中（>=-5），strong 不达标（<+10）
        ex = -5.0
        from weak_track_check import trig_ex_converge

        self.assertTrue(trig_ex_converge(ex))
        self.assertFalse(strong_ex(ex))


if __name__ == "__main__":
    unittest.main(verbosity=2)
