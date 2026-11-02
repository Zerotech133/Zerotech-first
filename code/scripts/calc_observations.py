# -*- coding: utf-8 -*-
"""观察记录计算：信号日收盘 → T+1/5/10/20 收盘收益、命中、期间最大回撤。

数据源：quant_backtest/data/tdx（只读）。数据未到期的窗口保持 NULL/partial，
不硬凑；幂等更新 v2_strategy_observation。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pyarrow.dataset as ds
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

TDX = r"C:\项目\老田的AI助手\quant_backtest\data\tdx"
HORIZONS = (1, 5, 10, 20)


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _load(codes: list[str]) -> pd.DataFrame:
    d = ds.dataset(TDX + "/ts_daily", format="parquet")
    t = d.to_table(filter=ds.field("ts_code").isin(codes),
                   columns=["ts_code", "trade_date", "close"])
    df = t.to_pandas().sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d").dt.date
    return df


def main() -> int:
    engine = _engine()
    with engine.begin() as c:
        rows = [dict(r) for r in c.execute(sa.text("""
            SELECT id, strategy_id, signal_date, code, status
            FROM v2_strategy_observation
            WHERE status IN ('pending','partial')
            ORDER BY signal_date, code
        """)).mappings().all()]
        if not rows:
            print("no pending observations")
            return 0
        df = _load(sorted({r["code"] for r in rows}))
        done = partial = pending = 0
        for r in rows:
            g = df[df["ts_code"] == r["code"]].reset_index(drop=True)
            idx = g.index[g["trade_date"] == r["signal_date"]].tolist()
            if not idx:
                pending += 1
                continue
            i = idx[0]
            base = float(g["close"].iloc[i])
            entry_date = g["trade_date"].iloc[i + 1] if i + 1 < len(g) else None
            vals = {}
            for h in HORIZONS:
                j = i + h
                if j < len(g) and base > 0:
                    vals[h] = float(g["close"].iloc[j] / base - 1)
                else:
                    vals[h] = None
            max_dd = None
            last = max([i + h for h in HORIZONS if i + h < len(g)], default=i)
            if last > i and base > 0:
                path = g["close"].iloc[i:last + 1].astype(float) / base - 1
                max_dd = float(path.min())
            have1 = vals[1] is not None
            full = vals[20] is not None
            status = "done" if full else ("partial" if have1 else "pending")
            if status == "done":
                done += 1
            elif status == "partial":
                partial += 1
            else:
                pending += 1
            c.execute(sa.text("""
                UPDATE v2_strategy_observation SET
                  entry_date=:entry_date,
                  ret_t1=:r1, ret_t5=:r5, ret_t10=:r10, ret_t20=:r20,
                  hit=:hit, max_dd=:mdd, status=:status, updated_at=now()
                WHERE id=:id
            """), {"entry_date": entry_date, "r1": vals[1], "r5": vals[5],
                    "r10": vals[10], "r20": vals[20],
                    "hit": (vals[1] > 0) if have1 else None,
                    "mdd": max_dd, "status": status, "id": r["id"]})
        print("done=%d partial=%d pending=%d" % (done, partial, pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
