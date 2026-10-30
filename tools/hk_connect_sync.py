# -*- coding: utf-8 -*-
"""H17 数据补齐：港股通（南向）资金流 + 港股通持股明细。

数据源（Tushare）：
- moneyflow_hsgt：沪深港通资金流向（北向/南向净额），按日期区间拉取 → 单文件
- hk_hold：港股通持股明细（逐日，约 1,000 只 HK 股）→ 按日 d_*.parquet
用法：
  python tools/hk_connect_sync.py --since 20230101            # 全量（缺省）
  python tools/hk_connect_sync.py --since 20230101 --hold-days 250   # 只补最近 N 个交易日持股
  python tools/hk_connect_sync.py --dry-run
红线：只写自身 data/tushare；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
VAULT = REPO / "tools" / "secrets" / "vault.py"
KG = Path(r"C:\Temp\kg_sync_task.log")


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def call(tok: str, api: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": api, "token": tok, "params": params, "fields": fields}).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request("http://api.tushare.pro", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            d = json.loads(safe_urlopen(req, timeout=40).read().decode())
            data = d.get("data") or {}
            return {"code": d.get("code"), "msg": (d.get("msg") or "")[:120],
                    "fields": data.get("fields") or [], "items": data.get("items") or []}
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + attempt * 2)
    raise last


def trade_days(since: str) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs if Path(f).stem[2:] >= since]
    return sorted(ds)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20230101")
    ap.add_argument("--hold-days", type=int, default=0, help="只补最近 N 个交易日持股（0=全部）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tok = token()
    if KG.exists():
        tail = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        if any("in_progress" in x for x in tail):
            print("kg 日志有进行中操作，停止", file=sys.stderr)
            return 3
    results = {}

    # 1) moneyflow_hsgt（单文件）
    d1 = DATA / "moneyflow_hsgt"
    d1.mkdir(parents=True, exist_ok=True)
    # moneyflow_hsgt：API 单次上限约 300 行 → 按 280 天窗口分片
    from datetime import timedelta
    start = datetime.strptime(args.since, "%Y%m%d").date()
    end = datetime.now().date()
    rows = {}
    cur = start
    while cur <= end:
        seg_end = min(end, cur + timedelta(days=280))
        r = call(tok, "moneyflow_hsgt", {"start_date": cur.strftime("%Y%m%d"),
                                         "end_date": seg_end.strftime("%Y%m%d")},
                 "trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money")
        if r["code"] == 0 and r["items"]:
            for row in r["items"]:
                rec = dict(zip(r["fields"], row))
                rows[str(rec["trade_date"])] = rec
        cur = seg_end + timedelta(days=1)
        time.sleep(0.3)
    if rows:
        if not args.dry_run:
            ds = sorted(rows)
            table = pa.table({f: [rows[d].get(f) for d in ds] for f in
                              ("trade_date", "ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money")})
            f = d1 / "moneyflow_hsgt.parquet"
            tmp = f.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp)
            tmp.replace(f)
        results["moneyflow_hsgt"] = {"rows": len(rows), "range": [sorted(rows)[0], sorted(rows)[-1]],
                                     "file": str(d1 / "moneyflow_hsgt.parquet")}
        print("moneyflow_hsgt rows:", len(rows), flush=True)
    else:
        results["moneyflow_hsgt"] = {"error": "no rows"}

    # 2) hk_hold（按日）
    d2 = DATA / "hk_hold"
    d2.mkdir(parents=True, exist_ok=True)
    days = trade_days(args.since)
    if args.hold_days > 0:
        days = days[-args.hold_days:]
    filled = exist = fail = 0
    t0 = time.time()
    for i, dt in enumerate(days, 1):
        f = d2 / ("d_%s.parquet" % dt)
        if f.exists():
            exist += 1
            continue
        if args.dry_run:
            filled += 1
            continue
        r = call(tok, "hk_hold", {"trade_date": dt}, "code,trade_date,ts_code,name,vol,ratio,exchange")
        if r["code"] != 0 or not r["items"]:
            fail += 1
            continue
        table = pa.table({fl: [row[j] for row in r["items"]] for j, fl in enumerate(r["fields"])})
        tmp = f.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        tmp.replace(f)
        filled += 1
        if i % 100 == 0:
            print(f"hk_hold {i}/{len(days)} filled={filled} exist={exist} fail={fail} {time.time()-t0:.0f}s", flush=True)
        time.sleep(0.25)
    results["hk_hold"] = {"days": len(days), "filled": filled, "exist": exist, "fail": fail,
                          "seconds": round(time.time() - t0, 1)}
    print("hk_hold:", results["hk_hold"], flush=True)
    KG.open("a", encoding="utf-8").write(
        "hk_connect_sync since=%s dry=%s results=%s %s\n" % (
            args.since, args.dry_run, json.dumps(results, ensure_ascii=False),
            datetime.now().astimezone().isoformat(timespec="seconds")))
    print(json.dumps(results, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
