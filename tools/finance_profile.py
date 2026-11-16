# -*- coding: utf-8 -*-
"""F1 基本面资料生成器：从本地 tushare 财务 parquet 取一份可复核的画像（2026-11-16 WO-FIN-PROFILE）。

## 为什么

F1 席位此前恒定输出 `unknown`——因为工单里只有技术信号，没有任何财务字段。而本地
`quant_backtest/data/tushare_curated/` 已有全市场财务数据（fina_indicator / income /
balancesheet / cashflow / daily_basic / stock_basic），完全够用，无需联网。

## 取哪些字段（只取能直接算、可复核的）

- 盈利能力：`roe` / `roe_dt` / `grossprofit_margin` / `netprofit_margin`
- 成长性：`or_yoy`（营收同比）/ `netprofit_yoy`（净利同比）/ `q_sales_yoy`
- 偿债：`debt_to_assets` / `current_ratio`
- 估值：`daily_basic.pe_ttm` / `pb` / `ps_ttm` / `turnover_rate`
- 行业：`stock_basic.industry`
- 趋势：最近 4 期的 roe / net_margin / or_yoy

**不推断、不补数**：缺的字段一律不出现在结果里；报告期滞后会写进 `warnings`。

    python -X utf8 tools/finance_profile.py 001228.SZ --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CURATED = REPO / "quant_backtest" / "data" / "tushare_curated"

FINA_COLS = ["ts_code", "ann_date", "end_date", "roe", "roe_dt", "grossprofit_margin",
             "netprofit_margin", "debt_to_assets", "current_ratio",
             "or_yoy", "netprofit_yoy", "q_sales_yoy", "ocf_yoy", "ocfps", "cash_ratio"]
LAG_WARN_DAYS = 120      # 报告期距今天超过 120 天就在 warnings 里点名


def _read(name: str, cols: list[str] | None, code: str):
    import pyarrow.parquet as pq
    p = CURATED / ("%s.parquet" % name)
    if not p.is_file():
        return None
    try:
        tbl = pq.read_table(p, columns=cols, filters=[("ts_code", "=", code)])
        return tbl.to_pandas() if tbl.num_rows else None
    except Exception:  # noqa: BLE001
        return None


def _f(v):
    try:
        if v is None:
            return None
        x = float(v)
        return None if x != x else round(x, 2)      # NaN → None
    except (TypeError, ValueError):
        return None


def profile(code: str) -> dict:
    out: dict = {"code": code, "source": "tushare_curated", "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    warns: list[str] = []

    # 1) 财务指标（主）
    fin = _read("fina_indicator", FINA_COLS, code)
    if fin is None or fin.empty:
        out["available"] = False
        out["warnings"] = ["fina_indicator 无此代码记录"]
        return out
    fin = fin.drop_duplicates(subset=["end_date"]).sort_values("end_date")
    last = fin.iloc[-1]
    end_date = str(last.get("end_date") or "")
    ann_date = str(last.get("ann_date") or "")
    out["report"] = {"end_date": end_date, "ann_date": ann_date}

    # 报告期滞后提示（不猜原因，只报事实）
    try:
        d_end = dt.datetime.strptime(end_date, "%Y%m%d").date()
        lag = (dt.date.today() - d_end).days
        out["report"]["lag_days"] = lag
        if lag > LAG_WARN_DAYS:
            warns.append("最新报告期 %s 距今天 %d 天（%d 天阈值），可能是新报告期未入库"
                         % (end_date, lag, LAG_WARN_DAYS))
    except ValueError:
        pass

    out["profitability"] = {"roe": _f(last.get("roe")), "roe_dt": _f(last.get("roe_dt")),
                            "gross_margin": _f(last.get("grossprofit_margin")),
                            "net_margin": _f(last.get("netprofit_margin"))}
    out["growth"] = {"revenue_yoy": _f(last.get("or_yoy")),
                     "profit_yoy": _f(last.get("netprofit_yoy")),
                     "q_sales_yoy": _f(last.get("q_sales_yoy"))}
    out["solvency"] = {"debt_to_assets": _f(last.get("debt_to_assets")),
                       "current_ratio": _f(last.get("current_ratio"))}
    cash = {k: _f(last.get(k)) for k in ("ocf_yoy", "ocfps", "cash_ratio")}
    cash = {k: v for k, v in cash.items() if v is not None}
    if cash:
        out["cash"] = cash

    # 2) 趋势（最近 4 期）
    tail = fin.tail(4)
    out["trend"] = [{"end_date": str(r.get("end_date")), "roe": _f(r.get("roe")),
                     "net_margin": _f(r.get("netprofit_margin")),
                     "revenue_yoy": _f(r.get("or_yoy"))} for _, r in tail.iterrows()]

    # 3) 估值（按需读，daily_basic 很大）
    db = _read("daily_basic", ["ts_code", "trade_date", "pe_ttm", "pb", "ps_ttm", "turnover_rate"], code)
    if db is not None and not db.empty:
        db = db.sort_values("trade_date")
        row = db.iloc[-1]
        out["valuation"] = {"trade_date": str(row.get("trade_date")),
                            "pe_ttm": _f(row.get("pe_ttm")), "pb": _f(row.get("pb")),
                            "ps_ttm": _f(row.get("ps_ttm")),
                            "turnover_rate": _f(row.get("turnover_rate"))}
    else:
        warns.append("daily_basic 无记录，估值缺失")

    # 4) 行业 / 名称
    sb = _read("stock_basic", ["ts_code", "name", "industry", "market", "list_date"], code)
    if sb is not None and not sb.empty:
        row = sb.iloc[0]
        out["basic"] = {"name": row.get("name"), "industry": row.get("industry"),
                        "market": row.get("market"), "list_date": str(row.get("list_date"))}

    out["available"] = True
    out["warnings"] = warns
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F1 基本面资料（本地 tushare parquet）")
    ap.add_argument("code")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    p = profile(args.code)
    print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0 if p.get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
