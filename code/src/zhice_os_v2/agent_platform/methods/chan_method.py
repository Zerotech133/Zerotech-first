"""缠论方法插件（简化缠论语义，MACD 背驰/顶分型卖点）。

复用策略（对 zhice 只读）：
- MACD 常量直接 import zhice chan.engine（12/26/9 口径钉死，不重写参数）。
- ≤600 根窗口（回测逐 bar / 线上滚动窗）用 numpy EMA 逐点公式——与 pandas
  ewm(span, adjust=False) 严格等价，为满足"30 天×37 标的 5 分钟"性能预算；
  >600 根整列路径仍走 zhice compute_macd_hist 复用。
- 完整 czsc 笔/中枢引擎依赖 zhice 内部对象链，逐 bar 成本过高，卖点语义按
  任务书批准的简化口径（背驰 0.7 / 顶分型+死叉 0.6 / 零轴分层 HOLD-WAIT）。
"""
from __future__ import annotations

import numpy as np

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)
from zhice_os_v2.domain.strategy.strategies.chan.engine import (
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    compute_macd_hist,
)

_DIVERGENCE_WINDOW = 30      # 背驰检测窗口（根）
_NUMPY_MAX_BARS = 600        # 超过则整列走 zhice compute_macd_hist


def ema_np(values: np.ndarray, span: int) -> np.ndarray:
    """EMA 逐点递推（alpha=2/(span+1)，与 pandas ewm(span, adjust=False) 等价）。"""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    if values.size == 0:
        return out
    out[0] = values[0]
    for i in range(1, values.size):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _dif_dea_np(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """DIF/DEA numpy 版（zhice 12/26/9 口径）。"""
    ema_fast = ema_np(closes, MACD_FAST)
    ema_slow = ema_np(closes, MACD_SLOW)
    dif = ema_fast - ema_slow
    dea = ema_np(dif, MACD_SIGNAL)
    return dif, dea


def _macd_dif_dea(closes) -> tuple:
    """pd.Series → (pd.Series, pd.Series)（zhice 同口径，测试/诊断用）。"""
    import pandas as pd

    dif, dea = _dif_dea_np(closes.to_numpy(dtype=float))
    return pd.Series(dif, index=closes.index), pd.Series(dea, index=closes.index)


def _dead_cross_points(dif: np.ndarray, dea: np.ndarray) -> list[int]:
    """窗口内死叉位置（DIF 自上而下穿 DEA）。"""
    if dif.size < 2:
        return []
    cross_down = (dif[1:] < dea[1:]) & (dif[:-1] >= dea[:-1])
    return [i + 1 for i in np.nonzero(cross_down)[0]]


def _top_fractal(highs: np.ndarray) -> bool:
    """顶分型：最近一根高点严格高于左右两根。"""
    if highs.size < 3:
        return False
    left, mid, right = float(highs[-3]), float(highs[-2]), float(highs[-1])
    return mid > left and mid > right


class ChanMethod(WatchMethod):
    """缠论（简化语义）卖点方法。"""

    method_id = "chan"
    display_name = "缠论动态退出"
    required_features = ["macd_dif", "macd_dea", "macd_hist"]

    def __init__(self, window: int = _DIVERGENCE_WINDOW) -> None:
        self.window = window

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        df = ctx.bars_df
        min_bars = MACD_SLOW + MACD_SIGNAL   # 35 根起步，指标才稳定
        if df is None or len(df) < max(min_bars, self.window):
            return MethodVote(self.method_id, "WAIT", 0.0,
                              [f"数据不足(len={0 if df is None else len(df)}<{min_bars})"])

        closes_all = ctx.ensure_array("close")
        highs_all = ctx.ensure_array("high")
        if len(df) > _NUMPY_MAX_BARS:   # 大窗口整列：直接复用 zhice 实现
            closes_series = df["close"].astype(float)
            hist_all = compute_macd_hist(closes_series).to_numpy(dtype=float)
            ema_fast = closes_series.ewm(span=MACD_FAST, adjust=False).mean()
            ema_slow = closes_series.ewm(span=MACD_SLOW, adjust=False).mean()
            dif_series = ema_fast - ema_slow
            dif_np = dif_series.to_numpy(dtype=float)
            dea_np = (dif_series.ewm(span=MACD_SIGNAL, adjust=False)
                      .mean().to_numpy(dtype=float))
            hist_last = float(hist_all[-1])
        else:
            dif_np, dea_np = _dif_dea_np(closes_all)
            hist_last = float((dif_np[-1] - dea_np[-1]) * 2.0)

        dif = dif_np[-self.window:]
        dea = dea_np[-self.window:]
        close_w = closes_all[-self.window:]
        highs_tail = highs_all[-3:]

        crosses = _dead_cross_points(dif, dea)
        dead_now = bool(dif.size and dif[-1] < dea[-1])

        # ① MACD 顶背驰：窗口内 ≥2 次死叉把走势分成两段冲高；
        #    第二段价格峰 > 第一段（价新高）但 DIF 峰 < 第一段（动能衰竭）。
        if len(crosses) >= 2 and dead_now:
            c1, c2 = crosses[-2], crosses[-1]
            dif_peak1 = float(dif[: c1 + 1].max())
            price_peak1 = float(close_w[: c1 + 1].max())
            dif_peak2 = float(dif[c1 + 1: c2 + 1].max())
            price_peak2 = float(close_w[c1 + 1: c2 + 1].max())
            if price_peak2 > price_peak1 and dif_peak2 < dif_peak1:
                return MethodVote(
                    self.method_id, "SELL", 0.7,
                    reasons=[
                        f"MACD顶背驰({self.window}根窗口{len(crosses)}次死叉): "
                        f"价格峰{price_peak2:.6g}创新高(前峰{price_peak1:.6g})但"
                        f"DIF峰{dif_peak2:.4g}未新高(前峰{dif_peak1:.4g})",
                        f"现DIF={dif[-1]:.4g} < DEA={dea[-1]:.4g}（死叉确认）",
                    ],
                    params_snapshot=self.params(),
                )

        # ② 顶分型 + 死叉（一卖/二卖形态）
        if dead_now and _top_fractal(highs_tail):
            return MethodVote(
                self.method_id, "SELL", 0.6,
                reasons=[
                    "顶分型成立（高点高于左右两根）+ MACD死叉",
                    f"hist={hist_last:.4g} 转弱",
                ],
                params_snapshot=self.params(),
            )

        # ③ 无卖点：DIF 零轴上方持有，下方观望
        dif_last = float(dif_np[-1])
        if dif_last > 0:
            return MethodVote(self.method_id, "HOLD", 0.3,
                              [f"DIF={dif_last:.4g}在零轴上方，多头结构未破坏"],
                              params_snapshot=self.params())
        return MethodVote(self.method_id, "WAIT", 0.3,
                          [f"DIF={dif_last:.4g}在零轴下方，观望"],
                          params_snapshot=self.params())

    def params(self) -> dict:
        return {
            **super().params(),
            "window": self.window,
            "macd": f"{MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL}",
            "semantics": "simplified_chan_v1",
        }
