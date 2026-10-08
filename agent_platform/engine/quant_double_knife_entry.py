# -*- coding: utf-8 -*-
"""Minute-level entry confirmation for double-knife daily candidates."""

from __future__ import annotations

import pandas as pd


def _intraday_vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    turnover = typical_price * df["volume"]
    return (turnover.cumsum() / df["volume"].cumsum().replace(0, pd.NA)).ffill().bfill()


def confirm_double_knife_entry(
    minute_df: pd.DataFrame,
    *,
    breakout_window: int = 20,
    volume_window: int = 20,
    breakout_threshold: float = -0.01,
    volume_ratio_threshold: float = 0.9,
    vwap_distance_limit: float = 0.025,
    open_strength_floor: float = -0.02,
    min_passes: int = 2,
) -> dict[str, float | bool | str]:
    """Confirm whether a daily double-knife candidate has intraday entry support.

    Required columns: open, high, low, close, volume
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(minute_df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if len(minute_df) < max(10, breakout_window):
        raise ValueError("minute bars insufficient for confirmation")

    df = minute_df.copy().sort_index()
    vwap = _intraday_vwap(df)
    prior_high = df["high"].rolling(window=breakout_window, min_periods=5).max().shift(1)
    breakout_strength = float((df["close"].iloc[-1] / prior_high.iloc[-1] - 1.0) if pd.notna(prior_high.iloc[-1]) else 0.0)

    volume_mean = df["volume"].rolling(window=volume_window, min_periods=5).mean().iloc[-1]
    volume_ratio = float(df["volume"].iloc[-1] / volume_mean) if pd.notna(volume_mean) and volume_mean else 1.0
    vwap_distance = float(abs(df["close"].iloc[-1] / vwap.iloc[-1] - 1.0)) if pd.notna(vwap.iloc[-1]) and vwap.iloc[-1] else 1.0
    open_relative_strength = float(df["close"].iloc[-1] / df["open"].iloc[0] - 1.0) if df["open"].iloc[0] else 0.0

    conditions = {
        "breakout": breakout_strength >= breakout_threshold,
        "volume": volume_ratio >= volume_ratio_threshold,
        "vwap": vwap_distance <= vwap_distance_limit,
        "open_strength": open_relative_strength >= open_strength_floor,
    }
    confirmed = sum(conditions.values()) >= min_passes

    note = "confirmed" if confirmed else "waiting_intraday_confirmation"
    if not confirmed:
        blockers = [key for key, passed in conditions.items() if not passed]
        note = ",".join(blockers) if blockers else note

    return {
        "entry_confirmed": bool(confirmed),
        "confirm_interval": "5m",
        "passes": sum(conditions.values()),
        "min_passes": min_passes,
        "breakout_strength": round(breakout_strength, 4),
        "volume_ratio": round(volume_ratio, 4),
        "vwap_distance": round(vwap_distance, 4),
        "open_relative_strength": round(open_relative_strength, 4),
        "note": note,
    }
