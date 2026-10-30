# -*- coding: utf-8 -*-
"""财报数据补齐（按报告期）：拉 Tushare *_vip → 合并进按股票 parquet（增量、去重、可重拉）。

背景：TushareSync 增量不覆盖财报，本地 income/balancesheet/cashflow/fina_indicator
只到 2026-06-30（中报），缺 2026Q3。本工具按 period 拉全市场数据并合并到按股票文件。

用法：
  python tools/fin_report_sync.py --period 20260930 --dry-run
  python tools/fin_report_sync.py --period 20260930
红线：只写自身数据目录（data/tushare/{表}），不碰数据库/服务；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
VAULT = REPO / "tools" / "secrets" / "vault.py"
KG = Path(r"C:\Temp\kg_sync_task.log")
APIS = {
    "income": "income_vip",
    "balancesheet": "balancesheet_vip",
    "cashflow": "cashflow_vip",
    "fina_indicator": "fina_indicator_vip",
}
KEY_COLS = ("ts_code", "end_date", "report_type")


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    t = (p.stdout or b"").decode("utf-8", "replace").strip()
    if not t:
        raise RuntimeError("no token")
    return t


def fetch(tok: str, api: str, period: str) -> dict:
    body = json.dumps({"api_name": api, "token": tok, "params": {"period": period}, "fields": ""}).encode()
    req = urllib.request.Request("http://api.tushare.pro", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    d = json.loads(safe_urlopen(req, timeout=120).read().decode())
    return {"code": d.get("code"), "msg": (d.get("msg") or "")[:150],
            "fields": (d.get("data") or {}).get("fields") or [],
            "items": (d.get("data") or {}).get("items") or []}


def fname(ts_code: str) -> str:
    return ts_code.replace(".", "_") + ".parquet"


def merge_store(store: str, fields: list[str], items: list[list], dry_run: bool) -> dict:
    d = DATA / store
    files = list(d.glob("*.parquet"))  # noqa: F841 - 既有未使用变量（批2不改行为）
    by_code = defaultdict(list)
    for row in items:
        rec = dict(zip(fields, row))
        by_code[rec.get("ts_code")].append(rec)
    written = skipped = created = 0
    t0 = time.time()
    for i, (code, rows) in enumerate(by_code.items(), 1):
        if not code:
            continue
        f = d / fname(code)
        try:
            if f.exists():
                old = pq.read_table(f)
                old_rows = old.to_pylist()
                schema_cols = old.schema.names
            else:
                old_rows, schema_cols = [], fields
                created += 1
            # 去重：key=(ts_code,end_date,report_type)，新数据优先（取 ann_date 较新）
            merged = {}
            for r in old_rows:
                key = tuple(str(r.get(k) if r.get(k) is not None else "") for k in KEY_COLS)
                merged[key] = r
            for r in rows:
                key = tuple(str(r.get(k) if r.get(k) is not None else "") for k in KEY_COLS)
                prev = merged.get(key)
                if prev is None or str(r.get("ann_date") or "") >= str(prev.get("ann_date") or ""):
                    merged[key] = r
            out_rows = list(merged.values())
            if not dry_run:
                cols = schema_cols
                table = pa.table({c: [r.get(c) for r in out_rows] for c in cols})
                tmp = f.with_suffix(".parquet.tmp")
                pq.write_table(table, tmp)
                tmp.replace(f)
                written += 1
        except Exception as e:  # noqa: BLE001
            skipped += 1
            if skipped <= 3:
                print("  SKIP", code, type(e).__name__, str(e)[:80])
        if i % 1000 == 0:
            print("  [%s] %d/%d written=%d skipped=%d %.0fs" % (store, i, len(by_code), written, skipped, time.time() - t0), flush=True)
    return {"store": store, "stocks": len(by_code), "written": written, "created": created,
            "skipped": skipped, "seconds": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="20260930")
    ap.add_argument("--stores", default="income,balancesheet,cashflow,fina_indicator")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    stores = [s.strip() for s in args.stores.split(",") if s.strip() in APIS]
    tok = token()
    KG_TAIL = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:] if KG.exists() else []
    if any("in_progress" in x for x in KG_TAIL):
        print("kg 日志有进行中操作，停止", file=sys.stderr)
        return 3
    results = []
    for store in stores:
        r = fetch(tok, APIS[store], args.period)
        print("[%s] %s rows=%d code=%s %s" % (store, APIS[store], len(r["items"]), r["code"], r["msg"]), flush=True)
        if r["code"] != 0 or not r["items"]:
            results.append({"store": store, "error": r["msg"] or "no rows", "rows": 0})
            continue
        res = merge_store(store, r["fields"], r["items"], args.dry_run)
        res["rows"] = len(r["items"])
        results.append(res)
    KG.open("a", encoding="utf-8").write(
        "fin_report_sync period=%s stores=%s dry_run=%s %s\n" % (
            args.period, ",".join(stores), args.dry_run,
            datetime.now().astimezone().isoformat(timespec="seconds")))
    try:
        fresh = {}
        for res in results:
            if res.get("store") and res.get("rows"):
                fresh[res["store"]] = {"report_period": args.period, "rows": res.get("rows"),
                                       "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        if fresh and not args.dry_run:
            fp = DATA / "_fin_freshness.json"
            old = {}
            try:
                old = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                old = {}
            old.update(fresh)
            fp.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps({"period": args.period, "dry_run": args.dry_run, "results": results},
                     ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
