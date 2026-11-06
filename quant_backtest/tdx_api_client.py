# -*- coding: utf-8 -*-
"""TDX-Trading API thin client：ts_stk_limit（每日涨跌停价）拉取与本地缓存。

设计（p0_limit 研发，20261106）：
- 只读 GET /api/data/table/ts_stk_limit，无鉴权；limit 上限 5000，单日全市场
  ~5500 行，按 offset 分页拉齐。
- 缓存 data/tdx_api/ts_stk_limit/{YYYYMM}.parquet 按月分片，列
  (trade_date int64, ts_code str, up_limit float, down_limit float)。
- 本地缓存命中（月分片含该日）不重复拉取；网络重试 3 次指数退避。
- 公网优先，失败回退内网。
"""

import os
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "data", "tdx_api", "ts_stk_limit")

BASES = (
    "http://rookie.xtcloud-tech.com:48088",
    "http://192.168.203.48:8088",
)
PAGE_LIMIT = 5000
RETRIES = 3
TIMEOUT = 90

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "qpzm-p0/1.0 (tdx_api_client)"


def _get(path, params):
    """带重试 + 双 base 回退的 GET，返回 dict。"""
    last = None
    for base in BASES:
        for i in range(RETRIES):
            try:
                r = SESSION.get(
                    base + path, params=params, timeout=TIMEOUT
                )
                r.raise_for_status()
                return r.json()
            except Exception as ex:  # noqa: BLE001 网络类异常统一退避重试
                last = ex
                time.sleep(2 ** i)
        break  # 公网连续 3 次失败再试内网
    raise RuntimeError("ts_stk_limit GET failed after retries: %s" % last)


def _month_path(date_int):
    return os.path.join(CACHE_DIR, "%d.parquet" % (date_int // 100))


def _load_month(date_int):
    p = _month_path(date_int)
    if os.path.exists(p):
        return pd.read_parquet(p)
    return None


def fetch_day(date_int, log=None):
    """拉单日全市场涨跌停价，返回 DataFrame(trade_date, ts_code, up_limit, down_limit)。

    优先本地缓存命中；未命中才走 API 并回写月分片（原子替换）。
    返回 None 表示当日库内无数据（非交易日或未采集）。
    """
    hit = _load_month(date_int)
    if hit is not None and (hit["trade_date"] == date_int).any():
        return hit[hit["trade_date"] == date_int].reset_index(drop=True)

    rows, offset = [], 0
    while True:
        d = _get(
            "/api/data/table/ts_stk_limit",
            {
                "start_date": str(date_int),
                "end_date": str(date_int),
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
        )
        got = d.get("rows", [])
        rows.extend(got)
        if len(got) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["trade_date", "ts_code", "up_limit", "down_limit"])
    df["trade_date"] = df["trade_date"].astype("int64")
    # 回写月分片（与既有日合并，原子替换）
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _month_path(date_int)
    if hit is not None:
        df = (
            pd.concat([hit[hit["trade_date"] != date_int], df], ignore_index=True)
            .sort_values(["trade_date", "ts_code"])
            .reset_index(drop=True)
        )
    tmp = p + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    if log:
        log("  [tdx_api] %s fetched %d rows -> %s" % (date_int, len(rows), os.path.basename(p)))
    return df


def trade_dates(start, end):
    """交易日历（SZSE，is_open=1），本地过滤 [start, end]，返回升序 int64 列表。

    注意：ts_trade_cal 接口忽略 start_date/end_date 过滤参数，必须
    全量翻页后本地过滤（全表 ~22k 行，5 页）。
    """
    rows, offset = [], 0
    while True:
        d = _get(
            "/api/data/table/ts_trade_cal",
            {"limit": PAGE_LIMIT, "offset": offset},
        )
        got = d.get("rows", [])
        rows.extend(got)
        if len(got) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    cols = d["columns"]
    di, oi = cols.index("cal_date"), cols.index("is_open")
    dates = sorted(
        {
            int(r[di])
            for r in rows
            if int(float(r[oi])) == 1 and int(start) <= int(r[di]) <= int(end)
        }
    )  # 多交易所日历行去重
    return dates


def ensure_cache(start, end, log=None):
    """确保 [start, end] 全部交易日涨跌停价已缓存，返回 (ok_dates, missing_dates)。"""
    dates = trade_dates(start, end)
    ok, missing = [], []
    for d in dates:
        try:
            df = fetch_day(d, log=log)
            (ok if df is not None else missing).append(d)
        except Exception as ex:  # noqa: BLE001 单日失败不阻断整体
            missing.append(d)
            if log:
                log("  [tdx_api] %s FAILED: %s" % (d, str(ex)[:80]))
    if log:
        log("[tdx_api] cache ok %d days, missing %d" % (len(ok), len(missing)))
    return ok, missing


def load_cache(start, end):
    """读 [start, end] 缓存并集，返回 DataFrame 或 None。"""
    frames = []
    months = [m for m in range(int(start) // 100, int(end) // 100 + 1)]
    for m in months:
        p = os.path.join(CACHE_DIR, "%d.parquet" % m)
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df = df[(df["trade_date"] >= int(start)) & (df["trade_date"] <= int(end))]
            if len(df):
                frames.append(df)
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(["trade_date", "ts_code"]).reset_index(drop=True)


if __name__ == "__main__":
    import sys

    s, e = sys.argv[1], sys.argv[2]
    ok, miss = ensure_cache(s, e, log=print)
    print("ok=%d missing=%s" % (len(ok), miss[:10]))
