# -*- coding: utf-8 -*-
"""数据中台 news/notice 别名读路径 → JEV 新闻分流接线。

从 quant_backtest 读层 datamart.read('news'/'notice') 按水位增量拉取
（news→tdx stock_news；notice→stock_notice 真源命中、anns_d 降级），
映射为 news.db news 表新行（id=xd:<kind>:md5(url) 幂等，source=xd_news/
xd_notice，stocks=[ts_code]），逐条过 JEV 本地模型判定写回 jev 列——
接入与分流一步完成。下游 pushed/digested/摘要管线无需改动。

水位存 runs 表（kind=jev_feed，detail 记各源日期水位），每轮回退 1 天
重叠拉取 + INSERT OR IGNORE 去重（与 tushare_sync 增量同口径）。
单轮截断（拉满 cap）时水位不动，下轮重叠窗口重拉。

用法：cd news_collector && python jev_feed.py [--days 2] [--cap 500]
      [--dryrun]
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))

import datamart as dm      # noqa: E402
import news_ai             # noqa: E402

DB = os.path.join(HERE, "news.db")
FEED_KIND = "jev_feed"


def _xd_id(kind, url, title):
    """幂等主键：xd:<kind>:md5(url|title) 前 12 位（对齐 sina:5115033 风格）。"""
    h = hashlib.md5((url or title or "").encode("utf-8")).hexdigest()[:12]
    return "xd:%s:%s" % (kind, h)


def _norm_ts(pub_date, pub_time=""):
    """YYYYMMDD(+HH:MM:SS) → 'YYYY-MM-DD HH:MM'（对齐 news.ts 口径）。"""
    d = str(pub_date or "").replace("-", "")[:8]
    if len(d) != 8 or not d.isdigit():
        return None
    t = str(pub_time or "")[:5]
    return "%s-%s-%s %s" % (d[:4], d[4:6], d[6:8], t if len(t) == 5 else "00:00")


def load_watermark():
    """读 runs 表最近一次成功 feed 的各源水位 {kind: YYYYMMDD}。"""
    db = sqlite3.connect(DB)
    try:
        row = db.execute(
            "SELECT detail FROM runs WHERE kind=? AND ok=1 "
            "ORDER BY id DESC LIMIT 1", (FEED_KIND,)).fetchone()
    finally:
        db.close()
    if not row:
        return {}
    try:
        return json.loads(row[0]).get("wm", {})
    except Exception:
        return {}


def _pick(row, *names):
    """dict 取首个非空字段。"""
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return v
    return ""


def feed_source(alias, kind, wm_date, days, cap, dryrun=False):
    """拉一个别名 → 映射插入 news.db → 返回 (插入数, 拉取数, max_date)。"""
    if wm_date:
        start = (datetime.strptime(wm_date, "%Y%m%d")
                 - timedelta(days=1)).strftime("%Y%m%d")
    else:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    if dryrun:
        print("  DRY %s <- %s start=%s cap=%d" % (kind, alias, start, cap))
        return 0, 0, None
    df = dm.read(alias, start=start, limit=cap)
    if df is None or df.empty:
        return 0, 0, None
    if len(df) >= cap:
        print("  WARN %s 拉满 cap=%d（截断，水位不推进，下轮重拉）" % (kind, cap))
    db = sqlite3.connect(DB)
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        max_date, n_ins = None, 0
        for row in df.to_dict("records"):
            title = str(_pick(row, "title"))
            if not title:
                continue
            nid = _xd_id(kind, _pick(row, "url"), title)
            ts = _norm_ts(_pick(row, "pub_date", "notice_date", "ann_date"),
                          _pick(row, "pub_time"))
            if ts is None:
                continue
            cur = db.execute("SELECT 1 FROM news WHERE id=?", (nid,)).fetchone()
            if cur:
                continue
            code = str(_pick(row, "ts_code") or _pick(row, "symbol") or "")
            stocks = [code] if code else []
            db.execute(
                "INSERT INTO news(id, source, ts, title, content, hit, "
                "pushed, digested, created_at, stocks, jev) "
                "VALUES (?,?,?,?,?,0,0,0,?,?,NULL)",
                (nid, "xd_%s" % kind, ts, title,
                 str(_pick(row, "content")), now,
                 json.dumps(stocks, ensure_ascii=False)))
            n_ins += 1
            d = ts[:10].replace("-", "")
            if max_date is None or d > max_date:
                max_date = d
        db.commit()
    finally:
        db.close()
    return n_ins, len(df), max_date


def judge_pending(kind, cap, dryrun=False):
    """对本轮 xd_ 来源未判行过 JEV 模型（每批 8 条，内部 0.8s 防限流）。"""
    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT id, title, stocks FROM news "
            "WHERE source=? AND (jev IS NULL OR jev='') "
            "ORDER BY ts DESC LIMIT ?", ("xd_%s" % kind, cap)).fetchall()
        if not rows:
            return 0
        if dryrun:
            print("  DRY judge %s: %d pending" % (kind, len(rows)))
            return 0
        judged = 0
        for i in range(0, len(rows), 8):
            items = []
            for nid, title, stocks in rows[i:i + 8]:
                try:
                    st = json.loads(stocks) if stocks else []
                except Exception:
                    st = []
                items.append({"id": nid, "title": title, "stocks": st})
            for nid, j in news_ai.jev_judge_batch(items).items():
                db.execute("UPDATE news SET jev=? WHERE id=?",
                           (json.dumps(j, ensure_ascii=False), nid))
            db.commit()
            judged += len(items)
            time.sleep(0.5)
        return judged
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2,
                    help="无水位（首跑）回看天数")
    ap.add_argument("--cap", type=int, default=500,
                    help="每源单轮拉取/判定上限")
    ap.add_argument("--dryrun", action="store_true")
    args = ap.parse_args()

    wm = load_watermark()
    print("=== jev_feed 水位=%s days=%d cap=%d dryrun=%s ==="
          % (wm or "无(首跑)", args.days, args.cap, args.dryrun))
    summary = {"wm": {}, "src": {}}
    t0 = time.time()
    total_ins = 0
    for alias, kind in [("news", "news"), ("notice", "notice")]:
        n_ins, n_fetched, max_date = feed_source(
            alias, kind, wm.get(kind), args.days, args.cap,
            dryrun=args.dryrun)
        # 截断不推进水位（下轮重拉）；未截断推进到本轮最大日期
        if not args.dryrun and max_date and n_fetched < args.cap:
            summary["wm"][kind] = max_date
        summary["src"][kind] = {"inserted": n_ins, "fetched": n_fetched}
        total_ins += n_ins
        print("  %s <- %s: inserted=%d fetched=%d max=%s"
              % (kind, alias, n_ins, n_fetched, max_date or "-"))
        j = judge_pending(kind, args.cap, dryrun=args.dryrun)
        if j:
            summary["src"][kind]["judged"] = j
            print("  %s judged=%d" % (kind, j))
    if args.dryrun:
        print("DRYRUN 结束，未写库。")
        return
    db = sqlite3.connect(DB)
    try:
        db.execute(
            "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
            "VALUES (?,?,?,?,0,?)",
            (FEED_KIND, datetime.now().strftime("%Y-%m-%d %H:%M"), 1,
             total_ins, json.dumps(summary, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    print("FEED done inserted=%d wm=%s %.1fs"
          % (total_ins, summary.get("wm", {}), time.time() - t0))


if __name__ == "__main__":
    main()
