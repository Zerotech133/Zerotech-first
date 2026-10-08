# -*- coding: utf-8 -*-
"""Double-knife signal prototype for A-share daily selection."""

from __future__ import annotations

import numpy as np
import pandas as pd


PROFILE_CONFIG = {
    "strict": {
        "range_change_limit": 0.30,
        "amount_20d_threshold": 3e6,
        "max_ma24_ratio": 1.20,
        "max_ma54_ratio": 1.30,
        "require_two_day_up": True,
        "blocked_regimes": {"trend_down"},
    },
    "relaxed": {
        "range_change_limit": 0.50,
        "amount_20d_threshold": 1e6,
        "max_ma24_ratio": 1.30,
        "max_ma54_ratio": 1.50,
        "require_two_day_up": False,
        "blocked_regimes": {"trend_down"},
    },
}


def _safe_minmax(series: pd.Series, window: int) -> pd.Series:
    rolling_min = series.rolling(window=window, min_periods=max(5, window // 3)).min()
    rolling_max = series.rolling(window=window, min_periods=max(5, window // 3)).max()
    spread = (rolling_max - rolling_min).replace(0, np.nan)
    normalized = (series - rolling_min) / spread
    return normalized.clip(0, 1).fillna(0.5)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif, dea


def _cross_star(df: pd.DataFrame) -> pd.Series:
    body_ratio = (df["open"] - df["close"]).abs() / ((df["high"] - df["low"]).abs() + 1e-4)
    return (
        (body_ratio <= 0.30)
        & (df["high"] > np.maximum(df["open"], df["close"]))
        & (df["low"] < np.minimum(df["open"], df["close"]))
    )


def _compute_regime(close: pd.Series, high: pd.Series, low: pd.Series, amount: pd.Series) -> pd.DataFrame:
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema_slope = ema20.pct_change(10).fillna(0.0)
    slope_score = _safe_minmax(ema_slope, 60)

    breakout_high = close.rolling(window=20, min_periods=5).max()
    breakout_hit = (close >= breakout_high.shift(1).fillna(breakout_high)).astype(float)
    breakout_rate = breakout_hit.rolling(window=20, min_periods=5).mean().fillna(0.0)

    signed_flow = np.sign(close.diff().fillna(0.0)) * amount
    volume_balance = (
        signed_flow.rolling(window=20, min_periods=5).sum()
        / amount.rolling(window=20, min_periods=5).sum().replace(0, np.nan)
    ).fillna(0.0).clip(-1, 1)

    downside_ratio = close.pct_change().lt(0).rolling(window=30, min_periods=5).mean().fillna(0.5)
    close_location = ((close - low) / (high - low).replace(0, np.nan)).fillna(0.5)
    realized_vol = close.pct_change().rolling(window=30, min_periods=5).std().fillna(0.0)
    vol_score = _safe_minmax(realized_vol, 90)
    sentiment = ((1.0 - downside_ratio) * 0.4 + close_location * 0.4 + (1.0 - vol_score) * 0.2).clip(0, 1)

    tsi = ((slope_score * 0.6) + (breakout_rate * 0.4)).clip(0, 1.5)

    regime = pd.Series('range', index=close.index, dtype='object')
    regime[(tsi >= 0.7) & (volume_balance >= 0.1) & (sentiment >= 0.55)] = 'trend_up'
    regime[(tsi <= 0.3) & (volume_balance <= -0.1) & (sentiment <= 0.45)] = 'trend_down'
    regime[(tsi >= 0.55) & (volume_balance.abs() < 0.1)] = 'rotation'

    return pd.DataFrame(
        {
            'regime_tsi': tsi,
            'regime_volume_balance': volume_balance,
            'regime_sentiment': sentiment,
            'regime_label': regime,
        },
        index=close.index,
    )


def quant_double_knife(df: pd.DataFrame, profile: str = "strict") -> pd.DataFrame:
    """Return per-bar double-knife signal diagnostics.

    Required columns: open, high, low, close, amount
    """
    if profile not in PROFILE_CONFIG:
        raise ValueError(f"unknown profile: {profile}")

    required = {"open", "high", "low", "close", "amount"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    cfg = PROFILE_CONFIG[profile]
    frame = df.copy()
    close = frame["close"]
    open_ = frame["open"]
    high = frame["high"]
    low = frame["low"]
    amount = frame["amount"]

    range_ = (high - low).abs()
    cross = _cross_star(frame)
    cross_2d = cross & cross.shift(1).fillna(False)

    range_change_ratio = ((range_ - range_.shift(1)).abs() / np.maximum(range_.shift(1), 1e-4)).fillna(np.inf)
    range_ok = range_change_ratio <= cfg["range_change_limit"]

    pct_change_1 = close.pct_change().fillna(0.0)
    pct_change_2 = close.shift(1).pct_change().fillna(0.0)
    if cfg["require_two_day_up"]:
        price_trend_ok = (
            (close >= close.shift(1))
            & (pct_change_1 <= 0.03)
            & (pct_change_2 <= 0.03)
        )
    else:
        price_trend_ok = close > close.shift(2)

    price_ok = price_trend_ok & (close >= open_) & close.between(6, 200)

    amount_20d = amount.rolling(20, min_periods=20).sum()
    amount_ok = amount_20d > cfg["amount_20d_threshold"]

    ma24 = close.rolling(24, min_periods=24).mean()
    ma54 = close.rolling(54, min_periods=54).mean()
    ma_stack_ok = (ma24 >= ma54) & (close >= ma24) & (close >= ma54)
    ma_distance_ok = (close <= ma24 * cfg["max_ma24_ratio"]) & (close <= ma54 * cfg["max_ma54_ratio"])

    dif, dea = _macd(close)
    macd_ok = (dif >= dea) & (dif > 0)

    regime_frame = _compute_regime(close, high, low, amount)
    regime_ok = ~regime_frame['regime_label'].isin(cfg['blocked_regimes'])

    signal = (
        cross_2d
        & range_ok
        & price_ok
        & amount_ok
        & ma_stack_ok
        & ma_distance_ok
        & macd_ok
        & regime_ok
    ).fillna(False)

    return pd.DataFrame(
        {
            "double_knife_signal": signal.astype(bool),
            "double_knife_profile": profile,
            "cross_star": cross.astype(bool),
            "cross_star_2d": cross_2d.astype(bool),
            "range_change_ratio": range_change_ratio.replace([np.inf, -np.inf], np.nan),
            "range_ok": range_ok.astype(bool),
            "price_ok": price_ok.astype(bool),
            "amount_20d": amount_20d,
            "amount_ok": amount_ok.astype(bool),
            "ma24": ma24,
            "ma54": ma54,
            "ma_stack_ok": ma_stack_ok.astype(bool),
            "ma_distance_ok": ma_distance_ok.astype(bool),
            "dif": dif,
            "dea": dea,
            "macd_ok": macd_ok.astype(bool),
            "regime_tsi": regime_frame['regime_tsi'],
            "regime_volume_balance": regime_frame['regime_volume_balance'],
            "regime_sentiment": regime_frame['regime_sentiment'],
            "regime_label": regime_frame['regime_label'],
            "regime_ok": regime_ok.astype(bool),
        },
        index=df.index,
    )

