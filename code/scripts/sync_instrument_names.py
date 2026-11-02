# -*- coding: utf-8 -*-
"""从数据仓 stock_basic/hk_basic/us_basic 同步中文名称到 v2_instrument。

只使用包含中文的名称；找不到保留原值，不造假。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated")
CJK = re.compile(r"[\u4e00-\u9fff]")
OUT = Path(r"C:\Temp\instrument_name_sync.json")


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    sources = []
    for market, fname, col in (
        ("CN", "stock_basic.parquet", "ts_code"),
        ("HK", "hk_basic.parquet", "ts_code"),
        ("US", "us_basic.parquet", "ts_code"),
    ):
        p = DW / fname
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=[col, "name"])
        df = df.dropna(subset=[col, "name"]).drop_duplicates(subset=[col], keep="last")
        sources.append((market, df))
    updates = []
    with e.connect() as c:
        for market, df in sources:
            existing = {
                str(r[0]) for r in c.execute(sa.text(
                    "SELECT symbol FROM v2_instrument WHERE market=:m"), {"m": market}).all()
            }
            for _, r in df.iterrows():
                code = str(r.iloc[0])
                name = str(r["name"]).strip()
                if not name or not CJK.search(name):
                    continue
                base = code.split(".")[0]
                for sym in (code, base, base.zfill(5) if market == "HK" else base):
                    if sym in existing:
                        updates.append({"m": market, "s": sym, "n": name})
                        break
    # 缺失的 A 股主数据：INSERT（先补 688836.SH 宇树科技等）
    inserts = []
    with e.connect() as c:
        existing_cn = {str(r[0]) for r in c.execute(sa.text("SELECT symbol FROM v2_instrument WHERE market='CN'")).all()}
    for market, df in sources:
        if market != "CN":
            continue
        for _, r in df.iterrows():
            code = str(r.iloc[0])
            name = str(r["name"]).strip()
            if not name or not CJK.search(name) or code in existing_cn:
                continue
            suffix = code.split(".")[-1].upper()
            exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix, "SSE")
            inserts.append({"m": "CN", "s": code, "n": name, "e": exchange, "iid": "CN:" + code})
    t = sa.table("v2_instrument", sa.column("market"), sa.column("symbol"), sa.column("name"))
    with e.begin() as c:
        for u in updates:
            c.execute(t.update().where((t.c.market == u["m"]) & (t.c.symbol == u["s"])).values(name=u["n"]))
        for x in inserts:
            c.execute(sa.text("""
                INSERT INTO v2_instrument(instrument_id, market, symbol, exchange, currency, name, lot_size, status, created_at)
                VALUES (:iid, :m, :s, :e, 'CNY', :n, 100, 'ACTIVE', CURRENT_TIMESTAMP)
                ON CONFLICT (instrument_id) DO UPDATE SET name=EXCLUDED.name
            """), x)
    OUT.write_text(__import__("json").dumps({"updated": len(updates), "inserted": len(inserts), "sample": updates[:10]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(__import__("json").dumps({"updated": len(updates), "inserted": len(inserts), "sample": inserts[:5]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
