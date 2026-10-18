"""trend_break 方法插件（T07）— 20 日线趋势破位判定（收盘确认）。

源文档 org-style-playbook-v0.1.md：破 20 日线减仓（趋势仓移动止盈依据）。
event 仓语义：收盘确认（不用盘中价）——引擎按 bar 重放天然满足。

优先消费 WatchContext.daily_df（daemon 由 v2_bar_daily 注入 / 回测引擎预载）；
None 时用 15m bars_df 重采样日线近似（reasons 标注）。

投票：
- 收盘 < MA20 × (1-band)（band 默认 0.5%）→ SELL 0.6 "跌破20日线"
- 连续 2 根日线收于 MA20 下方且今日反抽不过（high ≤ MA20）→ SELL 0.7
- 价格在 MA20 上方且 MA20 上行 → HOLD 0.5
- 日线数据不足 20 根 → WAIT 0.4
"""
from __future__ import annotations

import logging

import pandas as pd

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)

logger = logging.getLogger(__name__)

MA_PERIOD = 20            # 20 日线
BAND = 0.005              # 破位带宽 0.5%（收盘 < MA20×(1-band) 视为有效跌破）
CONSEC_DAYS = 2           # 连续收盘低于 MA20 的确认天数


def _daily_agg_np(dts, values, col: str):
    """15m 序列 → 日序列（close=末日 last / high=日 max；numpy 分组聚合）。"""
    import numpy as np

    days = np.asarray([pd.Timestamp(d).normalize().value for d in dts])
    order = np.argsort(days, kind="stable")        # dt 升序时恒等
    days_sorted = days[order]
    vals = values[order]
    # 日界切分：相邻 day 值变化处
    boundaries = np.nonzero(np.diff(days_sorted))[0] + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [len(days_sorted)]])
    out = np.empty(len(starts), dtype=float)
    for i, (s, e) in enumerate(zip(starts, ends)):
        seg = vals[s:e]
        seg = seg[~np.isnan(seg)]
        out[i] = (seg[-1] if len(seg) else np.nan) if col == "close" \
            else (seg.max() if len(seg) else np.nan)
    return out


class TrendBreakMethod(WatchMethod):
    """20 日线趋势破位（收盘确认）。daily_df 缺失时 15m 重采样近似。"""

    method_id = "trend_break"
    display_name = "趋势破位"
    weight = 1.0

    def __init__(self, ma_period: int = MA_PERIOD, band: float = BAND) -> None:
        self.ma_period = ma_period
        self.band = band

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        daily, approximated = self._daily_closes(ctx)
        if daily is None or len(daily) < self.ma_period:
            n = 0 if daily is None else len(daily)
            return MethodVote(
                self.method_id, "WAIT", 0.4,
                [f"日线数据不足（{n}<{self.ma_period}）"],
                params_snapshot=self.params())

        closes = daily.astype(float)
        ma = closes.rolling(self.ma_period).mean()
        last_close = float(closes.iloc[-1])
        last_ma = float(ma.iloc[-1])
        note = "（15m 重采样近似日线）" if approximated else ""

        if pd.isna(last_ma) or last_ma <= 0:
            return MethodVote(self.method_id, "WAIT", 0.4,
                              ["MA20 计算无效（NaN）"], params_snapshot=self.params())

        # ① 单日收盘跌破：close < MA20 × (1-band)
        if last_close < last_ma * (1.0 - self.band):
            return MethodVote(
                self.method_id, "SELL", 0.6,
                [f"跌破20日线{note}: 收盘{last_close:.4g} < "
                 f"MA20({last_ma:.4g})×(1-{self.band:.1%})"
                 f"={last_ma * (1.0 - self.band):.4g}"],
                params_snapshot=self.params())

        # ② 连续 2 根收于 MA20 下方且今日反抽不过（日内高点 ≤ MA20）
        below = (closes < ma).astype(bool)
        highs = self._daily_highs(ctx)
        if (len(below) >= CONSEC_DAYS and bool(below.iloc[-1])
                and bool(below.iloc[-2])):
            if highs is not None and len(highs) and float(highs.iloc[-1]) <= last_ma:
                return MethodVote(
                    self.method_id, "SELL", 0.7,
                    [f"连续{CONSEC_DAYS}日收于MA20下方且今日反抽不过"
                     f"（high={float(highs.iloc[-1]):.4g} ≤ MA20={last_ma:.4g}）{note}"],
                    params_snapshot=self.params())

        # ③ 价格在 MA20 上方且 MA20 上行 → HOLD
        ma_rising = (len(ma) >= 2 and not pd.isna(ma.iloc[-2])
                     and last_ma >= float(ma.iloc[-2]))
        if last_close >= last_ma and ma_rising:
            return MethodVote(
                self.method_id, "HOLD", 0.5,
                [f"收盘{last_close:.4g} ≥ MA20{last_ma:.4g} 且均线上行{note}"],
                params_snapshot=self.params())

        return MethodVote(
            self.method_id, "HOLD", 0.4,
            [f"价格贴近MA20（收盘{last_close:.4g}/MA20{last_ma:.4g}）未触发破位{note}"],
            params_snapshot=self.params())

    # ── 日线获取：daily_df 优先，15m 重采样近似兜底 ────────────────────────
    def _daily_closes(self, ctx: WatchContext) -> tuple[pd.Series | None, bool]:
        df = ctx.daily_df
        if df is not None and len(df) and "close" in df.columns:
            s = df["close"].astype(float)
            s = s.dropna()
            return (s if len(s) else None), False
        return self._resample_daily_cached(ctx, "close")

    def _daily_highs(self, ctx: WatchContext) -> pd.Series | None:
        df = ctx.daily_df
        if df is not None and len(df) and "high" in df.columns:
            return df["high"].astype(float).dropna()
        s, _ = self._resample_daily_cached(ctx, "high")
        return s

    def _resample_daily_cached(self, ctx: WatchContext, col: str):
        """15m→日 重采样（numpy 按日聚合；跨度 < ma_period 天直接短路返 None）。"""
        bars = ctx.bars_df
        if bars is None or len(bars) == 0 or "dt" not in bars.columns:
            return None, True
        # 短路：窗口时间跨度不足 ma_period 天 → 重采样后必不足 20 根，直接 WAIT 路径
        span_days = (pd.Timestamp(bars["dt"].iloc[-1])
                     - pd.Timestamp(bars["dt"].iloc[0])).total_seconds() / 86400.0
        if span_days < self.ma_period:
            return None, True
        arr = ctx.ensure_raw(
            f"tb_daily_{col}",
            lambda df: _daily_agg_np(df["dt"].to_numpy(), df[col].to_numpy(dtype=float),
                                     col))
        if arr is None or len(arr) == 0:
            return None, True
        s = pd.Series(arr)
        s = s.dropna()
        return (s if len(s) else None), True

    def params(self) -> dict:
        return {**super().params(), "ma_period": self.ma_period,
                "band": self.band, "consec_days": CONSEC_DAYS}
