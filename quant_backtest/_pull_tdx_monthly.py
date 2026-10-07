# -*- coding: utf-8 -*-
"""TDX 月度窗口泛化拉取器（--table 参数化，绕开远端深 offset 退化）。

背景（ts_daily 落盘实证 2026-09-27）：
· 远端全表深 offset 分页二次退化（offset 140万 → 28s/页），全量拉法
  在深表上不可行；
· 月度窗口内 offset 速度正常（0.6~0.8s/页）；
· 单日窗口在旧月份不可靠，月度窗口与全表 total 精确吻合；
· tdx_sector_daily 教训：部分表日期过滤只对近段行生效——拉取前先跑
  探针对比 Σ月度 total vs 全表 total，GAP>0 则放弃或只拉可达段。

用法：
  python _pull_tdx_monthly.py --table ts_daily_basic
  python _pull_tdx_monthly.py --table ts_index_daily --from 2020,1
断点：data/tdx/_state_<table>_monthly.json（done_months，中断重跑续传）。
落盘：data/tdx/<table>/part_NNNN.parquet（每月一片，按月序，索引与
done_months 对齐——写片前先清 idx>=len(done) 的残片，防中断残留重复）。
收口：manifest <table> = {rows, part, done: true, mode: "monthly"}
（行数按 parquet metadata 重算，不信任累计值）。
"""
import argparse
import glob
import json
import os
import re
import time

import pandas as pd
import pyarrow.parquet as pq
import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = os.path.dirname(os.path.abspath(__file__))
TDX = os.path.join(HERE, "data", "tdx")
MANIFEST = os.path.join(TDX, "_manifest.json")
PAGE = 5000
RETRY = 3
SLEEP = 0.1
SESS = requests.Session()


def get(tbl, params):
    err = None
    for i in range(RETRY):
        try:
            r = SESS.get(BASE + "/api/data/table/" + tbl,
                         params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            err = r.status_code
        except Exception as e:
            err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET %s %s failed: %s" % (tbl, params, err))


def state_path(tbl):
    return os.path.join(TDX, "_state_%s_monthly.json" % tbl)


def load_state(tbl):
    p = state_path(tbl)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"done_months": []}


def save_state(tbl, st):
    p = state_path(tbl)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, p)


def _part_file(out_dir, idx):
    return os.path.join(out_dir, "part_%04d.parquet" % idx)


def _clear_stale_parts(out_dir, keep_below):
    """删 idx >= keep_below 的残片（写片后记状态前中断的遗留）。"""
    for p in glob.glob(os.path.join(out_dir, "part_*.parquet")):
        mo = re.search(r"part_(\d+)\.parquet$", p)
        if mo and int(mo.group(1)) >= keep_below:
            os.remove(p)
            print("  清理残片 %s" % os.path.basename(p), flush=True)


def pull_month(tbl, y, m):
    """单月窗口分页。total=0 返回 (None, 0)；行数不符抛错。"""
    s = "%04d%02d01" % (y, m)
    e = "%04d%02d31" % (y, m)
    d = get(tbl, {"start_date": s, "end_date": e, "limit": 1})
    total = d.get("total", 0)
    if not total:
        return None, 0
    rows, cols, off = [], None, 0
    while off < total:
        d = get(tbl, {"start_date": s, "end_date": e,
                      "limit": PAGE, "offset": off})
        rk, ck = d.get("rows", []), d.get("columns", [])
        if not rk:
            break
        cols = ck
        rows.extend(rk)
        off += len(rk)
        time.sleep(SLEEP)
    assert rows and len(rows) == total, \
        "%s %04d-%02d 行数不符 %d != %d" % (tbl, y, m, len(rows), total)
    return pd.DataFrame(rows, columns=cols), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--from", dest="frm", default="2020,1",
                    help="起始 年,月（total=0 的月自动跳过）")
    args = ap.parse_args()
    tbl = args.table
    y, m = (int(x) for x in args.frm.split(","))
    lt = time.localtime()
    ny, nm = lt.tm_year, lt.tm_mon

    st = load_state(tbl)
    done = {tuple(x) for x in st["done_months"]}
    out_dir = os.path.join(TDX, tbl)
    os.makedirs(out_dir, exist_ok=True)

    months = []
    cy, cm = y, m
    while (cy, cm) <= (ny, nm):
        if (cy, cm) not in done:
            months.append((cy, cm))
        cm += 1
        if cm > 12:
            cy, cm = cy + 1, 1
    print("[%s] 待拉 %d 月（已完成 %d）" % (tbl, len(months), len(done)),
          flush=True)

    t0 = time.time()
    for k, (yy, mm) in enumerate(months):
        ts = time.time()
        df, total = pull_month(tbl, yy, mm)
        if df is None:
            st["done_months"].append([yy, mm])
            save_state(tbl, st)
            print("[%d/%d] %04d-%02d total=0 跳过"
                  % (k + 1, len(months), yy, mm), flush=True)
            continue
        _clear_stale_parts(out_dir, len(st["done_months"]))
        df.to_parquet(_part_file(out_dir, len(st["done_months"])),
                      index=False)
        st["done_months"].append([yy, mm])
        save_state(tbl, st)
        el = time.time() - t0
        eta = el / (k + 1) * (len(months) - k - 1)
        print("[%d/%d] %04d-%02d rows=%d (%.1fs) | 已用 %.0fs ETA %.0fs"
              % (k + 1, len(months), yy, mm, len(df), time.time() - ts,
                 el, eta), flush=True)

    # manifest 收口：行数按 parquet metadata 重算
    parts = sorted(glob.glob(os.path.join(out_dir, "part_*.parquet")))
    n_total = sum(pq.ParquetFile(p).metadata.num_rows for p in parts)
    with open(MANIFEST, encoding="utf-8") as f:
        man = json.load(f)
    man[tbl] = {"offset": n_total, "part": len(parts), "rows": n_total,
                "done": True, "mode": "monthly"}
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)
    print("[%s] DONE rows=%d parts=%d %.0fs"
          % (tbl, n_total, len(parts), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
