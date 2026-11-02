# -*- coding: utf-8 -*-
r"""数据仓多维缓存：为当前涉及的 CN/HK/US 股票生成估值/财务/行业/公告快照。

只读 parquet + DB；输出 C:\Temp\dw_multidim_cache.json，不写 DB。
CRYPTO 无财务/估值源，不做伪造。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated")
OUT = Path(r"C:\Temp\dw_multidim_cache.json")


def norm_cn(code: str) -> str:
    c = str(code).strip().upper()
    if "." in c:
        base, suf = c.split(".", 1)
        if suf in ("SH", "SZ", "BJ"):
            return f"{base}.{suf}"
    if c.startswith("SH"):
        return c[2:] + ".SH"
    if c.startswith("SZ"):
        return c[2:] + ".SZ"
    if c.isdigit() and len(c) == 6:
        if c.startswith(("6", "68", "9")):
            return c + ".SH"
        if c.startswith(("4", "8")):
            return c + ".BJ"
        return c + ".SZ"
    return c


def norm_hk(code: str) -> str:
    c = str(code).strip().upper()
    base = c.split(".")[0]
    return base.zfill(5) + ".HK"


def norm_us(code: str) -> str:
    return str(code).strip().upper().split(".")[0]


def latest_rows(df: pd.DataFrame, key: str, date_col: str) -> pd.DataFrame:
    d = df.dropna(subset=[key]).copy()
    d[date_col] = d[date_col].astype(str)
    return d.sort_values(date_col).groupby(key, as_index=False).tail(1)


def clean(v):
    return None if pd.isna(v) else v


def main() -> int:
    from zhice_os_v2.infrastructure.data_quality import attach_quality, preflight_cache_build

    gate = preflight_cache_build(producer="dw_multidim_cache")  # 默认 off=no-op；warn 只记录
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    codes = {"CN": set(), "HK": set(), "US": set()}
    with e.connect() as c:
        for market, sql in (
            ("CN", "SELECT code FROM v2_selection_pool WHERE market='CN' AND pool_date=(SELECT max(pool_date) FROM v2_selection_pool) UNION SELECT code FROM v2_acad1_pool WHERE market='CN' AND pool_date=(SELECT max(pool_date) FROM v2_acad1_pool) UNION SELECT ts_code FROM v2_candidate_asset WHERE market='CN'"),
            ("HK", "SELECT code FROM v2_selection_pool WHERE market='HK' AND pool_date=(SELECT max(pool_date) FROM v2_selection_pool) UNION SELECT code FROM v2_acad1_pool WHERE market='HK' AND pool_date=(SELECT max(pool_date) FROM v2_acad1_pool) UNION SELECT ts_code FROM v2_candidate_asset WHERE market='HK'"),
            ("US", "SELECT code FROM v2_selection_pool WHERE market='US' AND pool_date=(SELECT max(pool_date) FROM v2_selection_pool) UNION SELECT code FROM v2_acad1_pool WHERE market='US' AND pool_date=(SELECT max(pool_date) FROM v2_acad1_pool) UNION SELECT ts_code FROM v2_candidate_asset WHERE market='US'"),
        ):
            for r in c.execute(sa.text(sql)).all():
                if r[0]:
                    codes[market].add(r[0])
    cn = {norm_cn(x) for x in codes["CN"] if x}
    hk = {norm_hk(x) for x in codes["HK"] if x}
    us = {norm_us(x) for x in codes["US"] if x}

    stocks: dict[str, dict] = {}

    # CN
    db = pd.read_parquet(DW / "daily_basic.parquet")
    val = latest_rows(db[db["ts_code"].isin(cn)], "ts_code", "trade_date")
    inc_cols = ["ts_code", "ann_date", "end_date", "total_revenue", "operate_profit", "n_income", "n_income_attr_p", "basic_eps", "total_profit"]
    inc = latest_rows(pd.read_parquet(DW / "income.parquet", columns=inc_cols).query("ts_code in @cn"), "ts_code", "end_date")
    fin_cols = ["ts_code", "ann_date", "end_date", "roe", "roa", "debt_to_assets", "grossprofit_margin", "netprofit_margin", "current_ratio", "quick_ratio", "eps", "bps"]
    fin = latest_rows(pd.read_parquet(DW / "fina_indicator.parquet", columns=fin_cols).query("ts_code in @cn"), "ts_code", "end_date")
    bs_cols = ["ts_code", "ann_date", "end_date", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int", "money_cap", "inventories", "goodwill"]
    bs = latest_rows(pd.read_parquet(DW / "balancesheet.parquet", columns=bs_cols).query("ts_code in @cn"), "ts_code", "end_date")
    ann = pd.read_parquet(DW / "anns_d.parquet", columns=["ann_date", "ts_code", "name", "title", "url"])
    ann = ann[ann["ts_code"].isin(cn)].copy()
    ann["ann_date"] = ann["ann_date"].astype(str)
    ann = ann.sort_values("ann_date").groupby("ts_code", as_index=False).tail(10)
    sw = pd.read_parquet(DW / "sw_l1_member.parquet", columns=["index_code", "con_code", "in_date", "out_date", "is_new"])
    sw = sw[sw["con_code"].isin(cn)].copy()
    sw["in_date"] = sw["in_date"].astype(str)
    sw = sw.sort_values("in_date").groupby("con_code", as_index=False).tail(1)
    maps = {
        "val": {str(r["ts_code"]): r for _, r in val.iterrows()},
        "inc": {str(r["ts_code"]): r for _, r in inc.iterrows()},
        "fin": {str(r["ts_code"]): r for _, r in fin.iterrows()},
        "bs": {str(r["ts_code"]): r for _, r in bs.iterrows()},
        "sw": {str(r["con_code"]): r for _, r in sw.iterrows()},
    }
    ann_map = {}
    for _, r in ann.iterrows():
        ann_map.setdefault(str(r["ts_code"]), []).append({k: clean(v) for k, v in r.to_dict().items()})
    for code in sorted(cn):
        v, i, f, b, s = (maps["val"].get(code), maps["inc"].get(code), maps["fin"].get(code), maps["bs"].get(code), maps["sw"].get(code))
        stocks[code] = {
            "market": "CN",
            "valuation": {k: clean(x) for k, x in v.to_dict().items()} if v is not None else None,
            "income": {k: clean(x) for k, x in i.to_dict().items()} if i is not None else None,
            "fina": {k: clean(x) for k, x in f.to_dict().items()} if f is not None else None,
            "balance": {k: clean(x) for k, x in b.to_dict().items()} if b is not None else None,
            "industry": {k: clean(x) for k, x in s.to_dict().items()} if s is not None else None,
            "announcements": ann_map.get(code, []),
        }

    # HK (wide fina_indicator 已含估值/利润/资产负债)
    hk_cols = ["ts_code", "name", "end_date", "std_report_date", "pe_ttm", "pb_ttm", "total_market_cap",
               "roe_avg", "roa", "debt_asset_ratio", "operate_income", "holder_profit", "basic_eps",
               "current_ratio", "total_assets", "total_liabilities", "currency"]
    if (DW / "hk_fina_indicator.parquet").exists():
        hkdf = pd.read_parquet(DW / "hk_fina_indicator.parquet", columns=hk_cols)
        hkdf = latest_rows(hkdf[hkdf["ts_code"].isin(hk)], "ts_code", "end_date")
        for _, r in hkdf.iterrows():
            code = str(r["ts_code"])
            d = {k: clean(v) for k, v in r.to_dict().items()}
            stocks[code] = {
                "market": "HK",
                "valuation": {"trade_date": d.get("end_date"), "pe_ttm": d.get("pe_ttm"), "pb": d.get("pb_ttm"), "total_mv": d.get("total_market_cap")},
                "income": {"end_date": d.get("end_date"), "total_revenue": d.get("operate_income"), "n_income_attr_p": d.get("holder_profit"), "basic_eps": d.get("basic_eps")},
                "fina": {"end_date": d.get("end_date"), "roe": d.get("roe_avg"), "roa": d.get("roa"), "debt_to_assets": d.get("debt_asset_ratio"), "current_ratio": d.get("current_ratio")},
                "balance": {"end_date": d.get("end_date"), "total_assets": d.get("total_assets"), "total_liab": d.get("total_liabilities")},
                "industry": None, "announcements": [],
            }

    # US
    us_cols = ["ts_code", "end_date", "notice_date", "operate_income", "parent_holder_netprofit", "basic_eps",
               "gross_profit_ratio", "net_profit_ratio", "roe_avg", "roa", "debt_asset_ratio", "current_ratio"]
    if (DW / "us_fina_indicator.parquet").exists():
        usdf = pd.read_parquet(DW / "us_fina_indicator.parquet", columns=us_cols)
        usdf = latest_rows(usdf[usdf["ts_code"].isin(us)], "ts_code", "end_date")
        for _, r in usdf.iterrows():
            code = str(r["ts_code"])
            d = {k: clean(v) for k, v in r.to_dict().items()}
            stocks[code] = {
                "market": "US",
                "valuation": None,
                "income": {"end_date": d.get("end_date"), "total_revenue": d.get("operate_income"), "n_income_attr_p": d.get("parent_holder_netprofit"), "basic_eps": d.get("basic_eps")},
                "fina": {"end_date": d.get("end_date"), "roe": d.get("roe_avg"), "roa": d.get("roa"), "debt_to_assets": d.get("debt_asset_ratio"), "grossprofit_margin": d.get("gross_profit_ratio")},
                "balance": None, "industry": None, "announcements": [],
            }

    def missing(kind, key):
        return sorted(k for k in (cn | hk | us) if not (stocks.get(k) or {}).get(key))

    out = {
        "schema": "zhice.dw_multidim_cache/v2",
        "read_only": True,
        "source": str(DW),
        "codes": {"CN": len(cn), "HK": len(hk), "US": len(us), "total": len(cn | hk | us)},
        "stocks": stocks,
        "missing": {
            "valuation": missing("valuation", "valuation"),
            "financial": missing("financial", "fina"),
            "industry": [x for x in missing("industry", "industry") if x.endswith((".SH", ".SZ", ".BJ"))],
            "announcements": [x for x in missing("announcements", "announcements") if x.endswith((".SH", ".SZ", ".BJ"))],
        },
    }
    attach_quality(out, gate)  # off 模式不写入；warn 写 quality{asof,degraded,stale_sources}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"codes": out["codes"], "missing": {k: len(v) for k, v in out["missing"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
