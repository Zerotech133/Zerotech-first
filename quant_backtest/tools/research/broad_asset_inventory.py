# -*- coding: utf-8 -*-
"""广域资产盘点：期权/可转债/港股通/两融/期货/A+H/大宗/解禁/减持
只读。输出每表 schema + 行数 + 时间跨度 + 唯一标的数。
"""
import os
import sys
import json
import glob
import duckdb

BASE = r"C:/项目/老田的AI助手/quant_backtest/data/tdx"

TABLES = [
    "ts_opt_daily", "ts_opt_basic",
    "ts_cb_daily", "ts_cb_basic", "ts_cb_call",
    "ts_ggt_top10", "ts_hsgt_top10",
    "ts_margin_detail", "ts_slb_sec",
    "ts_stk_ah_comparison", "ts_block_trade",
    "ts_fut_holding", "ts_limit_step", "ts_limit_cpt_list",
    "ts_share_float", "ts_stk_holdertrade",
]


def pick_date_col(cols):
    for c in ("trade_date", "ann_date", "cal_date", "end_date", "date",
              "float_date", "in_date", "list_date", "trade_time", "month"):
        if c in cols:
            return c
    for c in cols:
        if "date" in c.lower() or c.lower() == "month":
            return c
    return None


def pick_key_col(cols):
    for c in ("ts_code", "code", "con_code", "symbol", "opt_code"):
        if c in cols:
            return c
    return None


def main():
    con = duckdb.connect()
    out = []
    for t in TABLES:
        d = os.path.join(BASE, t)
        files = glob.glob(os.path.join(d, "**", "*.parquet"), recursive=True)
        if not files:
            out.append({"table": t, "status": "NO_FILE"})
            continue
        g = (d.replace("\\", "/") + "/**/*.parquet")
        rec = {"table": t, "files": len(files)}
        try:
            cols = [r[0] for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{g}')").fetchall()]
            rec["n_cols"] = len(cols)
            rec["columns"] = cols
            dc = pick_date_col(cols)
            kc = pick_key_col(cols)
            sel = ["count(*) AS n"]
            if dc:
                sel.append(f"min({dc}) AS d0")
                sel.append(f"max({dc}) AS d1")
                sel.append(f"count(DISTINCT {dc}) AS n_dates")
            if kc:
                sel.append(f"count(DISTINCT {kc}) AS n_keys")
            row = con.execute(
                f"SELECT {','.join(sel)} FROM read_parquet('{g}')").fetchone()
            names = [s.split(" AS ")[-1] for s in sel]
            rec.update(dict(zip(names, [str(x) for x in row])))
            rec["status"] = "OK"
        except Exception as e:
            rec["status"] = "ERR"
            rec["err"] = str(e)[:300]
        out.append(rec)

    p = r"C:/项目/老田的AI助手/quant_backtest/reports/broad_asset_inventory.json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for r in out:
        if r["status"] != "OK":
            print(f"{r['table']:24s} {r['status']} {r.get('err','')}")
            continue
        print(f"{r['table']:24s} rows={int(r.get('n','0')):>12,} "
              f"keys={r.get('n_keys','-'):>6} dates={r.get('n_dates','-'):>5} "
              f"[{r.get('d0','-')} ~ {r.get('d1','-')}] cols={r['n_cols']}")
    print("\nJSON ->", p)


if __name__ == "__main__":
    main()
