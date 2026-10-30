# -*- coding: utf-8 -*-
"""回补 hk_daily 2021-01-01 ~ 2023-09-15（tushare hk_daily 按交易日）。

背景：现有 hk_daily 从 2023-09-18 开始（727 文件）；tushare 接口实测 2021 年起有数据。
目的：扩展 H20（港股通持仓×A/H）样本至 2021-2026。
红线：只写 quant_backtest/data/tushare/hk_daily 自身目录；前后写 kg 日志。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
VAULT = REPO / "tools" / "secrets" / "vault.py"
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
START, END = "20210101", "20230915"


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def api(tok: str, name: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": name, "token": tok, "params": params, "fields": fields}).encode()
    req = urllib.request.Request("http://api.tushare.pro", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> int:
    outdir = DATA / "hk_daily"
    # A 股每日文件 → 交易日列表（A/H 都交易日）
    trade_dates = sorted(Path(f).stem[2:] for f in (DATA / "daily").glob("d_*.parquet")
                         if START <= Path(f).stem[2:] <= END)
    existing = {Path(f).stem[2:] for f in outdir.glob("d_*.parquet")}
    todo = [d for d in trade_dates if d not in existing]
    print(f"trade_dates={len(trade_dates)} existing={len(existing)} todo={len(todo)}", flush=True)
    if not todo:
        return 0
    tok = token()
    log = "hk_daily 回补 %s~%s 开始 todo=%d" % (START, END, len(todo))
    KG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with KG_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [H20-扩展] {log}\n")
    ok = fail = 0
    for i, dt in enumerate(todo, 1):
        for attempt in range(3):
            try:
                d = api(tok, "hk_daily", {"trade_date": dt}, FIELDS)
                items = ((d.get("data") or {}).get("items")) or []
                cols = ((d.get("data") or {}).get("fields")) or [c.strip() for c in FIELDS.split(",")]
                if not items:
                    print(f"{dt} empty code={d.get('code')} msg={str(d.get('msg'))[:60]}", flush=True)
                    fail += 1
                    break
                df = pd.DataFrame(items, columns=cols)
                df["trade_date"] = df["trade_date"].astype(str)
                pq.write_table(pd.DataFrame(df).pipe(lambda x: __import__("pyarrow").Table.from_pandas(x)),
                               outdir / f"d_{dt}.parquet", compression="snappy")
                ok += 1
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    fail += 1
                    print(f"{dt} ERR {type(e).__name__}: {str(e)[:80]}", flush=True)
                else:
                    time.sleep(2 * (attempt + 1))
        if i % 50 == 0:
            print(f"{i}/{len(todo)} ok={ok} fail={fail}", flush=True)
        time.sleep(0.12)
    msg = "hk_daily 回补完成: ok=%d fail=%d（%s~%s）" % (ok, fail, START, END)
    with KG_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [H20-扩展] {msg}\n")
    print(msg, flush=True)
    return 0 if fail == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
