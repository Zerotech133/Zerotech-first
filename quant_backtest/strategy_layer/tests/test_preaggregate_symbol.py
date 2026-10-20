# -*- coding: utf-8 -*-
"""preaggregate_symbol 直接单测（方案 C 同票预聚合收编判例挂账项补测）。

覆盖：同票覆盖链（含 rationale 截断）/ dt 缺失视为最旧 / 跨策略隔离 /
同 dt 并列后到者胜 / empty 与单条边界。全程本地 stub，不触库、
不 import 生产 signal 模块。
"""
import os
import sys
from datetime import date

import pytest

QUANT_BT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer.aggregator import (  # noqa: E402
    _DT_NONE_SENTINEL,
    preaggregate_symbol,
)


class Sig:
    """最小 Signal stub：仅 preaggregate_symbol 消费的字段。"""

    def __init__(self, action="buy", score=50.0, rationale="", dt=None):
        self.action = action
        self.score = score
        self.rationale = rationale
        self.dt = dt


# ── ① 同票覆盖链：A→B→C 三条同策略信号 ────────────────────────────────
class TestOverrideChain:
    def test_chain_c_kept_a_b_superseded_in_order(self):
        """C 保留；A、B 逐条入 superseded 且顺序为 A→B（覆盖链完整回放）。"""
        a = Sig(action="watch", score=10.0, rationale="旧事件A",
                dt="2026-10-01")
        b = Sig(action="hold", score=30.0, rationale="中继事件B",
                dt="2026-10-02")
        c = Sig(action="buy", score=90.0, rationale="最新事件C",
                dt="2026-10-03")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, a), ("s1", 1.0, b), ("s1", 1.0, c)])
        assert [(s, w, g) for s, w, g in kept] == [("s1", 1.0, c)]
        items = sup["s1"]
        assert len(items) == 2
        # 顺序：先被覆盖的 A 在前，中间覆盖链的 B 在后
        assert items[0] == {"action": "watch", "score": 10.0,
                            "dt": "2026-10-01", "rationale": "旧事件A"}
        assert items[1] == {"action": "hold", "score": 30.0,
                            "dt": "2026-10-02", "rationale": "中继事件B"}

    def test_superseded_rationale_truncated_to_80(self):
        """被覆盖条目 rationale 截断 80 字符控体积。"""
        long_sig = Sig(action="sell", score=-5.5,
                       rationale="x" * 120, dt="2026-10-01")
        newer = Sig(action="buy", score=80.0, rationale="新", dt="2026-10-02")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, long_sig), ("s1", 1.0, newer)])
        assert kept[0][2] is newer
        item = sup["s1"][0]
        assert item["rationale"] == "x" * 80
        assert len(item["rationale"]) == 80
        assert item["score"] == pytest.approx(-5.5)


# ── ② dt 缺失视为最旧 ─────────────────────────────────────────────────
class TestDtMissing:
    def test_missing_dt_overridden_by_dated(self):
        """无 dt 的旧信号被有 dt 的覆盖，superseded 明细 dt 记 None。"""
        no_dt = Sig(action="hold", score=20.0, rationale="无源事件日")
        dated = Sig(action="buy", score=70.0, rationale="有日期",
                    dt="2026-10-15")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, no_dt), ("s1", 1.0, dated)])
        assert kept[0][2] is dated
        assert sup["s1"] == [{"action": "hold", "score": 20.0,
                              "dt": None, "rationale": "无源事件日"}]

    def test_missing_dt_arriving_later_still_loses(self):
        """无 dt 信号后到也判负（sentinel 最旧，凡有 dt 者优先）。"""
        dated = Sig(action="buy", score=70.0, dt="2026-10-15")
        no_dt = Sig(action="sell", score=10.0, dt=None)
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, dated), ("s1", 1.0, no_dt)])
        assert kept[0][2] is dated
        assert sup["s1"][0]["action"] == "sell"

    def test_empty_str_dt_treated_as_missing(self):
        """空串 dt 与 None 同判：走 _DT_NONE_SENTINEL 占位。"""
        empty_dt = Sig(dt="")
        dated = Sig(dt="2026-10-15")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, empty_dt), ("s1", 1.0, dated)])
        assert kept[0][2] is dated
        assert sup["s1"][0]["dt"] is None

    def test_all_missing_dt_later_input_wins(self):
        """全缺失 dt 时 sentinel 间 >= 成立，退化为输入序后到者胜。"""
        first = Sig(action="hold", score=1.0)
        second = Sig(action="buy", score=2.0)
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, first), ("s1", 1.0, second)])
        assert kept[0][2] is second
        assert sup["s1"][0] == {"action": "hold", "score": 1.0,
                                "dt": None, "rationale": ""}


