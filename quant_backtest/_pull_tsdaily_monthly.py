# -*- coding: utf-8 -*-
"""ts_daily 月度窗口拉取器（绕开远端全表深 offset 二次退化）。

探针结论（2026-09-27）：
· 全表深 offset 分页二次退化（offset 140万 → 28s/页），tdx_sync 的
  offset 全量拉法在 ts_daily（370 万行）上不可行；
· 月度窗口（start_date=月首&end_date=月末）页内深 offset 速度正常
  （0.6~0.8s/页），单月 ~11 万行 = ~22 页；
· 单日窗口在旧月份不可靠（20230703 total=0 但整月 total=5232）；
· 远端真实起始 = 2023-07（total=5232 ≈ 单日量，数据从月中某日开始）。

用法：python _pull_tsdaily_monthly.py [--from 2023,7]
断点续传：data/tdx/_tsdaily_monthly.json 记录已完成月份。
完成后写 manifest ts_daily done=True（今晚 23:00 计划任务自动跳过）。
"""
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx", "ts_daily")
STATE = os.path.join(HERE, "data", "tdx", "_tsdaily_monthly.json")
MANIFEST = os.path.join(HERE, "data", "tdx", "_manifest.json")
PAGE = 5000
RETRY = 3
SLEEP = 0.1

SESS = requests.Session()


def _get(params, timeout=90):
    for i in range(RETRY):
        try:
            r = SESS.get(BASE + "/api/data/table/ts_daily",
                         params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            err = "HTTP %s" % r.status_code
        except Exception as e:
            err = str(e)[:80]
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET fail %s: %s" % (params, err))


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"done_months": []}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def next_part_idx():
    os.makedirs(OUT, exist_ok=True)
    return len([f for f in os.listdir(OUT) if f.endswith(".parquet")])


def pull_month(y, m):
    """月窗口分页拉取。返回 (rows, cols, total)。"""
    s = "%04d%02d01" % (y, m)
    e = "%04d%02d31" % (y, m)
    d = _get({"start_date": s, "end_date": e, "offset": 0, "limit": PAGE})
    total = d.get("total") or 0
    rows, cols = list(d.get("rows") or []), d.get("columns") or []
    off = len(rows)
    while rows and off < total:
        d2 = _get({"start_date": s, "end_date": e, "offset": off,
                   "limit": PAGE})
        chunk = d2.get("rows") or []
        if not chunk:
            break
        rows.extend(chunk)
        off = len(rows)
    return rows, cols, total


def main(from_ym=None):
    st = load_state()
    done = set(st["done_months"])
    now = datetime.now()
    months = []
    y, m = from_ym or (2023, 7)
    while (y, m) <= (now.year, now.month):
        if (y, m) not in done:
            months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    print("待拉月份 %d 个（已完成 %d）" % (len(months), len(done)), flush=True)
    t0 = time.time()
    for k, (y, m) in enumerate(months):
        tm = time.time()
        rows, cols, total = pull_month(y, m)
        if total and len(rows) < total:
            raise RuntimeError("%04d-%02d rows=%d < total=%d"
                               % (y, m, len(rows), total))
        if rows:
            idx = next_part_idx()
            df = pd.DataFrame([dict(zip(cols, r)) for r in rows])
            df.to_parquet(os.path.join(OUT, "part_%04d.parquet" % idx),
                          index=False)
        st["done_months"].append([y, m])
        save_state(st)
        el = time.time() - t0
        eta = el / (k + 1) * (len(months) - k - 1)
        print("[%02d/%02d] %04d-%02d rows=%d (%.1fs) | 已用 %.0fs ETA %.0fs"
              % (k + 1, len(months), y, m, len(rows), time.time() - tm,
                 el, eta), flush=True)
        time.sleep(SLEEP)
    # manifest 收口
    n_parts = next_part_idx()
    n_rows = 0
    for f in sorted(os.listdir(OUT)):
        if f.endswith(".parquet"):
            n_rows += pd.read_parquet(os.path.join(OUT, f),
                                      columns=["ts_code"]).shape[0]
    mm = json.load(open(MANIFEST, encoding="utf-8"))
    mm["ts_daily"] = {"offset": n_rows, "part": n_parts, "rows": n_rows,
                      "done": True, "mode": "monthly"}
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mm, fh)
    os.replace(tmp, MANIFEST)
    os.remove(STATE)
    print("TS_DAILY_PULL_DONE parts=%d rows=%d" % (n_parts, n_rows),
          flush=True)


if __name__ == "__main__":
    fy = fm = None
    if "--from" in sys.argv:
        i = sys.argv.index("--from")
        fy, fm = (int(x) for x in sys.argv[i + 1].split(","))
    main((fy, fm) if fy else None)
