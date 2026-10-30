# -*- coding: utf-8 -*-
"""汇率数据补齐（换源）：东方财富日线 → data/tushare/fx_daily/。

原 Tushare FXCM 两条序列只有 ts_code/trade_date 两列（价格全缺，不可用）。
本工具用东财 K 线接口拉 USDCNH（离岸人民币）/ USDHKD 全历史，写入原文件
（文件名/ts_code 兼容旧代码，新增 close/source 列），供 A/H 溢价研究使用。

两种模式：
  python tools/fx_fill.py                 # 全历史（2010+，按年分片）
  python tools/fx_fill.py --recent 40     # 增量（最近 40 天，例行任务用）
  python tools/fx_fill.py --dry-run
红线：只写自身 data/tushare/fx_daily；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare" / "fx_daily"
KG = Path(r"C:\Temp\kg_sync_task.log")
PAIRS = {
    "USDCNH_FXCM.parquet": {"ts_code": "USDCNH.FXCM", "secid": "133.USDCNH", "name": "USDCNH 离岸人民币"},
    "USDHKD_FXCM.parquet": {"ts_code": "USDHKD.FXCM", "secid": "119.USDHKD", "name": "USDHKD 美元港币"},
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}


def _em_get(url: str) -> dict:
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(safe_urlopen(req, timeout=40).read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.5 + 1.5 * attempt)
    raise last


def _em_range(secid: str, beg: str, end: str) -> dict:
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3&fields2=f51,f53&klt=101&fqt=1&beg=%s&end=%s" % (secid, beg, end))
    d = _em_get(url)
    out = {}
    for k in ((d.get("data") or {}).get("klines")) or []:
        parts = k.split(",")
        if len(parts) >= 2:
            out[parts[0].replace("-", "")] = float(parts[1])
    return out


def _ecb_range(days: int) -> dict:
    end = datetime.now()
    beg = end - timedelta(days=days + 10)
    url = "https://api.frankfurter.app/%s..%s?from=USD&to=CNY,HKD" % (
        beg.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(safe_urlopen(req, timeout=30).read().decode())
    return {dt.replace("-", ""): rates for dt, rates in (d.get("rates") or {}).items()}


def _merge_write(f: Path, ts_code: str, new_rows: dict, source: str, dry: bool) -> int:
    old = pq.read_table(f).to_pylist() if f.exists() else []
    have = {(r["trade_date"], str(r.get("source") or "")) for r in old}
    added = 0
    for dt, close in sorted(new_rows.items()):
        if (dt, source) in have:
            continue
        old.append({"ts_code": ts_code, "trade_date": dt, "close": close, "source": source})
        added += 1
    if added and not dry:
        table = pa.table({"ts_code": [r["ts_code"] for r in old],
                          "trade_date": [r["trade_date"] for r in old],
                          "close": [r["close"] for r in old],
                          "source": [r["source"] for r in old]})
        tmp = f.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        tmp.replace(f)
    return added


def fetch_em_full(secid: str) -> list[dict]:
    """全历史：按年分片（避免大响应被东财断连）。"""
    rows = {}
    year, now_year = 2010, datetime.now().year
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
    ap.add_argument("--recent", type=int, default=0,
                    help="增量窗口（自然日），0=全历史；例行任务用 40")
    args = ap.parse_args(argv)
    if KG.exists():
        tail = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        if any("in_progress" in x for x in tail):
            print("kg 日志有进行中操作，停止", file=sys.stderr)
            return 3
    results = []
    if args.recent > 0:
        beg = (datetime.now() - timedelta(days=args.recent + 10)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        ecb = {}
        try:
            ecb = _ecb_range(args.recent)
        except Exception:  # noqa: BLE001
            ecb = {}
        for fname, spec in PAIRS.items():
            f = DATA / fname
            rows, source, err = {}, "eastmoney", ""
            try:
                rows = _em_range(spec["secid"], beg, end)
            except Exception as e:  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, str(e)[:80])
            if not rows and ecb:
                ccy = "CNY" if "CNH" in spec["ts_code"] else "HKD"
                rows = {dt: float(rates[ccy]) for dt, rates in ecb.items() if rates.get(ccy)}
                source = "ecb_fallback"
            added = _merge_write(f, spec["ts_code"], rows, source, args.dry_run)
            results.append({"pair": spec["name"], "file": fname, "window": [beg, end],
                            "fetched": len(rows), "added": added, "source": source,
                            "error": err, "dry": args.dry_run})
            print("%-22s fetched=%d added=%d source=%s %s" % (
                spec["name"], len(rows), added, source, err), flush=True)
            time.sleep(0.3)
    else:
        for fname, spec in PAIRS.items():
            f = DATA / fname
            rows = fetch_em_full(spec["secid"])
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
    try:
        logf = REPO / "work" / "data_sync" / "fx_daily.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        logf.open("a", encoding="utf-8").write(
            "%s fx_fill mode=%s dry=%s results=%s\n" % (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                "recent" if args.recent else "full", args.dry_run,
                json.dumps(results, ensure_ascii=False)))
    except Exception:  # noqa: BLE001
        pass
    KG.open("a", encoding="utf-8").write(
        "fx_fill mode=%s pairs=%s dry=%s %s\n" % (
            "recent" if args.recent else "full", ",".join(PAIRS), args.dry_run,
            datetime.now().astimezone().isoformat(timespec="seconds")))
    print(json.dumps(results, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
