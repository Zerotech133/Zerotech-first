# -*- coding: utf-8 -*-
"""同步 tushare report_rc（卖方评级/盈利预测/目标价）历史 → data/tushare/report_rc/rc_YYYYMM.parquet。

背景：research_report 仅覆盖 2026-07 之后（H34-H36 因覆盖不足无法检验）；
report_rc 字段：ts_code/name/report_date/report_title/report_type/classify/org_name/author_name/
              quarter/op_rt/op_pr/tp/np/eps/pe/rd/roe/ev_ebitda/rating/max_price/min_price
分页：单次上限 5000 行 → 按周窗口拉，行数打满则二分拆到日；限频退避 61s。
用法：python tools/data_sources/sync_report_rc.py [--start 20190101] [--end 20261031]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
OUT = REPO / "quant_backtest" / "data" / "tushare" / "report_rc"
API = "https://api.tushare.pro"
MAXROWS = 5000
SLEEP = 0.4


def load_token() -> str:
    p = REPO / "quant_backtest" / ".env.local"
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ln.strip().startswith("XT_DATA_KEY="):
            return ln.split("=", 1)[1].strip()
    raise SystemExit("TOKEN 缺失（quant_backtest/.env.local XT_DATA_KEY）")


TOKEN = load_token()


def call(params: dict) -> pd.DataFrame:
    for attempt in range(6):
        try:
            r = requests.post(API, json={"api_name": "report_rc", "token": TOKEN, "params": params, "fields": ""}, timeout=90)
            j = r.json()
            code = j.get("code")
            if code == 0:
                d = j.get("data") or {}
                items = d.get("items") or []
                return pd.DataFrame(items, columns=d.get("fields") or [])
            msg = str(j.get("msg"))
            if "每分钟" in msg or "频率" in msg or "抱歉" in msg:
                time.sleep(61)
                continue
            raise RuntimeError(f"code={code} msg={msg[:120]}")
        except Exception:  # noqa: BLE001
            if attempt == 5:
                raise
            time.sleep(5)
    return pd.DataFrame()


def fetch_range(start: str, end: str) -> pd.DataFrame:
    df = call({"start_date": start, "end_date": end})
    time.sleep(SLEEP)
    if len(df) >= MAXROWS:
        # 二分拆窗
        s = datetime.strptime(start, "%Y%m%d")
        e = datetime.strptime(end, "%Y%m%d")
        if s == e:
            return df  # 单日仍满 5000：接受截断并记录
        mid = s + (e - s) / 2
        left = fetch_range(start, mid.strftime("%Y%m%d"))
        right = fetch_range((mid + timedelta(days=1)).strftime("%Y%m%d"), end)
        return pd.concat([left, right], ignore_index=True)
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--end", default="20261031")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    log = REPO / "work" / "research2" / "sync_report_rc.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def w(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    months: dict[str, list[pd.DataFrame]] = {}
    d = datetime.strptime(args.start, "%Y%m%d")
    end = datetime.strptime(args.end, "%Y%m%d")
    total = 0
    while d <= end:
        # 每 7 天一个窗口；按自然月归集
        w_end = min(d + timedelta(days=6), end)
        try:
            df = fetch_range(d.strftime("%Y%m%d"), w_end.strftime("%Y%m%d"))
        except Exception as e:  # noqa: BLE001
            w(f"FAIL {d:%Y%m%d}-{w_end:%Y%m%d}: {type(e).__name__} {str(e)[:100]}")
            d = w_end + timedelta(days=1)
            continue
        if len(df):
            df["_fetch_window"] = f"{d:%Y%m%d}-{w_end:%Y%m%d}"
            months.setdefault(f"{d:%Y%m}", []).append(df)
            total += len(df)
            w(f"{d:%Y%m%d}-{w_end:%Y%m%d} rows={len(df)} total={total}")
        d = w_end + timedelta(days=1)
    # 落盘：按月合并去重
    n_files = 0
    for ym, frames in months.items():
        df = pd.concat(frames, ignore_index=True)
        keys = [k for k in ("ts_code", "report_date", "org_name", "report_title", "quarter") if k in df.columns]
        if keys:
            df = df.drop_duplicates(subset=keys)
        p = OUT / f"rc_{ym}.parquet"
        df.to_parquet(p, index=False)
        n_files += 1
        w(f"SAVE {p.name} rows={len(df)}")
    meta = {"synced_at": datetime.now().astimezone().isoformat(timespec="seconds"), "start": args.start,
            "end": args.end, "rows": total, "files": n_files}
    (OUT / "_sync_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    w(f"DONE rows={total} files={n_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
