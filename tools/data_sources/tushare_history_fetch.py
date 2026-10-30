# -*- coding: utf-8 -*-
"""Tushare 历史数据抓取：top_list / top_inst（按交易日，分批/断点续传）。

- token 从 SOPS/age 保险库取（不落盘、不打印）；
- 逐交易日调用 API → 写 `quant_backtest/data/tushare/<store>/d_<date>.parquet`；
- 已存在文件默认跳过（断点续传）；配额不足时明确报错退出（可次日续跑）；
- 数据根结构操作：运行前读、运行后补记 `C:\\Temp\\kg_sync_task.log`。

用法：
  python tools/data_sources/tushare_history_fetch.py --api top_list --days 365 --dry-run
  python tools/data_sources/tushare_history_fetch.py --api top_list --days 365
  python tools/data_sources/tushare_history_fetch.py --api top_inst --days 365
退出码：0=成功 / 2=配额或网络失败（可续跑） / 3=前置失败。
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

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ROOT = Path(r"C:\项目\老田的AI助手")
DATA = ROOT / "quant_backtest" / "data"
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
PY = sys.executable
VAULT = ROOT / "tools" / "secrets" / "vault.py"
URL = "http://api.tushare.pro"

API_SPEC = {
    "top_list": {
        "fields": "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason",
        "params": lambda d: {"trade_date": d},
    },
    "top_inst": {
        "fields": "trade_date,ts_code,exalter,side,buy,sell,net_buy,reason",
        "params": lambda d: {"trade_date": d},
    },
}


def get_token() -> str:
    p = subprocess.run([PY, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    tok = (p.stdout or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0 or not tok:
        raise RuntimeError("vault decrypt failed")
    return tok


def trading_days_last(days: int) -> list[str]:
    files = sorted((DATA / "tushare" / "daily").glob("d_*.parquet"))
    ds = [f.stem[2:] for f in files if f.stem[2:].isdigit()]
    return ds[-days:]


def call(token: str, api: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": api, "token": token, "params": params, "fields": fields}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with safe_urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {"code": d.get("code"), "msg": (d.get("msg") or "")[:200],
                "fields": (d.get("data") or {}).get("fields") or [],
                "items": (d.get("data") or {}).get("items") or []}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "msg": type(e).__name__ + ": " + str(e)[:200], "fields": [], "items": []}


def kg_append(line: str) -> None:
    try:
        with KG_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tushare 历史抓取（top_list/top_inst）")
    ap.add_argument("--api", choices=sorted(API_SPEC), required=True)
    ap.add_argument("--days", type=int, default=250, help="最近 N 个交易日（250≈1 年）")
    ap.add_argument("--sleep", type=float, default=0.25, help="每次调用间隔秒")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    spec = API_SPEC[args.api]
    store = DATA / "tushare" / args.api
    dates = trading_days_last(args.days)
    todo = [d for d in dates if not (store / ("d_%s.parquet" % d)).exists()]
    print("%s: 目标 %d 交易日 / 已有 %d / 待拉 %d" % (
        args.api, len(dates), len(dates) - len(todo), len(todo)))
    if args.dry_run:
        print("dry-run: 首/末 =", todo[:1], todo[-1:] if todo else [])
        return 0
    if not todo:
        print("无需拉取")
        return 0
    try:
        token = get_token()
    except Exception as e:  # noqa: BLE001
        print("token 获取失败:", e, file=sys.stderr)
        return 3
    # kg 日志前置
    tail = [ln.strip() for ln in KG_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()][-3:]
    if any(("in_progress" in ln) or ("running" in ln) for ln in tail):
        print("kg 日志显示有进行中操作，停止", file=sys.stderr)
        return 3
    store.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    consecutive_fail = 0
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        r = call(token, args.api, spec["params"](d), spec["fields"])
        if r["code"] != 0:
            print("FAIL %s: code=%s %s" % (d, r["code"], r["msg"]))
            fail += 1
            if "每分钟" in r["msg"] or "最多" in r["msg"] or "权限" in r["msg"] or "积分" in r["msg"]:
                print("疑似配额/权限限制，停止（可明日续跑）")
                break
            consecutive_fail += 1
            if consecutive_fail >= 5:
                print("连续失败 %d 次，停止（检查 token/网络）" % consecutive_fail)
                break
            time.sleep(1.0)
            continue
        consecutive_fail = 0
        if not r["items"]:
            skip += 1
        else:
            table = pa.table({f: [row[j] for row in r["items"]] for j, f in enumerate(r["fields"])})
            tmp = store / ("d_%s.parquet.tmp" % d)
            pq.write_table(table, tmp)
            tmp.replace(store / ("d_%s.parquet" % d))
            ok += 1
        if i % 25 == 0:
            print("进度 %d/%d ok=%d fail=%d 空=%d 用时 %.0fs" % (i, len(todo), ok, fail, skip, time.time() - t0))
        time.sleep(args.sleep)
    kg_append("%s date=%s api=%s days=%d ok=%d fail=%d empty=%d exit=0" % (
        "tushare_fetch", datetime.now().astimezone().isoformat(timespec="seconds"),
        args.api, args.days, ok, fail, skip))
    print("完成：ok=%d fail=%d empty=%d 用时 %.0fs" % (ok, fail, skip, time.time() - t0))
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
