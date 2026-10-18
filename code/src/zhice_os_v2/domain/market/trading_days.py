"""交易日计数（纯函数，与持久化解耦，便于测试）。"""
from __future__ import annotations

from datetime import date


def count_trading_days(
    calendar: dict[date, bool], opened: date, as_of: date
) -> int:
    """统计 [opened, as_of] 闭区间内 is_open=True 的交易日数（含首尾）。

    calendar: {trade_date: is_open}。缺失的日期视为非交易日。
    as_of < opened 返回 0。
    """
    if as_of < opened:
        return 0
    return sum(
        1
        for d, is_open in calendar.items()
        if is_open and opened <= d <= as_of
    )
