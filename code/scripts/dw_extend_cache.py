# -*- coding: utf-8 -*-
"""把数据仓可用维度合并进个股多维缓存（资金流/融资/涨跌停/龙虎榜/筹码/研报/指数）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated")
CACHE = Path(r"C:\Temp\dw_multidim_cache.json")


def latest(df, key, date_col):
    df = df.dropna(subset=[key]).copy()
    df[date_col] = df[date_col].astype(str)
    return df.sort_values(date_col).groupby(key, as_index=False).tail(1)


def recs(df, key):
    out = {}
    for _, r in df.iterrows():
        out[str(r[key])] = {k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()}
    return out


def main() -> int:
    from zhice_os_v2.infrastructure.data_quality import attach_quality, preflight_cache_build

    gate = preflight_cache_build(producer="dw_extend_cache")  # 默认 off=no-op；warn 只记录
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    stocks = cache.get("stocks") or {}
    cn = {k for k in stocks if k.endswith((".SH", ".SZ", ".BJ"))}

    flow = latest(pd.read_parquet(DW / "moneyflow.parquet", columns=["ts_code", "trade_date", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount", "net_mf_amount"]).query("ts_code in @cn"), "ts_code", "trade_date")
    margin = latest(pd.read_parquet(DW / "margin_detail.parquet", columns=["ts_code", "trade_date", "rzye", "rqye", "rzrqye"]).query("ts_code in @cn"), "ts_code", "trade_date")
    limit = latest(pd.read_parquet(DW / "limit_list_d.parquet", columns=["ts_code", "trade_date", "limit", "up_stat", "limit_times", "pct_chg", "turnover_ratio"]).query("ts_code in @cn"), "ts_code", "trade_date")
    dragon = pd.read_parquet(DW / "top_list.parquet", columns=["ts_code", "trade_date", "reason", "net_amount", "net_rate", "amount"]).query("ts_code in @cn")
    dragon = dragon.sort_values("trade_date").groupby("ts_code", as_index=False).tail(3)
    cyq = latest(pd.read_parquet(DW / "cyq_perf.parquet", columns=["ts_code", "trade_date", "cost_50pct", "weight_avg", "winner_rate"]).query("ts_code in @cn"), "ts_code", "trade_date")
    research = pd.read_parquet(DW / "research_report.parquet", columns=["ts_code", "trade_date", "title", "author", "inst_csname", "report_type"])
    research = research[research["ts_code"].notna() & research["ts_code"].isin(cn)].copy()
    research = research.sort_values("trade_date").groupby("ts_code", as_index=False).tail(5)
    idx = pd.read_parquet(DW / "index_weight.parquet", columns=["index_code", "con_code", "trade_date", "weight"])
    idx = idx[idx["con_code"].isin(cn)].copy()
    idx["trade_date"] = idx["trade_date"].astype(str)
    idx = idx.sort_values("trade_date").groupby("con_code", as_index=False).tail(10)

    maps = {
        "flow": recs(flow, "ts_code"),
        "margin": recs(margin, "ts_code"),
        "limit": recs(limit, "ts_code"),
        "cyq": recs(cyq, "ts_code"),
    }
    dragon_map, research_map, idx_map = {}, {}, {}
    for _, r in dragon.iterrows():
        dragon_map.setdefault(str(r["ts_code"]), []).append({k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()})
    for _, r in research.iterrows():
        research_map.setdefault(str(r["ts_code"]), []).append({k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()})
    for _, r in idx.iterrows():
        idx_map.setdefault(str(r["con_code"]), []).append({"index_code": r["index_code"], "trade_date": r["trade_date"], "weight": None if pd.isna(r["weight"]) else r["weight"]})

    for code in cn:
        stocks[code].update({
            "flow": maps["flow"].get(code),
            "margin": maps["margin"].get(code),
            "limit": maps["limit"].get(code),
            "dragon": dragon_map.get(code, []),
            "cyq": maps["cyq"].get(code),
            "research": research_map.get(code, []),
            "index_membership": idx_map.get(code, []),
        })
    cache["schema"] = "zhice.dw_multidim_cache/v3"
    cache["extra_dimensions"] = ["flow", "margin", "limit", "dragon", "cyq", "research", "index_membership"]
    cache["coverage"] = {
        "flow": sum(1 for k in cn if stocks[k].get("flow")),
        "margin": sum(1 for k in cn if stocks[k].get("margin")),
        "limit": sum(1 for k in cn if stocks[k].get("limit")),
        "dragon": sum(1 for k in cn if stocks[k].get("dragon")),
        "cyq": sum(1 for k in cn if stocks[k].get("cyq")),
        "research": sum(1 for k in cn if stocks[k].get("research")),
        "index_membership": sum(1 for k in cn if stocks[k].get("index_membership")),
    }
    attach_quality(cache, gate)  # off 模式不写入；warn 写 quality{asof,degraded,stale_sources}
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cn": len(cn), "coverage": cache["coverage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
