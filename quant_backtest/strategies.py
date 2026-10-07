# -*- coding: utf-8 -*-
"""策略库：每个策略函数 (df, params) → position Series（1=做多, 0=空仓）。

新策略只需在此注册：返回与 df 同索引的 0/1 序列即可被 runner 调用。
仅做多（A股无做空）；信号在次日开盘生效（backtest 引擎 shift(1) 避免未来函数）。
"""
import numpy as np
import pandas as pd

import indicators as ind


def ma_cross(df, params):
    """均线交叉：fast MA 上穿 slow MA 做多，下穿空仓。"""
    fast = int(params.get("fast", 5))
    slow = int(params.get("slow", 20))
    fa = ind.ma(df["close"], fast)
    sl = ind.ma(df["close"], slow)
    pos = (fa > sl).astype(int)
    return pos


def rsi_reversal(df, params):
    """RSI 反转：RSI 跌破 oversold 做多，升破 overbought 空仓。
    事件帧语义：未首次触发前空仓；触发后持有至反向事件。"""
    period = int(params.get("period", 14))
    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    r = ind.rsi(df["close"], period)
    pos = pd.Series(np.nan, index=df.index)
    pos[r < oversold] = 1
    pos[r > overbought] = 0
    return pos.ffill().fillna(0).astype(int)


def boll_breakout(df, params):
    """布林突破：收盘价突破上轨做多，跌破中轨空仓。"""
    period = int(params.get("period", 20))
    k = float(params.get("k", 2))
    upper, mid, _ = ind.bollinger(df["close"], period, k)
    pos = (df["close"] > upper).astype(int)
    pos = pos.replace(0, pd.NA).ffill().fillna(0)
    pos[df["close"] < mid] = 0
    return pos.astype(int)


def trend_follow(df, params):
    """趋势跟随：收盘价在 N 日均线之上且均线斜率为正 → 做多。"""
    n = int(params.get("n", 20))
    m = ind.ma(df["close"], n)
    slope = m.diff(int(params.get("slope_lookback", 5)))
    pos = ((df["close"] > m) & (slope > 0)).astype(int)
    return pos


REGISTRY = {
    "ma_cross": ma_cross,
    "rsi_reversal": rsi_reversal,
    "boll_breakout": boll_breakout,
    "trend_follow": trend_follow,
}

# 各策略参数默认值（与函数内 get 默认一致）：参数寻优"当前配置"行匹配用
PARAM_DEFAULTS = {
    "ma_cross": {"fast": 5, "slow": 20},
    "rsi_reversal": {"period": 14, "oversold": 30, "overbought": 70},
    "boll_breakout": {"period": 20, "k": 2},
    "trend_follow": {"n": 20, "slope_lookback": 5},
}
