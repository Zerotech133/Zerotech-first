"""P2-H 缠论 dt 字符串比较丢首根 bar 回归测试。

根因: pandas 3.x 下 df["dt"].astype(str) 对午夜日线数据产出 "2024-01-05"(10 字符),
而笔端点 start_dt=str(Timestamp)="2024-01-05 00:00:00"(19 字符)。字典序比较
"2024-01-05" >= "2024-01-05 00:00:00" == False(短串是长串前缀)→ 首根 bar 被丢。
首根是笔分型端点(局部极值)→ 改变背驰面积/强度分级/买卖点。

覆盖 engine._bi_slice 与 rules_v2._window 两处;分钟线(dt 非午夜)作回归护栏。
"""
from __future__ import annotations

import pandas as pd
import pytest

from zhice_os_v2.domain.strategy.strategies.chan.engine import _bi_slice
from zhice_os_v2.domain.strategy.strategies.chan.rules_v2 import _green_area, _window

# 复刻 str(bi.fx_a.dt) / str(bi.fx_b.dt) 的 Timestamp 字符串格式(带 00:00:00)
BI_DAILY = {"start_dt": "2024-01-05 00:00:00", "end_dt": "2024-01-09 00:00:00"}


def _daily_df() -> pd.DataFrame:
    """5 根纯 date 日线(午夜),dt 为 datetime64。"""
    dts = pd.to_datetime(
        ["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08", "2024-01-09"]
    )
    return pd.DataFrame(
        {
            "dt": dts,
            "open": [10.0, 9.0, 8.0, 7.0, 6.0],
            "high": [10.0, 9.0, 8.0, 7.0, 6.0],
            "low": [9.0, 8.0, 7.0, 6.0, 5.0],
            "close": [9.0, 8.0, 7.0, 6.0, 5.0],
            "volume": [100.0, 90.0, 80.0, 70.0, 60.0],
        }
    )


def test_bi_slice_daily_keeps_first_bar() -> None:
    """_bi_slice 日线闭区间须含首根 bar(笔起点)。修前 RED=4,修后 GREEN=5。"""
    df = _daily_df()
    sl = _bi_slice(df, BI_DAILY)
    assert len(sl) == 5, f"首根 bar 被丢: 期望 5 根实得 {len(sl)} 根"
    assert sl.iloc[0]["dt"] == pd.Timestamp(BI_DAILY["start_dt"])
    assert sl.iloc[-1]["dt"] == pd.Timestamp(BI_DAILY["end_dt"])


def test_divergence_area_changes_when_first_bar_dropped() -> None:
    """端到端: 首根(局部极值)携带最大绿柱面积,丢首根改变背驰面积比→改判定。

    _hist 全为绿柱(负),首根 -5.0 占主导。正确面积=7.0,丢首根仅 2.0。
    修前 _window 丢首根 → full==dropped(判定被污染);修后 full != dropped。
    """
    df2 = _daily_df().assign(_hist=[-5.0, -1.0, -0.5, -0.3, -0.2])
    full = _green_area(_window(df2, "2024-01-05 00:00:00", "2024-01-09 00:00:00"))
    dropped_first = _green_area(df2.iloc[1:])  # 手工丢首根后的面积基准
    assert full == pytest.approx(7.0), f"修前丢首根面积仅 {full}(应为 7.0)"
    assert full != dropped_first  # 首根确实改变背驰面积→改分级/买卖点判定


def test_window_minute_unaffected() -> None:
    """回归护栏: 分钟线 dt 非午夜(时间对得上),修复不改原正确行为。"""
    dts = pd.to_datetime(
        ["2024-01-05 09:30:00", "2024-01-05 09:31:00", "2024-01-05 09:32:00"]
    )
    df2 = pd.DataFrame({"dt": dts, "_hist": [-1.0, -2.0, -3.0]})
    sl = _window(df2, "2024-01-05 09:30:00", "2024-01-05 09:32:00")
    assert len(sl) == 3
    assert sl.iloc[0]["dt"] == pd.Timestamp("2024-01-05 09:30:00")
    assert sl.iloc[-1]["dt"] == pd.Timestamp("2024-01-05 09:32:00")


def test_bi_slice_minute_unaffected() -> None:
    """回归护栏: _bi_slice 分钟线亦不受影响。"""
    dts = pd.to_datetime(
        ["2024-01-05 09:30:00", "2024-01-05 09:31:00", "2024-01-05 09:32:00"]
    )
    df = pd.DataFrame({"dt": dts, "volume": [10.0, 20.0, 30.0]})
    bi = {"start_dt": "2024-01-05 09:30:00", "end_dt": "2024-01-05 09:32:00"}
    sl = _bi_slice(df, bi)
    assert len(sl) == 3
