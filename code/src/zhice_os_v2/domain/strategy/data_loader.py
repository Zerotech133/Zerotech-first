"""策略数据加载。

从 V2 日K表读取近端窗口数据；未接入数据库时返回空 DataFrame。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

DEFAULT_LOOKBACK_CALENDAR_DAYS = 90

_COLUMNS = [
    "security_id",
    "ts_code",
    "name",
    "trade_date",
    "close",
    "turnover_cny",
    "pct_change",
]


def load_recent_bars(
    trade_date: date,
    lookback_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
    market: str = "CN",
) -> pd.DataFrame:
    try:
        session = get_session()
        start = trade_date - timedelta(days=lookback_days)
        rows = session.query(
            orm.InstrumentModel.instrument_id.label("security_id"),
            orm.BarDailyModel.symbol.label("ts_code"),
            orm.InstrumentModel.name,
            orm.BarDailyModel.trade_date,
            orm.BarDailyModel.close,
            orm.BarDailyModel.turnover.label("turnover_cny"),
        ).join(
            orm.InstrumentModel,
            orm.InstrumentModel.instrument_id == orm.BarDailyModel.instrument_id,
        ).filter(
            orm.BarDailyModel.market == market,
            orm.BarDailyModel.trade_date >= start,
            orm.BarDailyModel.trade_date <= trade_date,
        ).all()
        session.close()
        if not rows:
            return pd.DataFrame(columns=_COLUMNS)
        df = pd.DataFrame(
            [
                {
                    "security_id": r[0],
                    "ts_code": r[1],
                    "name": r[2] or "",
                    "trade_date": r[3],
                    "close": r[4],
                    "turnover_cny": r[5],
                    "pct_change": 0.0,
                }
                for r in rows
            ]
        )
        for col in ("close", "turnover_cny", "pct_change"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=_COLUMNS)
