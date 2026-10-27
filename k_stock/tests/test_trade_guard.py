# -*- coding: utf-8 -*-
"""trade_guard 交易日守卫测试（冻结时钟注入，不碰真实行情湖）。

覆盖 K-STOCK-101（1699be1）盘前常态放行修复：
- 湖日历只含已入库 bar 的交易日（最新=上一交易日），盘前「今天」必不在
  日历内 → 工作日放行 / 周末仍拦 / 日历异常 fail-open / 空日历 fail-closed。
"""

from __future__ import annotations

import sys
from datetime import datetime as _real_datetime
from pathlib import Path

import pytest

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

import trade_guard  # noqa: E402
from engine import Reject  # noqa: E402


def _freeze(y: int, m: int, d: int):
    """造一个 now() 返回指定日期的 datetime 替身（monkeypatch 进 trade_guard）。"""

    class FrozenDatetime(_real_datetime):
        @classmethod
        def now(cls):
            return cls(y, m, d, 7, 5, 0)

    return FrozenDatetime


class _CalMarket:
    """最小市场替身：trading_days() 返回固定日历（可注异常）。"""

    def __init__(self, days, raise_exc: bool = False):
        self._days = days
        self._raise = raise_exc

    def trading_days(self):
        if self._raise:
            raise RuntimeError("lake unavailable")
        return list(self._days)


def test_today_in_calendar_trades(monkeypatch):
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 12))
    mkt = _CalMarket(["20261009", "20261012"])  # 今晨 bar 已入库
    assert trade_guard.is_trading_day_today(mkt) is True


def test_preopen_monday_stale_calendar_passes(monkeypatch):
    """K-STOCK-101 回归：周一 07:05 盘前，湖日历最新只到上周五 → 放行。"""
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 12))  # 周一
    mkt = _CalMarket(["20261009"])  # 上周五，今天的 bar 必然未入库
    assert trade_guard.is_trading_day_today(mkt) is True


def test_weekend_stale_calendar_blocked(monkeypatch):
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 10))  # 周六
    mkt = _CalMarket(["20261009"])
    assert trade_guard.is_trading_day_today(mkt) is False


def test_today_earlier_than_calendar_blocked(monkeypatch):
    """今天不在日历且早于湖最新（异常中间缺日）→ fail-closed。"""
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 8))  # 周四
    mkt = _CalMarket(["20261009"])
    assert trade_guard.is_trading_day_today(mkt) is False


def test_empty_calendar_blocked(monkeypatch):
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 12))
    assert trade_guard.is_trading_day_today(_CalMarket([])) is False


def test_calendar_exception_fail_open(monkeypatch):
    """日历不可得 fail-open（engine replan 守卫兜底）——文档承诺行为。"""
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 10))  # 周六也会放
    assert trade_guard.is_trading_day_today(_CalMarket([], raise_exc=True)) is True


def test_ensure_raises_on_non_trading_day(monkeypatch):
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 10))  # 周六
    with pytest.raises(Reject) as ei:
        trade_guard.ensure_trading_day(_CalMarket(["20261009"]), market_label="CN")
    assert "非 CN 交易日" in str(ei.value)


def test_ensure_silent_on_trading_day(monkeypatch):
    monkeypatch.setattr(trade_guard, "datetime", _freeze(2026, 10, 12))  # 周一盘前
    trade_guard.ensure_trading_day(
        _CalMarket(["20261009"]), market_label="CN"
    )  # 不抛即过
