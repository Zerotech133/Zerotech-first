# -*- coding: utf-8 -*-
"""涨跌停名单 · 空壳修复 + 增量补齐（打板策略命脉）。

## 根因（2026-10-15 07:35 实证·P0 级静默故障）

`tushare_sync.py:957-959`（`batch_bclass` 内）：
```python
f = os.path.join(tdir("limit_list_d"), "d_%s.parquet" % d)
if os.path.exists(f):        # ← 文件存在即跳过
    continue
df = pull_paged("limit_list_d", {"trade_date": d}, 1000)
df.to_parquet(f, index=False)
```
某次失败运行写下了**只有 schema 没有数据**的空壳 parquet（`num_rows=0`，size≈8023），
此后 `os.path.exists` 恒为真 ⇒ 这批日期**永久跳过、再也不重拉**，而日志照报「ok / 新拉 0 天」。

实证：`limit_list_d` 275 个文件中 **10-01 之后 12 个交易日全是 0 行空壳**，
有效数据停在 **20260930** ⇒ 涨停数据断流 11 个交易日。
而远端实测 `limit_list_d {"trade_date":"20261013"}` **正常返回 94 行** ⇒ 不是没数据，是代码 bug。

## 本脚本做什么
1. **扫描空壳**：逐文件读 `num_rows`，识别有 schema 无数据的文件；
2. **重拉空壳 + 增量缺口**：按交易日历算出「本地最新+1 .. 今天」，只拉缺失/空壳的日期；
3. **写前自证**：拉到 0 行**不写文件**（避免再造空壳），并如实计入 skip；
4. **附带**：同批修复 `top_list`（龙虎榜，日更路径同样有 0 行空壳）。

纪律：只写 `limit_list_d/d_*.parquet` 与 `top_list/d_*.parquet`；不碰其他表；不删历史文件。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import pandas as pd
import requests

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tushare")
API = "https://api.tushare.pro"
SESS = requests.Session()
SLEEP = 0.35
RETRY = 3

# 表 → (api, 单次上限)
TARGETS = {
    "limit_list_d": ("limit_list_d", 1500),
    "top_list": ("top_list", 1000),
}


def token() -> str:
    t = os.environ.get("TUSHARE_TOKEN", "")
    if t:
        return t
    for c in (r"C:\项目\老田的AI助手\config\tushare_token.txt", r"C:\Temp\tushare_token.txt"):
        if os.path.exists(c):
            return open(c, encoding="utf-8").read().strip()
    raise SystemExit("★找不到 TUSHARE_TOKEN")


TOK = ""


def ts_call(api, params):
    last = None
    for a in range(RETRY):
        try:
            r = SESS.post(API, json={"api_name": api, "token": TOK,
                                     "params": params, "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                d = j.get("data") or {}
                return d.get("fields") or [], d.get("items") or []
            last = "code=%s msg=%s" % (j.get("code"), str(j.get("msg"))[:70])
        except Exception as e:      # noqa: BLE001
            last = "%s %s" % (type(e).__name__, str(e)[:70])
        time.sleep(1.5 * (a + 1))
    raise RuntimeError("%s %s -> %s" % (api, params, last))


def pull_paged(api, params, cap):
    rows, fields, off, last_items = [], None, 0, None
    while True:
        p = dict(params)
        p["offset"] = off
        f, items = ts_call(api, p)
        if fields is None:
            fields = f
        if not items:
            break
        if items == last_items:            # 服务端 offset 无效 → 停（anns_d 死循环教训）
            break
        last_items = items
        rows.extend(items)
        off += len(items)
        if len(items) < cap or len(rows) >= 500000:
            break
        time.sleep(SLEEP)
    return pd.DataFrame(rows, columns=fields)


def trading_days(start: str, end: str) -> list[str]:
    calp = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
    if not calp:
        return []
    cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"]) for p in calp])
    days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    return [d for d in days if start <= d <= end]


def scan(tbl: str) -> tuple[dict, list]:
    """返回 (日期→行数, 空壳日期列表)。"""
    import pyarrow.parquet as pq
    d = os.path.join(OUT, tbl)
    sizes, shells = {}, []
    for f in sorted(glob.glob(os.path.join(d, "d_*.parquet"))):
        day = os.path.basename(f)[2:10]
        try:
            n = pq.ParquetFile(f).metadata.num_rows
        except Exception:
            n = -1
        sizes[day] = n
        if n <= 0:
            shells.append(day)
    return sizes, shells


def main() -> int:
    global TOK
    ap = argparse.ArgumentParser(description="涨跌停/龙虎榜 空壳修复 + 增量补齐")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--start", default="", help="起始交易日（默认本地有效最新+1）")
    ap.add_argument("--end", default="", help="结束（默认今天）")
    args = ap.parse_args()

    TOK = token()
    end = args.end or time.strftime("%Y%m%d")
    total_fixed = total_new = 0

    for tbl, (api, cap) in TARGETS.items():
        d = os.path.join(OUT, tbl)
        if not os.path.isdir(d):
            print("[%s] ★目录不存在，跳过" % tbl)
            continue
        sizes, shells = scan(tbl)
        valid = {k: v for k, v in sizes.items() if v > 0}
        lmax = max(valid) if valid else ""
        print("\n[%s] 文件=%d 有效=%d 空壳=%d 有效区间 %s..%s"
              % (tbl, len(sizes), len(valid), len(shells), lmax or "-",
                 max(sizes) if sizes else "-"))
        if shells:
            print("  空壳日期(%d): %s" % (len(shells), ", ".join(shells[:20])))

        start = args.start or (str(int(lmax) + 1) if lmax else "20250901")
        days = trading_days(start, end)
        open_set = set(days)
        # 🔴 2026-10-15 07:40 判例：休市日（如国庆 10-01~10-04，trade_cal is_open=0）
        #   **不能拉** —— tushare 对非交易日 trade_date 会回退返回"最近交易日"的数据，
        #   于是 10-01/02/03/04 四个文件内容完全相同（涨停 52 / 最高 7 板，同一份 09-30 数据）。
        #   原同步器 `batch_bclass` 也有此问题（只在 is_open=1 的 days 里拉，
        #   但空壳已存在的日期会被 `os.path.exists` 跳过 → 休市日空壳反而被修好了，本例是
        #   我的修复器把休市日也纳入了待处理）。规则：**只处理 is_open=1 的交易日**。
        shells_trade = [s for s in shells if s in open_set] if open_set else shells
        offdays = [s for s in shells if open_set and s not in open_set]
        if offdays:
            print("  ⚠ 跳过休市日空壳 %d 天（is_open=0，源端会回退返回前一交易日数据）: %s"
                  % (len(offdays), ", ".join(offdays)))
        todo = sorted(set(shells_trade) |
                      {d for d in days if d not in valid})
        print("  待处理 %d 天（%s..%s）" % (len(todo), todo[0] if todo else "-",
                                       todo[-1] if todo else "-"))
        if args.dryrun or not todo:
            continue

        fixed = new = skipped = 0
        for day in todo:
            try:
                df = pull_paged(api, {"trade_date": day}, cap)
            except Exception as e:      # noqa: BLE001
                print("  %s FAIL %s" % (day, str(e)[:70]))
                continue
            if df.empty:
                # 🔴 写前自证：0 行不落盘，避免再造空壳（原 bug 的成因）
                print("  %s 源端 0 行 → 跳过（不写空壳）" % day)
                skipped += 1
                continue
            fp = os.path.join(d, "d_%s.parquet" % day)
            was_shell = day in shells
            df.to_parquet(fp, index=False)
            if was_shell:
                fixed += 1
                print("  %s 修复空壳 rows=%d" % (day, len(df)))
            else:
                new += 1
                print("  %s 新增 rows=%d" % (day, len(df)))
            time.sleep(SLEEP)

        print("  → 修复 %d 天 / 新增 %d 天 / 跳过(源端空) %d 天" % (fixed, new, skipped))
        total_fixed += fixed
        total_new += new

    print("\n=== 合计：修复 %d 天 / 新增 %d 天 ===" % (total_fixed, total_new))
    print("完成 %s" % time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())