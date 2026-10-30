# -*- coding: utf-8 -*-
"""新浪财经直播 news 历史回填（试跑版）：分页往回抓，写 news.db（去重/限流）。

用法：
  python tools/data_sources/news_sina_backfill.py --days 90 --dry-run
  python tools/data_sources/news_sina_backfill.py --days 90
说明：
  - **实测深度上限**：新浪 feed 深页会返回重复/固定尾部，可回填深度约 1 周（page150≈6天，
    继续翻页最早时间不再前移）→ 默认 --days 7；1 年/90 天历史无法回填，只能长期每日采集；
  - 东财/华尔街见闻不可回填；
  - 仅 INSERT OR IGNORE 到 news.db，不删不改；--days 为回填天数上限；
  - 限流 sleep，按页记录进度；进程中断可重跑（已存在 id 自动跳过）。
退出码：0=完成 / 2=中止（网络/接口变化）/ 3=前置失败。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ROOT = Path(r"C:\项目\老田的AI助手")
NEWS_DB = ROOT / "news_collector" / "news.db"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
FEED = "https://zhibo.sina.com.cn/api/zhibo/feed"


def fetch_page(page: int, size: int = 100) -> list[dict]:
    params = {"page": page, "page_size": size, "zhibo_id": 152, "tag_id": 0,
              "dire": "f", "dpc": 1, "type": 0}
    url = FEED + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with safe_urlopen(req, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (((d.get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新浪 news 历史回填")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-pages", type=int, default=4000)
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not NEWS_DB.exists():
        print("news.db missing", file=sys.stderr)
        return 3
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    print("cutoff:", cutoff, "max_pages:", args.max_pages, flush=True)
    con = sqlite3.connect(NEWS_DB, timeout=30)
    exists = {r[0] for r in con.execute("SELECT id FROM news WHERE source='sina'")}
    print("existing sina rows:", len(exists), flush=True)
    inserted = skipped = pages = 0
    min_seen = None
    stagnant = 0
    stopped = False
    for page in range(1, args.max_pages + 1):
        try:
            items = fetch_page(page)
        except Exception as e:  # noqa: BLE001
            print("page %d fetch error: %s %s" % (page, type(e).__name__, str(e)[:80]), flush=True)
            time.sleep(2)
            continue
        if not items:
            print("page %d empty -> stop" % page, flush=True)
            stopped = True
            break
        times = []
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for it in items:
            ct = it.get("create_time") or ""
            sid = str(it.get("id") or "")
            if not ct or not sid:
                continue
            times.append(ct)
            nid = "sina:" + sid
            if nid in exists:
                skipped += 1
                continue
            title = (it.get("rich_text") or it.get("text") or "").strip()
            title = title.split("\n")[0][:200] if title else ""
            content = (it.get("text") or "").strip()
            if args.dry_run:
                inserted += 1
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO news(id,source,ts,title,content,hit,pushed,digested,created_at,stocks,jev)"
                " VALUES(?,?,?,?,?,0,0,0,?, '[]', NULL)",
                (nid, "sina", ct, title, content, now))
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if page % 50 == 0:
            con.commit()
            print("page %d inserted=%d skipped=%d" % (page, inserted, skipped), flush=True)
        pages = page
        if times:
            page_min = min(times)
            if min_seen is None or page_min < min_seen:
                min_seen = page_min
                stagnant = 0
            else:
                stagnant += 1
            if page_min < cutoff:
                print("reached cutoff at page %d (min=%s)" % (page, page_min), flush=True)
                stopped = True
                break
            if stagnant >= 5:
                print("stagnant at page %d: earliest not advancing (min=%s) -> stop (API depth limit)" % (page, page_min), flush=True)
                stopped = True
                break
        time.sleep(args.sleep)
    if not args.dry_run:
        con.commit()
    con.close()
    print(json.dumps({"ok": True, "pages": pages, "inserted": inserted, "skipped": skipped,
                      "stopped": stopped, "days": args.days, "dry_run": args.dry_run},
                     ensure_ascii=False), flush=True)
    return 0 if stopped else 2


if __name__ == "__main__":
    sys.exit(main())
