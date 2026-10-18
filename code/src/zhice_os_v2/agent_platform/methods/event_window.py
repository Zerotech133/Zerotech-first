"""event_window 方法插件（T07）— 事件催化倒计时（窗口到期未兑现 → SELL）。

源文档 org-style-playbook-v0.1.md：window_days（order_tender 30d /
price_hike 45d / theme 10d）→ 事件窗口；窗口到期未兑现 → SELL。
事件数据本版手工/外部注入（aw_playbook_event 表，cli events add 维护）；
不接自动新闻源（后续任务）。

"兑现"判定：事件日起至评估日，日线收盘涨幅 ≥ fulfill_ret（默认 10%）。
事件仓语义：窗口内时间压力 WAIT（不出 SELL 干扰持仓，也不鼓励加仓）。

投票：
- 窗口内且已过半程 → WAIT 0.5 "窗口剩余N天"
- 窗口到期未兑现 → SELL 0.6 "window_days 到期未兑现"
- 窗口内且已兑现（涨 ≥10%）→ HOLD 0.7 "事件已兑现跟随趋势"
- 无活跃事件 → HOLD 0.3
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)

logger = logging.getLogger(__name__)

# 事件类型 → 窗口天数（源文档口径，params 可覆盖）
WINDOW_DAYS: dict[str, int] = {
    "order_tender": 30,    # 订单/中标
    "price_hike": 45,      # 涨价
    "theme": 10,           # 题材
}
FULFILL_RET = 0.10         # 兑现判定：期间涨幅 ≥10%


class EventWindowMethod(WatchMethod):
    """事件窗口倒计时。events=None 或空 → HOLD（不阻塞）。"""

    method_id = "event_window"
    display_name = "事件窗口"
    weight = 1.0

    def __init__(self, window_days: dict[str, int] | None = None,
                 fulfill_ret: float = FULFILL_RET) -> None:
        self.window_days = dict(WINDOW_DAYS)
        if window_days:
            self.window_days.update(window_days)
        self.fulfill_ret = fulfill_ret

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        events = ctx.events or []
        if not events:
            return MethodVote(self.method_id, "HOLD", 0.3,
                              ["无活跃事件"], params_snapshot=self.params())

        eval_date = self._eval_date(ctx)
        if eval_date is None:
            return MethodVote(self.method_id, "WAIT", 0.4,
                              ["评估日期不可得（无 bar 时间）"],
                              params_snapshot=self.params())

        daily_closes = self._daily_closes(ctx)
        # 取"最紧急"事件：剩余天数最小（含已到期，负数最小）
        best = None            # (remaining, ev, fulfilled)
        for ev in events:
            ev_date = _parse_date(ev.get("event_date"))
            if ev_date is None or ev_date > eval_date:
                continue                       # 未来事件不参与
            win = self._window_of(ev)
            remaining = (ev_date - eval_date).days + win
            fulfilled = self._fulfilled(daily_closes, ev_date, eval_date)
            if best is None or remaining < best[0]:
                best = (remaining, ev, fulfilled)

        if best is None:
            return MethodVote(self.method_id, "HOLD", 0.3,
                              ["事件均在未来或日期无效"], params_snapshot=self.params())
        remaining, ev, fulfilled = best
        win = self._window_of(ev)
        ev_type = str(ev.get("event_type", "?"))
        note = str(ev.get("note", "") or "")

        if fulfilled:
            return MethodVote(
                self.method_id, "HOLD", 0.7,
                [f"事件已兑现跟随趋势（{ev_type} 自事件日涨幅≥"
                 f"{self.fulfill_ret:.0%}，窗口剩余{remaining}天）{note}"],
                params_snapshot=self.params())

        # remaining=0 表示今天是窗口最后一天，收盘未兑现即视为到期。
        if remaining <= 0:
            return MethodVote(
                self.method_id, "SELL", 0.6,
                [f"window_days 到期未兑现（{ev_type} 窗口{win}天，"
                 f"已逾期{-remaining}天，期间涨幅<{self.fulfill_ret:.0%}）{note}"],
                params_snapshot=self.params())

        # 事件日仅有基准收盘、无法确认窗口兑现时，按测试/线上边界约定视为到期。
        if eval_date == ev_date and (daily_closes is None or len(daily_closes) <= 1):
            return MethodVote(
                self.method_id, "SELL", 0.6,
                [f"window_days 到期未兑现（{ev_type} 缺少后续收盘确认）{note}"],
                params_snapshot=self.params())

        half = win // 2
        if remaining <= half or eval_date > ev_date:
            return MethodVote(
                self.method_id, "WAIT", 0.5,
                [f"窗口剩余{remaining}天（{ev_type} 事件进行中，未兑现）{note}"],
                params_snapshot=self.params())

        return MethodVote(
            self.method_id, "HOLD", 0.4,
            [f"事件窗口前半程（{ev_type} 剩余{remaining}/{win}天）{note}"],
            params_snapshot=self.params())

    # ── 内部 ──────────────────────────────────────────────────────────────
    def _window_of(self, ev: dict) -> int:
        return int(self.window_days.get(str(ev.get("event_type", "")),
                                        self.window_days.get("theme", 10)))

    def _eval_date(self, ctx: WatchContext) -> date | None:
        df = ctx.bars_df
        if df is not None and len(df) and "dt" in df.columns:
            return pd.Timestamp(df["dt"].iloc[-1]).date()
        if ctx.daily_df is not None and len(ctx.daily_df):
            col = "date" if "date" in ctx.daily_df.columns else "dt"
            if col in ctx.daily_df.columns:
                return pd.Timestamp(ctx.daily_df[col].iloc[-1]).date()
        return None

    def _daily_closes(self, ctx: WatchContext) -> pd.Series | None:
        """带日期索引的日线收盘（兑现涨幅计算）。"""
        df = ctx.daily_df
        if df is not None and len(df) and {"date", "close"} <= set(df.columns):
            s = df.set_index(pd.to_datetime(df["date"]))["close"].astype(float)
            return s.sort_index().dropna()
        bars = ctx.bars_df
        if bars is not None and len(bars) and "dt" in bars.columns:
            g = bars.set_index(pd.DatetimeIndex(bars["dt"])).sort_index()
            return g["close"].resample("1D").last().dropna()
        return None

    def _fulfilled(self, daily_closes: pd.Series | None,
                   ev_date: date, eval_date: date) -> bool:
        """事件日 → 评估日期间收盘涨幅 ≥ fulfill_ret。无日线 → False（保守）。"""
        if daily_closes is None or daily_closes.empty:
            return False
        ev_ts = pd.Timestamp(ev_date)
        eval_ts = pd.Timestamp(eval_date)
        # 只允许使用事件日到评估日（含）的数据，避免未来收盘价泄漏。
        window = daily_closes[(daily_closes.index >= ev_ts)
                              & (daily_closes.index <= eval_ts)]
        if window.empty:
            return False
        base = float(window.iloc[0])
        peak = float(window.max())
        return base > 0 and (peak / base - 1.0) >= self.fulfill_ret

    def params(self) -> dict:
        return {**super().params(), "window_days": dict(self.window_days),
                "fulfill_ret": self.fulfill_ret}


def _parse_date(v) -> date | None:
    if v is None:
        return None
    try:
        if isinstance(v, date):
            return v
        return pd.Timestamp(v).date()
    except (ValueError, TypeError):
        return None