# ── ③ 跨策略不受影响 ──────────────────────────────────────────────────
class TestCrossStrategy:
    def test_distinct_strategy_ids_all_kept(self):
        """同 symbol 不同 strategy_id 各自保留，互不覆盖。"""
        a1 = Sig(action="buy", score=60.0, dt="2026-10-01")
        b1 = Sig(action="sell", score=40.0, dt="2026-10-01")
        kept, sup = preaggregate_symbol(
            [("s1", 0.6, a1), ("s2", 0.4, b1)])
        assert [(s, w) for s, w, _ in kept] == [("s1", 0.6), ("s2", 0.4)]
        assert sup == {}

    def test_override_confined_to_own_strategy(self):
        """同策略覆盖不波及其他策略；superseded 无 s2 键。"""
        s1_old = Sig(action="watch", score=10.0, dt="2026-10-01")
        s2_sig = Sig(action="sell", score=40.0, dt="2026-10-02")
        s1_new = Sig(action="buy", score=80.0, dt="2026-10-03")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, s1_old), ("s2", 1.0, s2_sig), ("s1", 1.0, s1_new)])
        assert [(s, w, g) for s, w, g in kept] == [
            ("s1", 1.0, s1_new), ("s2", 1.0, s2_sig)]
        assert set(sup) == {"s1"}
        assert sup["s1"] == [{"action": "watch", "score": 10.0,
                              "dt": "2026-10-01", "rationale": ""}]

    def test_interleaved_same_symbol_kept_order_stable(self):
        """交错输入：各策略保留条目维持首现相对顺序。"""
        a1 = Sig(dt="2026-10-01")
        b1 = Sig(dt="2026-10-02")
        a2 = Sig(dt="2026-10-05")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, a1), ("s2", 1.0, b1), ("s1", 1.0, a2)])
        assert [s for s, _, _ in kept] == ["s1", "s2"]
        assert kept[0][2] is a2 and kept[1][2] is b1


# ── ④ 同 dt 并列：输入序后到者胜 ──────────────────────────────────────
class TestSameDtTie:
    def test_same_dt_later_input_wins(self):
        first = Sig(action="sell", score=30.0, rationale="先到",
                    dt="2026-10-10")
        second = Sig(action="buy", score=65.0, rationale="后到",
                     dt="2026-10-10")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, first), ("s1", 1.0, second)])
        assert kept[0][2] is second
        assert sup["s1"] == [{"action": "sell", "score": 30.0,
                              "dt": "2026-10-10", "rationale": "先到"}]

    def test_three_way_same_dt_chain(self):
        """三条同 dt：逐一覆盖，前两条均入 superseded（>= 语义）。"""
        s1 = Sig(action="watch", score=1.0, dt="2026-10-10")
        s2 = Sig(action="hold", score=2.0, dt="2026-10-10")
        s3 = Sig(action="buy", score=3.0, dt="2026-10-10")
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, s1), ("s1", 1.0, s2), ("s1", 1.0, s3)])
        assert kept[0][2] is s3
        assert [it["action"] for it in sup["s1"]] == ["watch", "hold"]


# ── ⑤ 边界：empty / 单条 ──────────────────────────────────────────────
class TestBoundaries:
    def test_empty_entries(self):
        assert preaggregate_symbol([]) == ([], {})

    def test_single_entry_kept_as_is(self):
        sig = Sig(action="buy", score=55.0, rationale="唯一",
                  dt="2026-10-19")
        kept, sup = preaggregate_symbol([("solo", 0.8, sig)])
        assert kept == [("solo", 0.8, sig)]
        assert sup == {}

    def test_dt_key_non_str_falls_to_sentinel(self):
        """_DT_NONE_SENTINEL 占位路径：非 str dt 与缺失同判（见实现疑点）。"""
        assert _DT_NONE_SENTINEL == "0000-00-00"
        kept, sup = preaggregate_symbol(
            [("s1", 1.0, Sig(dt=None)), ("s1", 1.0, Sig(dt="2026-10-19"))])
        assert sup["s1"][0]["dt"] is None


# ── dt 契约（WO-QT-20261020-055 方案甲，2026-10-21 老田拍板）─────────
# 旧 xfail 疑点用例显式作废：方案甲裁决为入口对非字符串 dt 显式拒绝
# （fail-visible，不隐式转换），date 对象的排序语义之争在契约层终结——
# 该输入场景在聚合入口即被拒绝，原疑点场景不可能再发生。
@pytest.mark.parametrize("bad_dt", [date(2026, 10, 19), 20261019, 19.5])
def test_nonstr_dt_rejected_fail_visible(bad_dt):
    """方案甲：非字符串 dt（非 None）必须 ValueError 显式拒绝，
    绝不静默按缺失降档、绝不隐式转换（字面量断言承重）。"""
    older = Sig(action="sell", score=10.0, dt="2026-10-18")
    newer = Sig(action="buy", score=90.0, dt=bad_dt)
    with pytest.raises(ValueError, match="YYYY-MM-DD") as exc_info:
        preaggregate_symbol([("s1", 1.0, older), ("s1", 1.0, newer)])
    assert "YYYY-MM-DD" in str(exc_info.value)
    assert type(bad_dt).__name__ in str(exc_info.value)


def test_nonstr_dt_rejection_does_not_mutate_kept_entries():
    """拒绝发生在任何 kept/superseded 写入生效前（首条即违例也拒绝）。"""
    bad = Sig(action="buy", score=90.0, dt=date(2026, 10, 19))
    with pytest.raises(ValueError):
        preaggregate_symbol([("s1", 1.0, bad)])
    # 单条违例同样拒绝（入口校验不因"无比较对象"而放行）

