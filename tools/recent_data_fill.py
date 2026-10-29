# -*- coding: utf-8 -*-
"""研究相关数据「补齐」：最近缺口按日/按公告日拉取并落盘。

覆盖：limit_list_d、hk_daily、moneyflow（按交易日）；index_global（全球指数按日）；
forecast/express（业绩预告/快报按公告日）；top_list/top_inst（如缺）。
用法：
  python tools/recent_data_fill.py --days 7 --dry-run
  python tools/recent_data_fill.py --days 7
红线：只写自身 data/tushare 目录；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
VAULT = REPO / "tools" / "secrets" / "vault.py"
KG = Path(r"C:\Temp\kg_sync_task.log")
BY_DATE = {
    "limit_list_d": ("limit_list_d", "ts_code,trade_date,limit,amount,limit_amount,fd_amount"),
    "hk_daily": ("hk_daily", "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"),
    "moneyflow": ("moneyflow", "ts_code,trade_date,net_mf_amount"),
    "top_list": ("top_list", "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason"),
    "top_inst": ("top_inst", "trade_date,ts_code,exalter,side,buy,sell,net_buy,reason"),
}


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def call(tok: str, api: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": api, "token": tok, "params": params, "fields": fields}).encode()
    req = urllib.request.Request("http://api.tushare.pro", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    d = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    return {"code": d.get("code"), "msg": (d.get("msg") or "")[:120],
            "fields": (d.get("data") or {}).get("fields") or [],
            "items": (d.get("data") or {}).get("items") or []}


def trading_days(days: int) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs if Path(f).stem[2:].isdigit()]
    return ds[-days:]


def fill_by_date(tok, store: str, api: str, fields: str, days: int, dry: bool) -> dict:
    d = DATA / store
    target = trading_days(days)
    todo = [x for x in target if not (d / ("d_%s.parquet" % x)).exists()]
    ok = fail = 0
    for dt in todo:
        r = call(tok, api, {"trade_date": dt}, fields)
        if r["code"] != 0 or not r["items"]:
            fail += 1
            continue
        if not dry:
            table = pa.table({f: [row[j] for row in r["items"]] for j, f in enumerate(r["fields"])})
            tmp = d / ("d_%s.parquet.tmp" % dt)
            pq.write_table(table, tmp)
            tmp.replace(d / ("d_%s.parquet" % dt))
        ok += 1
        time.sleep(0.2)
    return {"store": store, "missing": len(todo), "filled": ok, "fail": fail}


def fill_index_global(tok, days: int, dry: bool) -> dict:
    d = DATA / "index_global"
    target = trading_days(days)
    filled = {}
    for dt in target:
        r = call(tok, "index_global", {"trade_date": dt}, "ts_code,trade_date,open,close,high,low,pre_close,change,pct_chg")
        if r["code"] != 0 or not r["items"]:
            continue
        by_code = {}
        for row in r["items"]:
            rec = dict(zip(r["fields"], row))
            by_code.setdefault(rec["ts_code"], []).append(rec)
        for code, rows in by_code.items():
            f = d / ("%s.parquet" % code)
            if not f.exists():
                continue
            if dry:
                filled[code] = filled.get(code, 0) + len(rows)
                continue
            old = pq.read_table(f)
            old_rows = old.to_pylist()
            new_dates = {r["trade_date"] for r in rows}
            merged = [r for r in old_rows if r.get("trade_date") not in new_dates] + rows
            table = pa.table({c: [r.get(c) for r in merged] for c in old.schema.names})
            tmp = f.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp)
            tmp.replace(f)
            filled[code] = filled.get(code, 0) + len(rows)
        time.sleep(0.2)
    return {"store": "index_global", "codes_updated": len(filled), "rows": sum(filled.values())}


def fill_forecast(tok, store: str, api: str, fields: str, days: int, dry: bool) -> dict:
    d = DATA / store
    files = sorted(d.glob("part_*.parquet"))
    if not files:
        return {"store": store, "error": "no files"}
    # 找到 2026 年度文件（按 max ann_date 年份）
    target_file = files[-1]
    for f in files:
        tb = pq.read_table(f, columns=["ann_date"])
        anns = [str(x) for x in tb.column("ann_date").to_pylist() if x]
        if anns and max(anns)[:4] == "2026":
            target_file = f
            break
    old = pq.read_table(target_file)
    old_rows = old.to_pylist()
    existing = {(r.get("ts_code"), str(r.get("ann_date")), str(r.get("end_date"))) for r in old_rows}
    added = []
    today = datetime.now()
    for i in range(days):
        dt = (today - timedelta(days=i)).strftime("%Y%m%d")
        r = call(tok, api, {"ann_date": dt}, fields)
        if r["code"] != 0 or not r["items"]:
            continue
        for row in r["items"]:
            rec = dict(zip(r["fields"], row))
            key = (rec.get("ts_code"), str(rec.get("ann_date")), str(rec.get("end_date")))
            if key not in existing:
                added.append(rec)
                existing.add(key)
        time.sleep(0.15)
    if added and not dry:
        merged = old_rows + added
        table = pa.table({c: [r.get(c) for r in merged] for c in old.schema.names})
        tmp = target_file.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        tmp.replace(target_file)
    return {"store": store, "added": len(added), "file": target_file.name}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tok = token()
    if KG.exists():
        tail = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        if any("in_progress" in x for x in tail):
            print("kg 日志有进行中操作，停止", file=sys.stderr)
            return 3
    results = []
    for store, (api, fields) in BY_DATE.items():
        results.append(fill_by_date(tok, store, api, fields, args.days, args.dry_run))
    results.append(fill_index_global(tok, args.days, args.dry_run))
    results.append(fill_forecast(tok, "forecast", "forecast_vip",
                                 "ts_code,ann_date,end_date,type,p_change_min,p_change_max,last_parent_net,first_ann_date,summary,change_reason",
                                 args.days, args.dry_run))
    results.append(fill_forecast(tok, "express", "express_vip",
                                 "ts_code,ann_date,end_date,revenue,n_income,total_profit,eps,total_assets,total_hldr_eqy_exc_min_int",
                                 args.days, args.dry_run))
    KG.open("a", encoding="utf-8").write(
        "recent_data_fill date=%s days=%d dry=%s results=%s\n" % (
            datetime.now().astimezone().isoformat(timespec="seconds"), args.days, args.dry_run,
            json.dumps(results, ensure_ascii=False)))
    print(json.dumps({"days": args.days, "dry_run": args.dry_run, "results": results},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
