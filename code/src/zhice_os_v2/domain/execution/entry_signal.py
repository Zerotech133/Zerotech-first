"""盘中买卖点信号 — 纯函数（盯盘池入场确认 + 持仓止盈）。

设计（对齐"盘前选股=盯盘池，盘中表现定买点"的操作模型）：
  入场不在开盘无条件成交，而是盘中每个监控周期评估：
    E1 价格纪律：现价较昨收涨跌幅 ∈ [ENTRY_MIN_PCT, ENTRY_MAX_PCT]
       （高开过多不追、低走过深不接——原开盘跳空保护融入此价格带）
    E2 日内走强：现价 >= 当日开盘价
    E3 动能延续：现价 >= 上一监控周期价格
  三条全满足 → 触发买入（实时价成交）。

  持仓卖点（与止损/移动止损互补）：
    X1 止盈触达：现价 >= 计划止盈价 → 平仓
"""
from __future__ import annotations

# 已由 Profile.intraday_entry_band/window() 接管；保留导出供 test_entry_signal.py 作边界参照。
ENTRY_MIN_PCT = -0.02   # 相对昨收最低容忍（低开/低走超2%不接）
ENTRY_MAX_PCT = 0.05    # 相对昨收最高追价（涨超5%不追）
ENTRY_WINDOW = ("09:35", "14:45")  # 入场时间窗（开盘竞价噪音后，尾盘前）


def evaluate_entry(
    live_price: float,
    pre_close: float | None,
    day_open: float | None,
    prev_price: float | None,
    now_hhmm: str,
    market: str = "CN",
) -> tuple[bool, str]:
    """评估盯盘标的是否触发买点。返回 (是否触发, 大白话原因)。"""
    from zhice_os_v2.domain.market.registry import get_profile
    profile = get_profile(market)
    win_lo, win_hi = profile.intraday_entry_window()
    band_lo, band_hi = profile.intraday_entry_band()
    min_pct, max_pct = float(band_lo), float(band_hi)

    if not live_price or live_price <= 0:
        return False, "无有效实时价"

    if not (win_lo <= now_hhmm <= win_hi):
        return False, f"不在入场时间窗{win_lo}~{win_hi}内"

    # E1 价格纪律
    if pre_close and pre_close > 0:
        chg = live_price / pre_close - 1
        if chg > max_pct:
            return False, f"较昨收已涨{chg:.1%}，超过{max_pct:.0%}不追高"
        if chg < min_pct:
            return False, f"较昨收已跌{abs(chg):.1%}，超过{abs(min_pct):.0%}不接刀"

    # E2 日内走强
    if day_open and day_open > 0 and live_price < day_open:
        return False, f"现价{live_price:.2f}低于开盘价{day_open:.2f}，日内未走强"

    # E3 动能延续
    if prev_price and prev_price > 0 and live_price < prev_price:
        return False, f"现价{live_price:.2f}低于上一周期{prev_price:.2f}，动能转弱"

    chg_str = f"{(live_price / pre_close - 1):+.1%}" if pre_close else "—"
    return True, (
        f"盘中买点确认: 现价{live_price:.2f}（较昨收{chg_str}），"
        f"站上开盘价且较上周期走强"
    )


def evaluate_take_profit(live_price: float, take_profit_price: float | None) -> tuple[bool, str]:
    """止盈触达评估。返回 (是否触发, 原因)。"""
    if not take_profit_price or take_profit_price <= 0 or not live_price:
        return False, ""
    if live_price >= take_profit_price:
        return True, f"止盈触达: 现价{live_price:.2f} ≥ 止盈价{take_profit_price:.2f}"
    return False, ""


def t_plus_one_locked(opened_at, today, market: str = "CN") -> bool:
    """当日开仓批次当日是否禁卖。真源:Profile.same_day_sell_allowed；CN 锁(T+1)按市场 tz 换算日期。

    CN=T+1 交易锁(当日买不可当日卖);US=T+0 交易(当日可卖)。
    """
    if opened_at is None:
        return False
    from zhice_os_v2.domain.market.registry import get_profile

    profile = get_profile(market)
    if profile.same_day_sell_allowed:
        return False
    dt = opened_at
    if dt.tzinfo is not None:
        dt = dt.astimezone(profile.tz)
    return dt.date() == today
