# -*- coding: utf-8 -*-
"""K-STOCK-311：拉取 ts_namechange（股票曾用名）全表落本地，用于历史 ST 回溯。

背景（老田 10-13）：market_scan 现名剔 ST 有幸存者偏差——历史被 ST、后来摘帽的
股票（现名不带 ST）一直留在回测宇宙里。ts_namechange 含每只股票每个曾用名的
[start_date, end_date] 区间，可精确判定「回测窗口内任一时点是 ST」。

产物：data/tdx/ts_namechange.parquet（幂等：重跑覆盖）。
"""
import os
import time

import pandas as pd
import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx", "ts_namechange.parquet")
PAGE = 5000
RETRY = 3

SESS = requests.Session()


def fetch_table(table, out_extra=None):
    """offset/limit 分页（服务端忽略 page/page_size，2026-10-13 实测 4000 行假象教训）。"""
    rows, off = [], 0
    total = None
    while True:
        for i in range(RETRY):
            try:
                r = SESS.get(BASE + "/api/data/table/" + table,
                             params={"offset": off, "limit": PAGE}, timeout=60)
                if r.status_code == 200:
                    d = r.json()
                    break
                err = "HTTP %s" % r.status_code
            except Exception as e:
                err = str(e)[:80]
            time.sleep(1.5 * (i + 1))
        else:
            raise RuntimeError("offset %s fail: %s" % (off, err))
        got = d.get("rows") or []
        rows.extend(got)
        total = int(d.get("total") or 0)
        print("[%s] offset %s: +%d (cum %d / total %d)" % (table, off, len(got), len(rows), total), flush=True)
        if not got or len(rows) >= total:
            break
        off += len(got)
    df = pd.DataFrame(rows, columns=d.get("columns")).drop_duplicates()
    print("[%s] 去重后 %d 行" % (table, len(df)), flush=True)
    return df


def fetch_all():
    return fetch_table("ts_namechange")


def audit(df):
    print("\n=== 体检 ===")
    print("总行数:", len(df), "| 涉及股票:", df["ts_code"].nunique())
    sd = pd.to_datetime(df["start_date"], format="%Y%m%d", errors="coerce")
    print("start_date 范围:", sd.min().date(), "→", sd.max().date())
    print("start_date 按年分布:")
    print(sd.dt.year.value_counts().sort_index().to_string())
    is_st = df["name"].astype(str).str.upper().str.contains("ST")
    print("\nname 含 ST 行数:", int(is_st.sum()), "| 涉及股票:", df.loc[is_st, "ts_code"].nunique())
    ed = pd.to_datetime(df.loc[is_st, "end_date"], format="%Y%m%d", errors="coerce")
    print("ST 行 end_date 为空(=现行名):", int(ed.isna().sum()))
    # 回测窗口相关的：窗口 20231010~20261012 内处于 ST 区间的
    d0, d1 = "20231010", "20261012"
    st_in_win = df[is_st & (df["start_date"].astype(str) <= d1)
                    & (df["end_date"].fillna("99999999").astype(str) >= d0)]
    print("三年窗(20231010~20261012)内处于 ST 区间的行:", len(st_in_win),
          "| 股票:", st_in_win["ts_code"].nunique())
    d0y, d1y = "20251013", "20261012"
    st_1y = df[is_st & (df["start_date"].astype(str) <= d1y)
               & (df["end_date"].fillna("99999999").astype(str) >= d0y)]
    print("一年窗(20251013~20261012)内处于 ST 区间的行:", len(st_1y),
          "| 股票:", st_1y["ts_code"].nunique())
    # 现名过滤已覆盖的（end_date 空 = 现行 ST）在窗口内的增量
    cur = st_in_win[st_in_win["end_date"].isna()]
    print("其中现行 ST(现名过滤已覆盖):", cur["ts_code"].nunique(),
          "| 历史增量(幸存者偏差量):", st_in_win["ts_code"].nunique() - cur["ts_code"].nunique())


if __name__ == "__main__":
    df = fetch_all()
    df.to_parquet(OUT, index=False)
    print("\nsaved:", OUT, os.path.getsize(OUT), "bytes")
    audit(df)
    # 现名对照（stock_basic）：区分「现行 ST（现名过滤已覆盖）」与「历史 ST（幸存者偏差增量）」
    sb = fetch_table("ts_stock_basic")
    sb.to_parquet(os.path.join(HERE, "data", "tdx", "ts_stock_basic.parquet"), index=False)
    cur_st = set(sb.loc[sb["name"].astype(str).str.upper().str.contains("ST"), "ts_code"])
    nc_st = set(df.loc[df["name"].astype(str).str.upper().str.contains("ST"), "ts_code"])
    print("\n现名 ST（stock_basic）:", len(cur_st),
          "| namechange 含 ST:", len(nc_st),
          "| 现行∩记录:", len(cur_st & nc_st),
          "| 历史 ST 增量（现名非 ST 但曾 ST）:", len(nc_st - cur_st))
    print("增量样本:", sorted(nc_st - cur_st)[:12])
