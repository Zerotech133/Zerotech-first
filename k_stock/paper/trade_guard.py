# -*- coding: utf-8 -*-
"""交易日守卫（077q）：调度层拒绝在非交易日执行 exec 落单。

事故背景：周末 automation 照跑 exec，产物每日换文件名→幂等失效→pending
堆叠两批同票单，周一 settle 双倍成交。engine 侧 replan 守卫兜底后，本模块
在调度层治本：非交易日直接不跑（产物保留，下一交易日例行执行最新意图）。

用法（市场对象需有 trading_days()，ParquetMarket / UsMarket 均可）：
  from trade_guard import ensure_trading_day
  ensure_trading_day(ParquetMarket())          # 非交易日 raise Reject
  ensure_trading_day(UsMarket(), market="US")  # 报错文案标注市场

注意：routine（settle/check-stops/mark）不加此守卫——mark 每天都要跑
（权益曲线连续性），settle 在非交易日无新 bar 可撮、天然无害。
"""
from __future__ import annotations

from datetime import datetime

from engine import Reject  # noqa: E402


def is_trading_day_today(market) -> bool:
    """今天在交易日历中 → True。日历不可得时 fail-open（engine 守卫兜底）。"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        return today in set(market.trading_days())
    except Exception:
        return True


def ensure_trading_day(market, market_label: str = "CN") -> None:
    """非交易日 raise Reject（automation 跳过当日 exec）。"""
    if not is_trading_day_today(market):
        raise Reject(
            f"[trade_guard] 今日 {datetime.now():%Y-%m-%d} 非 {market_label} 交易日，"
            "exec 不执行（产物保留，下一交易日例行执行最新意图；"
            "调试用 --force-trading 绕过）")
