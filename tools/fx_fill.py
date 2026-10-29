# -*- coding: utf-8 -*-
"""汇率数据补齐（换源）：东方财富日线 → data/tushare/fx_daily/。

原 Tushare FXCM 两条序列只有 ts_code/trade_date 两列（价格字段全缺，不可用）。
本工具用东财 K 线接口拉 USDCNH（离岸人民币）/ USDHKD 全历史，写入原文件
（文件名不变，ts_code 不变，新增 close/source 列），供 A/H 溢价研究使用。

用法：
  python tools/fx_fill.py --dry-run
  python tools/fx_fill.py
红线：只写自身 data/tushare/fx_daily；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare" / "fx_daily"
KG = Path(r"C:\Temp\kg_sync_task.log")
# 东财 secid：133=外汇（USDCNH 离岸），119=外汇（USDHKD）
PAIRS = {
    "USDCNH_FXCM.parquet": {"ts_code": "USDCNH.FXCM", "secid": "133.USDCNH", "name": "USDCNH 离岸人民币"},
    "USDHKD_FXCM.parquet": {"ts_code": "USDHKD.FXCM", "secid": "119.USDHKD", "name": "USDHKD 美元港币"},
}


def _em_get(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers=headers)
            return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.5 + 1.5 * attempt)
    raise last


def fetch_em(secid: str) -> list[dict]:
    """按年分片拉取，避免大响应被东财断连。"""
    rows = {}
    year = 2010
    now_year = datetime.now().year
    while year <= now_year:
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
               "&fields1=f1,f2,f3&fields2=f51,f53&klt=101&fqt=1&beg=%d0101&end=%d1231"
               % (secid, year, year))
        try:
            d = _em_get(url)
            for k in ((d.get("data") or {}).get("klines")) or []:
                parts = k.split(",")
                if len(parts) >= 2:
                    rows[parts[0].replace("-", "")] = float(parts[1])
        except Exception as e:  # noqa: BLE001
            print("  chunk", year, "ERR", type(e).__name__, str(e)[:60], flush=True)
        year += 1
        time.sleep(0.8)
    return [{"trade_date": dt, "close": rows[dt]} for dt in sorted(rows)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if KG.exists():
        tail = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        if any("in_progress" in x for x in tail):
            print("kg 日志有进行中操作，停止", file=sys.stderr)
            return 3
    results = []
    for fname, spec in PAIRS.items():
        f = DATA / fname
        rows = fetch_em(spec["secid"])
        if not rows:
            results.append({"pair": spec["name"], "error": "no klines"})
            print(spec["name"], "ERR no klines", flush=True)
            continue
        dates = sorted({r["trade_date"] for r in rows})
        closes = {r["trade_date"]: r["close"] for r in rows}
        records = [{"ts_code": spec["ts_code"], "trade_date": dt, "close": closes[dt],
                    "source": "eastmoney"} for dt in dates]
        if not args.dry_run:
            table = pa.table({"ts_code": [r["ts_code"] for r in records],
                              "trade_date": [r["trade_date"] for r in records],
                              "close": [r["close"] for r in records],
                              "source": [r["source"] for r in records]})
            tmp = f.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp)
            tmp.replace(f)
        results.append({"pair": spec["name"], "file": fname, "rows": len(records),
                        "start": dates[0], "end": dates[-1], "dry": args.dry_run})
        print("%-22s rows=%d %s..%s" % (spec["name"], len(records), dates[0], dates[-1]), flush=True)
        time.sleep(0.3)
    KG.open("a", encoding="utf-8").write(
        "fx_fill source=eastmoney pairs=%s dry=%s %s\n" % (
            ",".join(PAIRS), args.dry_run, datetime.now().astimezone().isoformat(timespec="seconds")))
    try:
        logf = REPO / "work" / "data_sync" / "fx_daily.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        logf.open("a", encoding="utf-8").write(
            "%s fx_fill dry=%s results=%s\n" % (
                datetime.now().astimezone().isoformat(timespec="seconds"), args.dry_run,
                json.dumps(results, ensure_ascii=False)))
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps(results, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
