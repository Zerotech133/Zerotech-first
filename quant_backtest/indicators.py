# -*- coding: utf-8 -*-
"""技术指标（pandas 向量化）。"""

import numpy as np
import pandas as pd


def ma(series, n):
    return series.rolling(window=int(n), min_periods=1).mean()


def ema(series, n):
    return series.ewm(span=int(n), adjust=False).mean()


def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=int(n), min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window=int(n), min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(
        axis=1
    )
    return tr.rolling(window=int(n), min_periods=1).mean()


def bollinger(series, n=20, k=2):
    mid = ma(series, n)
    std = series.rolling(window=int(n), min_periods=1).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def macd(series, fast=12, slow=26, signal=9):
    dif = ema(series, fast) - ema(series, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist
