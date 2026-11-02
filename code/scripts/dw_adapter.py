# -*- coding: utf-8 -*-
"""张松数据仓只读适配：给 ACAD1 池补 pre_close / ST / list_date。

只读 parquet + 智策 PG；输出 JSON 到 C:\\Temp，不写数据仓、不写生产表。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data")
DAILY = DW / "tdx" / "ts_daily"
NAMECHANGE = DW / "tdx" / "ts_namechange"
BASIC = DW / "tdx" / "ts_stock_basic.parquet"
HK_DAILY = DW / "tushare" / "hk_daily"
HK_BASIC = DW / "tushare" / "hk_basic"
US_DAILY = DW / "tushare" / "us_daily"
US_BASIC = DW / "tushare" / "us_basic"


def _hk_code(code: str) -> str:
    return code if code.endswith(".HK") else code + ".HK"


def _latest_parquet(folder: Path):
    files = sorted(folder.glob("*.parquet"))
    return files[-1] if files else None


def to_dw_code(code: str) -> str:
    """智策池可能用 sh688004/sz000301；数据仓用 688004.SH/000301.SZ。"""
    c = (code or "").strip().lower()
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", c)
    if m:
        return m.group(2) + "." + m.group(1).upper()
    return code

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--json", default=r"C:\Temp\dw_enrichment.json")
    args = ap.parse_args()

    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        if args.date:
            day = date.fromisoformat(args.date)
        else:
            day = c.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        pool = c.execute(sa.text("""
            SELECT market, code, name FROM v2_acad1_pool
            WHERE pool_date=:d ORDER BY market, code
        """), {"d": day}).mappings().all()

    cn = [r["code"] for r in pool if r["market"] == "CN"]
    cn_dw = [to_dw_code(c) for c in cn]
    out = {"pool_date": str(day), "pool": len(pool), "cn_codes": len(cn),
           "daily": {}, "st_codes": [], "basic": {}}
    if cn and DAILY.exists():
        dataset = ds.dataset(str(DAILY), format="parquet")
        table = dataset.to_table(
            columns=["ts_code", "trade_date", "pre_close", "close", "pct_chg"],
            filter=ds.field("ts_code").isin(cn_dw))
        df = table.to_pandas().sort_values("trade_date")
        for code, g in df.groupby("ts_code"):
            last = g.iloc[-1]
            orig = cn[cn_dw.index(code)] if code in cn_dw else code
            out["daily"][orig] = {
                "trade_date": str(last["trade_date"]),
                "pre_close": float(last["pre_close"]) if last["pre_close"] is not None else None,
                "close": float(last["close"]) if last["close"] is not None else None,
                "pct_chg": float(last["pct_chg"]) if last["pct_chg"] is not None else None,
            }
    if NAMECHANGE.exists():
        dataset = ds.dataset(str(NAMECHANGE), format="parquet")
        table = dataset.to_table(columns=["ts_code", "name", "start_date", "end_date"])
        df = table.to_pandas()
        day_i = int(str(day).replace("-", ""))
        start = df["start_date"].fillna(0).astype(int)
        end = df["end_date"].fillna(99999999).astype(int)
        st = df[(start <= day_i) & (end >= day_i)
                & df["name"].astype(str).str.contains("ST", na=False)]["ts_code"].tolist()
        st_set = set(st)
        out["st_codes"] = sorted({orig for orig, dw in zip(cn, cn_dw) if dw in st_set})
    if BASIC.exists():
        import pandas as pd
        df = pd.read_parquet(BASIC)
        for orig, code in zip(cn, cn_dw):
            row = df[df["ts_code"] == code]
            if not row.empty:
                out["basic"][orig] = {
                    "name": row.iloc[0].get("name"),
                    "list_date": str(row.iloc[0].get("list_date") or ""),
                    "industry": row.iloc[0].get("industry"),
                }
    # HK：按 5 位代码映射 .HK，取最近交易日；基本面取最新快照
    hk = [r["code"] for r in pool if r["market"] == "HK"]
    hk_dw = [_hk_code(x) for x in hk]
    if hk and HK_DAILY.exists():
        dataset = ds.dataset(str(HK_DAILY), format="parquet")
        table = dataset.to_table(columns=["ts_code", "trade_date", "pre_close", "close", "pct_chg"],
                                 filter=ds.field("ts_code").isin(hk_dw))
        df = table.to_pandas().sort_values("trade_date")
        for code, g in df.groupby("ts_code"):
            last = g.iloc[-1]
            orig = hk[hk_dw.index(code)]
            out["daily"][orig] = {"trade_date": str(last["trade_date"]),
                                  "pre_close": float(last["pre_close"]) if last["pre_close"] is not None else None,
                                  "close": float(last["close"]) if last["close"] is not None else None,
                                  "pct_chg": float(last["pct_chg"]) if last["pct_chg"] is not None else None}
    hb = _latest_parquet(HK_BASIC) if HK_BASIC.exists() else None
    if hk and hb:
        import pandas as pd
        df = pd.read_parquet(hb)
        for orig, code in zip(hk, hk_dw):
            row = df[df["ts_code"] == code]
            if not row.empty:
                out["basic"][orig] = {"name": row.iloc[0].get("name"),
                                      "list_date": str(row.iloc[0].get("list_date") or ""),
                                      "industry": ""}
    # US：us_daily 按 symbol 单文件；basic 取最新快照
    us = [r["code"] for r in pool if r["market"] == "US"]
    for code in us:
        f = US_DAILY / (code + ".parquet")
        if f.exists():
            try:
                import pandas as pd
                df = pd.read_parquet(f).sort_values("trade_date")
                if not df.empty:
                    last = df.iloc[-1]
                    out["daily"][code] = {"trade_date": str(last["trade_date"]),
                                          "pre_close": float(last["pre_close"]) if last.get("pre_close") is not None else None,
                                          "close": float(last["close"]) if last.get("close") is not None else None,
                                          "pct_chg": float(last["pct_change"]) if last.get("pct_change") is not None else None}
            except Exception:
                pass
    ub = _latest_parquet(US_BASIC) if US_BASIC.exists() else None
    if us and ub:
        import pandas as pd
        df = pd.read_parquet(ub)
        for code in us:
            row = df[df["ts_code"] == code]
            if not row.empty:
                out["basic"][code] = {"name": row.iloc[0].get("name"),
                                      "list_date": str(row.iloc[0].get("list_date") or ""),
                                      "industry": ""}
    p = Path(args.json)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("pool", len(pool), "cn", len(cn),
          "daily_hit", len(out["daily"]), "st", len(out["st_codes"]),
          "basic_hit", len(out["basic"]), "saved", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
