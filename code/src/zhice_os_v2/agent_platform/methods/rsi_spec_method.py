"""RSI-spec 方法插件：对齐 zhice spec.py 信号型模式的超买钝化观察。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)


def rsi_wilder(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI（ewm(alpha=1/period) 平滑；整列向量化）。"""
    return pd.Series(rsi_wilder_np(closes.to_numpy(dtype=float), period),
                     index=closes.index)


def rsi_wilder_np(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI（numpy 热路径；语义与 pandas ewm 版一致）。

    - avg_gain/avg_loss 用 Wilder 递推（等价 ewm(alpha=1/period, adjust=False)）
    - avg_loss==0（单边涨）→ RSI=100；avg_gain==avg_loss==0（零波动）→ 50
    - 前 period 根无定义 → NaN
    """
    n = closes.size
    rsi = np.full(n, np.nan)
    if n < 2:
        return rsi
    delta = np.diff(closes)                       # 长度 n-1
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    alpha = 1.0 / period

    # Wilder 递推（首值 = 前 period 根简单均值，与 min_periods=period 对齐）
    avg_gain = np.empty(delta.size)
    avg_loss = np.empty(delta.size)
    avg_gain[0] = gain[0]
    avg_loss[0] = loss[0]
    for i in range(1, delta.size):
        avg_gain[i] = alpha * gain[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i] + (1 - alpha) * avg_loss[i - 1]

    valid_from = period - 1                        # rsi 索引 = delta 索引 + 1
    for i in range(valid_from, delta.size):
        g, l = avg_gain[i], avg_loss[i]
        if l == 0.0:
            rsi[i + 1] = 100.0 if g > 0 else 50.0
        else:
            rs = g / l
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


class RsiSpecMethod(WatchMethod):
    """RSI(14) 超买钝化 / 超卖观察 / 中性持有。"""

    method_id = "rsi_spec"
    display_name = "RSI超买钝化"
    required_features = ["rsi_14"]
    period = 14
    overbought = 70.0
    oversold = 30.0
    dull_bars = 3               # 连续钝化根数
    neutral_low = 40.0
    neutral_high = 60.0

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        df = ctx.bars_df
        need = self.period + self.dull_bars + 2
        if df is None or len(df) < need:
            return MethodVote(self.method_id, "WAIT", 0.0,
                              [f"数据不足(len={0 if df is None else len(df)}<{need})"])

        rsi = ctx.ensure_raw(
            "rsi_14", lambda d: rsi_wilder_np(d["close"].to_numpy(dtype=float), self.period)
        )
        tail = rsi[-self.dull_bars:]
        rsi_last = float(tail[-1]) if tail.size and np.isfinite(tail[-1]) else None
        if rsi_last is None:
            return MethodVote(self.method_id, "WAIT", 0.0, ["RSI 未就绪(NaN)"])

        # ① 超买钝化：连续 3 根 > 70
        if tail.size >= self.dull_bars and bool((tail > self.overbought).all()):
            return MethodVote(
                self.method_id, "SELL", 0.5,
                reasons=[
                    f"RSI({self.period})超买钝化: 最近{self.dull_bars}根 "
                    + "/".join(f"{v:.1f}" for v in tail) + f" 均>{self.overbought:.0f}",
                ],
                params_snapshot=self.params(),
            )

        # ② 超卖观察
        if rsi_last < self.oversold:
            return MethodVote(
                self.method_id, "WAIT", 0.5,
                reasons=[f"RSI({self.period})={rsi_last:.1f}<{self.oversold:.0f}，超卖观察"],
                params_snapshot=self.params(),
            )

        # ③ 中性区
        if self.neutral_low <= rsi_last <= self.neutral_high:
            return MethodVote(
                self.method_id, "HOLD", 0.5,
                reasons=[f"RSI({self.period})={rsi_last:.1f}处于{self.neutral_low:.0f}-"
                         f"{self.neutral_high:.0f}中性区"],
                params_snapshot=self.params(),
            )

        # ④ 偏强/偏弱过渡区
        zone = "偏强" if rsi_last > self.neutral_high else "偏弱"
        return MethodVote(
            self.method_id, "HOLD" if rsi_last > self.neutral_high else "WAIT", 0.4,
            reasons=[f"RSI({self.period})={rsi_last:.1f}{zone}过渡区，未触发阈值"],
            params_snapshot=self.params(),
        )

    def params(self) -> dict:
        return {
            **super().params(),
            "period": self.period,
            "overbought": self.overbought,
            "oversold": self.oversold,
            "dull_bars": self.dull_bars,
        }
