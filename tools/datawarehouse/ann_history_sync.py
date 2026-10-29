# -*- coding: utf-8 -*-
"""ann_history 增量回填（DATA1 治理 · L2-2A 批准范围）。

背景：
- ``zerobound-kg/data/ann_history/ann_history.db`` 的 ``ann`` 表停在 2026-10-01：
  旧生产者 ``kg_ann_history_fetch.py`` 是一次性历史工件（W3 归档标注），
  按 stock_watermark 跳过已完成股票，不会刷新新公告；且它依赖 Neo4j 取 universe。
- 本工具做**近月增量**：直接以库内 stock_watermark 的 ts_code 为 universe，
  逐股只翻最近页（notice_date >= --since），INSERT OR IGNORE 写回 ann / ann_stock，
  天然幂等、可续跑、不触碰 news.db、不依赖 Neo4j。

用法：
  python tools/datawarehouse/ann_history_sync.py --stats
  python tools/datawarehouse/ann_history_sync.py --dry-run --limit 10
  python tools/datawarehouse/ann_history_sync.py --apply --limit 20
  python tools/datawarehouse/ann_history_sync.py --apply            # 全量增量（长跑落盘）
选项：--since YYYY-MM-DD（默认 2026-09-15）、--limit N（仅前 N 只）、--sleep 秒。
退出码：0=正常；1=有单股失败；2=致命（库不可用/参数错误）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(r"C:\项目\zerobound-kg\data\ann_history\ann_history.db")
DB_PATH = Path(os.environ.get("ANN_HISTORY_DB", str(DEFAULT_DB)))
LOG_DIR = Path(os.environ.get("ANN_HISTORY_LOG_DIR", str(DB_PATH.parent)))

PAGE_SIZE = 100
PAGE_HARD_CAP = 8          # 单股最多翻 8 页（覆盖近月足够）
RETRY = 3
SLEEP = 0.2

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"}
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=CTX))


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HDRS)
    last = None
    for i in range(RETRY):
        try:
            with OPENER.open(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (2 ** i))
    raise RuntimeError("API 3 连败: %r" % (last,))


def open_db_readonly(db: Path = DB_PATH) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % str(db).replace("\\", "/")
    return sqlite3.connect(uri, uri=True)


def load_universe(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT ts_code FROM stock_watermark ORDER BY ts_code").fetchall()
    return [r[0] for r in rows if r[0]]


def parse_page(payload: dict, since: str) -> tuple[list[dict], bool]:
    """解析一页公告；返回 (符合 since 的条目, 是否已翻到 since 之前)。"""
    data = payload.get("data") or {}
    items = data.get("list") or []
    keep = []
    hit_old = False
    for x in items:
        nd = (x.get("notice_date") or "")[:10]
        if nd and nd < since:
            hit_old = True
            continue
        if x.get("art_code"):
            keep.append(x)
    return keep, hit_old or len(items) < PAGE_SIZE


def norm_rows(items: list[dict]) -> tuple[list[tuple], list[tuple]]:
    now = datetime.now().isoformat(timespec="seconds")
    rows, pairs = [], []
    for x in items:
        ac = x.get("art_code")
        if not ac:
            continue
        rows.append((
            "ann:" + ac, ac, x.get("title"),
            (x.get("notice_date") or "")[:10],
            ";".join(c.get("column_name", "") for c in (x.get("columns") or [])),
            x.get("source_type"), now,
        ))
        for c in (x.get("codes") or []):
            sc = c.get("stock_code")
            if sc:
                pairs.append((ac, sc, c.get("short_name"), c.get("ann_type")))
    return rows, pairs


def upsert(con: sqlite3.Connection, rows: list[tuple], pairs: list[tuple]) -> tuple[int, int]:
    before = con.total_changes
    with con:
        con.executemany("INSERT OR IGNORE INTO ann VALUES (?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT OR IGNORE INTO ann_stock VALUES (?,?,?,?)", pairs)
    return con.total_changes - before, len(rows)


def fetch_stock(ts_code: str, since: str, sleep: float) -> list[dict]:
    stock_code = ts_code.split(".")[0]
    out: dict[str, dict] = {}
    page = 1
    while page <= PAGE_HARD_CAP:
        url = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
               "?sr=-1&page_size=%d&page_index=%d&ann_type=A"
               "&client_source=web&stock_list=%s&f_node=0&s_node=0"
               % (PAGE_SIZE, page, stock_code))
        payload = api_get(url)
        keep, done = parse_page(payload, since)
        for x in keep:
            out.setdefault(x["art_code"], x)
        if done:
            break
        page += 1
        time.sleep(sleep)
    return list(out.values())


def stats(con: sqlite3.Connection) -> dict:
    n_ann = con.execute("SELECT count(*) FROM ann").fetchone()[0]
    n_pair = con.execute("SELECT count(*) FROM ann_stock").fetchone()[0]
    dmin, dmax = con.execute("SELECT min(notice_date), max(notice_date) FROM ann").fetchone()
    return {"ann": n_ann, "ann_stock": n_pair, "date_min": dmin, "date_max": dmax}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ann_history 近月增量回填")
    ap.add_argument("--apply", action="store_true", help="写库（默认只 dry-run 统计）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写")
    ap.add_argument("--since", default="2026-09-15", help="增量起点 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只股票")
    ap.add_argument("--sleep", type=float, default=SLEEP, help="每股间隔秒")
    ap.add_argument("--stats", action="store_true", help="只读输出当前库统计")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not DB_PATH.exists():
        print("DB not found: %s" % DB_PATH, file=sys.stderr)
        return 2
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")

    if args.stats:
        result = stats(con)
        print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else result)
        con.close()
        return 0

    mode = "apply" if args.apply and not args.dry_run else "dry-run"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / ("sync_%s.log" % datetime.now().strftime("%Y%m%d"))
    logf = open(log_path, "a", encoding="utf-8")

    def log(msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    uni = load_universe(con)
    if args.limit:
        uni = uni[: args.limit]
    log("START mode=%s since=%s universe=%d db=%s" % (mode, args.since, len(uni), DB_PATH))
    stats0 = stats(con)
    log("BEFORE %s" % stats0)

    total_new, total_rows, fails = 0, 0, []
    t0 = time.time()
    for i, ts in enumerate(uni, 1):
        try:
            items = fetch_stock(ts, args.since, args.sleep)
            rows, pairs = norm_rows(items)
            n_new, n_rows = upsert(con, rows, pairs) if mode == "apply" else (0, len(rows))
            total_new += n_new
            total_rows += n_rows
        except Exception as e:  # noqa: BLE001
            fails.append(ts)
            log("FAIL %s: %r" % (ts, e))
        if i % 200 == 0 or i == len(uni):
            rate = i / max(time.time() - t0, 1)
            eta = (len(uni) - i) / max(rate, 0.01) / 60
            log("PROGRESS %d/%d rows=%d new=%d fails=%d ETA=%.1fmin"
                % (i, len(uni), total_rows, total_new, len(fails), eta))
        if mode == "apply":
            time.sleep(args.sleep)

    stats1 = stats(con)
    log("AFTER %s" % stats1)
    log("DONE mode=%s rows=%d new=%d fails=%d elapsed=%.1fs"
        % (mode, total_rows, total_new, len(fails), time.time() - t0))
    logf.close()
    con.close()
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
