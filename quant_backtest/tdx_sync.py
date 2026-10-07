# -*- coding: utf-8 -*-
"""TDX-Trading 远端数据仓全量/增量同步器（断点续传）。

数据源：http://rookie.xtcloud-tech.com:48088（TDX-Trading 数据查询 API，
30 张表 · Tushare + akshare + 通达信采集，每日 17:00 增量更新）。

落库布局（与现有数据仓对齐）：
· K线/财务/板块等结构化表 → data/tdx/<table>/part_NNNN.parquet（分片，
  读取用 pd.concat(map(pd.read_parquet, glob))）
· 公告/个股新闻/研报 → data/tdx/tdx_info.db 三张表（独立库，不进
  news.db——22 万条个股新闻会淹没 JEV 判断队列，JEV 按需选择性接入）
· 进度 → data/tdx/_manifest.json（每表 offset/分片号/done，断点续传）

增量：表 done 后重跑按本地最新日期/时间戳只拉新增（远端每日 17:00 更新，
配合每日定时任务即为增量同步）。

用法：
  python tdx_sync.py --all                # 全部结构化表（不含 ts_rt_min）
  python tdx_sync.py --tables ts_daily,stock_notice
  python tdx_sync.py --include-rtmin      # 追加 4752 万行分钟线（3-4h）
  python tdx_sync.py --status             # 查看同步进度
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import time

import pandas as pd
import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx")
INFO_DB = os.path.join(OUT, "tdx_info.db")
MANIFEST = os.path.join(OUT, "_manifest.json")

PAGE = 5000                      # API 单次上限
FLUSH_PAGES = 40                 # 每 40 页（20 万行）落一个 parquet 分片
RETRY = 3                        # 单页失败重试
SLEEP = 0.15                     # 请求间隔（保护远端）
SQLITE_TABLES = {"stock_notice", "stock_news", "stock_report"}
# ts_rt_min 4752 万行单独开关，默认跳过
SKIP_DEFAULT = {"ts_rt_min", "tdx_sector_min"}  # K53: ts_moneyflow 移出（done 表走增量续拉，不再全量翻页）

SESS = requests.Session()


def _get(path, params=None, timeout=60):
    for i in range(RETRY):
        try:
            r = SESS.get(BASE + path, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            err = r.json().get("error", r.status_code)
        except Exception as e:
            err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET %s failed: %s" % (path, err))


def _load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(m):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def _remote_tables():
    d = _get("/api/data/status")
    return {t["table"]: t for t in d.get("tables", [])}


# ── parquet 分片表 ──────────────────────────────────────────
def _part_path(tbl, idx):
    d = os.path.join(OUT, tbl)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "part_%04d.parquet" % idx)


def _parquet_latest_date(tbl):
    """本地最新日期（增量起点）：全分片取日期列 max（分片不保证按日期
    有序，如 sector_daily 窗口补齐分片存放更早数据）。"""
    parts = sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    if not parts:
        return None
    try:
        dcol = _date_col_of(tbl)
        vals = []
        for p in parts:
            vals.extend(pd.read_parquet(p, columns=[dcol])[dcol].astype(str))
        vals = [v.replace("-", "").split(" ")[0][:8] for v in vals]
        vals = [v for v in vals if len(v) == 8 and v.isdigit()]
        return max(vals) if vals else None
    except Exception:
        return None


def _date_col_of(tbl):
    return {"ts_income": "end_date", "ts_balancesheet": "end_date",
            "ts_cashflow": "end_date", "ts_fina_indicator": "end_date",
            "ts_index_weight": "trade_date"}.get(tbl, "trade_date")


def sync_parquet_table(tbl, meta, m):
    """分页拉全量 → 分片 parquet；done 后重跑按最新日期增量续拉。"""
    date_col = _date_col_of(tbl)
    total = meta.get("rows", 0)
    st = m.setdefault(tbl, {"offset": 0, "part": 0, "rows": 0, "done": False})
    buf = []
    start_time = time.time()

    def flush():
        if not buf:
            return
        df = pd.DataFrame(buf)
        df.to_parquet(_part_path(tbl, st["part"]), index=False)
        st["part"] += 1
        buf.clear()

    # 增量：已 done 的表，从本地最新日期继续
    params = {}
    latest = _parquet_latest_date(tbl) if st.get("done") else None
    if latest:
        ld = latest.replace("-", "").split(" ")[0]
        params["start_date"] = ld          # 重叠一天由去重兜底
        mode = "incremental from %s" % ld
    else:
        mode = "full"
    # 增量模式从 offset 0 翻页（远端 offset 基于 start_date 过滤后结果集，
    # 月度拉取表 manifest offset=累计行数非全表 offset，必须重置 0 否则秒过空）
    if latest:
        st["offset"] = 0
    print("[%s] %s rows=%s" % (tbl, mode, total))
    while True:
        p = dict(params)
        p["limit"] = PAGE
        p["offset"] = st["offset"]
        d = _get("/api/data/table/" + tbl, p)
        rows = d.get("rows", [])
        cols = d.get("columns", [])
        if not rows:
            break
        if latest:
            # 增量去重：丢掉本地已有的最新日（重叠窗口内）
            di = cols.index(date_col) if date_col in cols else None
            if di is not None:
                ld = latest.replace("-", "").split(" ")[0]
                rows = [r for r in rows if str(r[di]).replace("-", "")[:8] > ld]
                if not rows:
                    break
        buf.extend(dict(zip(cols, r)) for r in rows)
        st["offset"] += len(rows)
        st["rows"] += len(rows)
        if len(buf) >= FLUSH_PAGES * PAGE:
            flush()
            _save_manifest(m)
        if st["offset"] >= d.get("total", total) and d.get("total"):
            break
        time.sleep(SLEEP)
    flush()
    # 增量模式下 offset 语义还原（下次重跑仍从头翻页，靠 start_date 过滤）
    if latest:
        st["offset"] = 0
    st["done"] = True
    _save_manifest(m)
    n_parts = len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    print("[%s] DONE rows=%d parts=%d %.0fs"
          % (tbl, st["rows"], n_parts, time.time() - start_time))


# ── SQLite 信息表（公告/新闻/研报） ─────────────────────────
DATE_COL_SQLITE = {"stock_notice": "notice_date", "stock_news": "pub_date",
                   "stock_report": "pub_date"}


def _sqlite_latest_date(tbl):
    """本地 sqlite 表最新日期（增量起点）。"""
    if not os.path.exists(INFO_DB):
        return None
    dcol = DATE_COL_SQLITE[tbl]
    try:
        conn = sqlite3.connect(INFO_DB, timeout=30)
        v = conn.execute("SELECT MAX(%s) FROM %s" % (dcol, tbl)).fetchone()[0]
        conn.close()
        return str(v).replace("-", "")[:8] if v else None
    except sqlite3.OperationalError:
        return None


def sync_sqlite_table(tbl, meta, m):
    """K92 修复：远端按日期 DESC 排序且滚动窗口（rows 守恒，新数据插
    头部、旧尾部滚出）——旧 offset 续传永远拉旧尾部，INSERT OR IGNORE
    全忽略，本地停在首拉水位。改日期增量（与 parquet 同款）：done 后
    带 start_date=本地最新日重翻，重叠日由 IGNORE 幂等兜底。"""
    st = m.setdefault(tbl, {"offset": 0, "rows": 0, "done": False})
    params = {}
    if st.get("created"):
        latest = _sqlite_latest_date(tbl)
        if latest:
            params["start_date"] = latest
            st["offset"] = 0
            print("[%s] incremental from %s" % (tbl, latest))
    conn = sqlite3.connect(INFO_DB, timeout=30)
    start_time = time.time()
    while True:
        p = dict(params)
        p.update({"limit": PAGE, "offset": st["offset"]})
        d = _get("/api/data/table/" + tbl, p)
        rows = d.get("rows", [])
        cols = d.get("columns", [])
        if not rows:
            break
        ph = ",".join("?" * len(cols))
        if not st.get("created"):
            conn.execute("CREATE TABLE IF NOT EXISTS %s (%s)" % (
                tbl, ",".join("%s TEXT" % c for c in cols)))
            conn.execute("CREATE INDEX IF NOT EXISTS ix_%s ON %s(ts_code)"
                         % (tbl, tbl))
            st["created"] = True
        conn.executemany(
            "INSERT OR IGNORE INTO %s (%s) VALUES (%s)"
            % (tbl, ",".join(cols), ph),
            [tuple(str(v) for v in r) for r in rows])
        conn.commit()
        st["offset"] += len(rows)
        st["rows"] += len(rows)
        _save_manifest(m)
        if st["offset"] >= d.get("total", 0) and d.get("total"):
            break
        time.sleep(SLEEP)
    conn.close()
    st["offset"] = 0          # 日期增量模式每次重翻，offset 语义还原
    st["done"] = True
    _save_manifest(m)
    print("[%s] DONE rows=%d %.0fs"
          % (tbl, st["rows"], time.time() - start_time))


def sync_rtmin(m):
    """ts_rt_min：按日期切片（每日全市场 ~120 万行 → 240 页/日）。"""
    meta = _remote_tables().get("ts_rt_min", {})
    latest = _parquet_latest_date("ts_rt_min")
    print("[ts_rt_min] remote=%s from=%s"
          % (meta.get("rows", "?"), latest or "start"))
    st = m.setdefault("ts_rt_min", {"offset": 0, "part": 0, "rows": 0,
                                    "done": False})
    buf = []

    def flush():
        if buf:
            pd.DataFrame(buf).to_parquet(
                _part_path("ts_rt_min", st["part"]), index=False)
            st["part"] += 1
            buf.clear()

    # 以单日为切分单位（trade_date 精确过滤），已 done 从最新日期续
    cur_date = (latest or "").replace("-", "")[:8]
    dates = []
    if latest:
        dates = [cur_date]     # 增量：仅补最新日（滚动窗口）
    else:
        dates = ["FULL"]       # 全量：不带日期翻页
    for tag in dates:
        while True:
            p = {"limit": PAGE, "offset": st["offset"]}
            if tag != "FULL":
                p["start_date"] = tag
                p["end_date"] = tag
            d = _get("/api/data/table/ts_rt_min", p, timeout=120)
            rows = d.get("rows", [])
            cols = d.get("columns", [])
            if not rows:
                break
            buf.extend(dict(zip(cols, r)) for r in rows)
            st["offset"] += len(rows)
            st["rows"] += len(rows)
            if len(buf) >= FLUSH_PAGES * PAGE:
                flush()
                _save_manifest(m)
            if st["offset"] >= d.get("total", 0) and d.get("total"):
                break
            time.sleep(SLEEP)
    flush()
    st["offset"] = 0
    st["done"] = True
    _save_manifest(m)
    print("[ts_rt_min] DONE rows=%d" % st["rows"])


def status():
    m = _load_manifest()
    remote = _remote_tables()
    print("%-22s %10s %10s %8s %6s %s" % (
        "table", "remote", "local", "part", "done", "mode"))
    for tbl, meta in sorted(remote.items()):
        st = m.get(tbl, {})
        parts = len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
        if tbl in SQLITE_TABLES:
            local = st.get("rows", "-")
            parts = "-"
        else:
            local = st.get("rows", "-")
        print("%-22s %10s %10s %8s %6s %s" % (
            tbl, meta.get("rows", "?"), local, parts,
            "Y" if st.get("done") else "-",
            "skip(rtmin)" if tbl in SKIP_DEFAULT else ""))
    if os.path.exists(INFO_DB):
        conn = sqlite3.connect(INFO_DB)
        for t in SQLITE_TABLES:
            try:
                n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                print("tdx_info.db %-14s %d rows" % (t, n))
            except sqlite3.OperationalError:
                pass
        conn.close()
    print("db: %.1f MB" % (os.path.getsize(INFO_DB) / 1e6)
          if os.path.exists(INFO_DB) else "no info db yet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tables", default="")
    ap.add_argument("--include-rtmin", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.status:
        status()
        return
    remote = _remote_tables()
    if a.tables:
        tables = [t.strip() for t in a.tables.split(",") if t.strip()]
    else:
        tables = [t for t in remote if t not in SKIP_DEFAULT]
        if a.include_rtmin:
            tables.append("ts_rt_min")
    m = _load_manifest()
    # 先小表后大表（尽快见到产出；大表中断可续传）
    tables.sort(key=lambda t: remote.get(t, {}).get("rows", 0))
    for tbl in tables:
        meta = remote.get(tbl)
        if not meta:
            print("[%s] not in remote, skip" % tbl)
            continue
        try:
            if tbl == "ts_rt_min":
                sync_rtmin(m)
            elif tbl in SQLITE_TABLES:
                sync_sqlite_table(tbl, meta, m)
            else:
                sync_parquet_table(tbl, meta, m)
        except Exception as e:
            print("[%s] ERROR %s (进度已存，重跑续传)" % (tbl, e))
    print("ALL DONE. manifest=%s" % MANIFEST)


if __name__ == "__main__":
    main()
