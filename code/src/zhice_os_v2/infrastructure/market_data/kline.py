"""K线重采样服务。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.market_data.memory import KlineBar

MINUTE_PERIODS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
DAY_PERIODS = {"day": "D", "week": "W-FRI"}


def resample_bars(bars: list[KlineBar], period: str, limit: int = 300) -> list[dict]:
    if not bars:
        return []
    df = pd.DataFrame(
        [
            {
                "time": pd.to_datetime(b.time, utc=True),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
    ).sort_values("time")

    if period in MINUTE_PERIODS:
        minutes = MINUTE_PERIODS[period]
        if minutes == 1:
            out = df.tail(limit)
        else:
            out = (
                df.set_index("time")
                .resample(f"{minutes}min")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna(subset=["close"])
                .tail(limit)
                .reset_index()
            )
    elif period in DAY_PERIODS:
        rule = DAY_PERIODS[period]
        out = (
            df.set_index("time")
            .resample(rule)
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["close"])
            .tail(limit)
            .reset_index()
        )
    else:
        raise ValueError(f"unsupported period: {period}")

    return [
        {
            "time": r["time"].isoformat() if hasattr(r["time"], "isoformat") else str(r["time"]),
            "open": round(float(r["open"]), 6),
            "high": round(float(r["high"]), 6),
            "low": round(float(r["low"]), 6),
            "close": round(float(r["close"]), 6),
            "volume": float(r["volume"]),
        }
        for r in out.to_dict("records")
    ]


def fetch_db_minute_bars(ts_code: str, period: str, limit: int = 300) -> list[dict] | None:
    """从 V2 分钟K表读取真实数据并重采样；无数据返回 None。"""
    try:
        session = get_session()
        rows = session.query(
            orm.BarMinuteModel.bar_time,
            orm.BarMinuteModel.open,
            orm.BarMinuteModel.high,
            orm.BarMinuteModel.low,
            orm.BarMinuteModel.close,
            orm.BarMinuteModel.volume,
        ).filter(orm.BarMinuteModel.symbol == ts_code).order_by(
            orm.BarMinuteModel.bar_time.desc()
        ).limit(limit * 10).all()
        session.close()
        if not rows:
            return None
        seen: dict[Any, Any] = {}
        for row in rows:
            seen.setdefault(row[0], row)
        bars = [
            KlineBar(
                time=row[0].isoformat(),
                open=float(row[1] or 0),
                high=float(row[2] or 0),
                low=float(row[3] or 0),
                close=float(row[4] or 0),
                volume=float(row[5] or 0),
            )
            for row in seen.values()
        ]
        return resample_bars(bars, period, limit=limit)
    except Exception:
        return None


def fetch_db_bars(ts_code: str, period: str, limit: int = 300) -> list[dict] | None:
    """从 V2 日K表读取真实数据并重采样；无数据返回 None。"""
    try:
        session = get_session()
        rows = session.query(
            orm.BarDailyModel.trade_date,
            orm.BarDailyModel.open,
            orm.BarDailyModel.high,
            orm.BarDailyModel.low,
            orm.BarDailyModel.close,
            orm.BarDailyModel.volume,
        ).filter(orm.BarDailyModel.symbol == ts_code).order_by(
            orm.BarDailyModel.trade_date.desc()
        ).limit(limit * 2).all()
        session.close()
        if not rows:
            return None
        seen: dict[Any, Any] = {}
        for row in rows:
            seen.setdefault(row[0], row)
        bars = [
            KlineBar(
                time=row[0].isoformat(),
                open=float(row[1] or 0),
                high=float(row[2] or 0),
                low=float(row[3] or 0),
                close=float(row[4] or 0),
                volume=float(row[5] or 0),
            )
            for row in seen.values()
        ]
        return resample_bars(bars, period, limit=limit)
    except Exception:
        return None
