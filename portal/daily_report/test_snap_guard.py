# -*- coding: utf-8 -*-
"""快照价格守卫（snap_guard）的判定测试。

守的是 2026-11-11 那个真实事故：
  11-06/main 当天 15:56:27 与 15:59:07 两次快照，累计收益 12.558% → 14.349%，
  相差 1.79pp；16:15 重跑回到 12.558%。收盘后价格已定格，这个差异只能是坏价，
  而报告若在 15:59~16:15 之间生成就会把 14.35% 当成绩写进去。

注意：本文件里的时刻字符串只作入参，不做数值推断；断言只看 severity / code
这类枚举与布尔，不靠打印出来的数字肉眼判断。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cohort_track as ct  # noqa: E402


def rec(cum, day_index=4):
    return {"cum_today_mean": cum, "day_index": day_index}


def test_r1_收盘后定盘不一致判高危():
    """真实事故的复现：15:56:27 → 15:59:07，+1.79pp。"""
    w = ct.snap_guard("20261106/main", 0.143486, "15:59:07", 4,
                      rec(0.125580), "15:56:27")
    assert w is not None
    assert w["severity"] == "high"
    assert w["code"] == "price_anomaly"
    assert w["delta_pp"] == pytest.approx(1.791, abs=1e-3)


def test_r1_负向同样判高危():
    """坏价也可能把价格压低了，方向不影响判定。"""
    w = ct.snap_guard("20261106/main", 0.080000, "16:20:00", 4,
                      rec(0.125580), "16:15:21")
    assert w is not None and w["severity"] == "high"
    assert w["delta_pp"] < 0


def test_收盘后差异很小不告警():
    """16:15 与 18:01 两次只差 0.00pp，属正常。"""
    assert ct.snap_guard("20261106/main", 0.125580, "18:01:09", 4,
                         rec(0.125580), "16:15:21") is None


def test_恰好一pp不告警():
    """规矩是「超过 1pp」，正好 1.00pp 不触发。"""
    assert ct.snap_guard("x/main", 0.010000, "16:00:00", 1,
                         rec(0.000000, day_index=1), "15:30:00") is None


def test_r2_盘中三十分钟内跳变判中危():
    w = ct.snap_guard("x/auto", 0.050000, "10:15:00", 1,
                      rec(0.020000, day_index=1), "09:58:00")
    assert w is not None
    assert w["severity"] == "medium"
    assert w["code"] == "price_jump"


def test_盘中间隔太久不告警():
    """两小时里涨 3pp 是真行情，不是坏价，不能报。"""
    assert ct.snap_guard("x/auto", 0.050000, "13:30:00", 1,
                         rec(0.020000, day_index=1), "10:30:00") is None


def test_跨日不比较():
    """上一轮是第 3 日、本轮第 4 日 → 隔夜跳空是真行情。"""
    assert ct.snap_guard("x/main", 0.14, "15:59:07", 4,
                         rec(0.10, day_index=3), "15:56:27") is None


def test_没有上一轮就不判():
    assert ct.snap_guard("x/main", 0.14, "15:59:07", 4, None, None) is None
    assert ct.snap_guard("x/main", 0.14, "15:59:07", 4, {}, None) is None


def test_数值缺失不判():
    assert ct.snap_guard("x/main", None, "15:59:07", 4, rec(0.12), "15:56:00") is None
    assert ct.snap_guard("x/main", 0.14, "15:59:07", 4,
                         {"cum_today_mean": None, "day_index": 4}, "15:56:00") is None


def test_时刻解析失败退化处理():
    """at 拿不到时不崩，且不误判为收盘后（宁可漏报也不误报）。"""
    assert ct._at_minutes("乱码") is None
    assert ct._at_minutes(None) is None
    assert ct._at_minutes("15:59:07") == 15 * 60 + 59
    # 上一轮时刻缺失 → 无法证明「都在收盘后」→ 不判 high
    w = ct.snap_guard("x/main", 0.14, "15:59:07", 4, rec(0.12), None)
    assert w is None
