# -*- coding: utf-8 -*-
"""K263：东方财富股吧舆论采集器（公开列表页 SSR 内嵌 JSON，只读不登录）。

数据源：https://guba.eastmoney.com/list,{code6},{page}.html
  页面内嵌 var article_list={"re":[…80 帖/页…]}，字段含
  post_id / post_title / post_publish_time / post_click_count /
  post_comment_count / user_nickname / bullish_bearish(看多看空)。
合规：仅拉列表页公开标题与计数（不进帖正文、不登录），1.5s/页限速。

存储：data/guba/{code6}.jsonl（post_id 去重增量合并，原子写）。
用法：
  python guba_crawl.py 000338.SZ --pages 2   # 采潍柴动力吧 2 页
  python guba_crawl.py 000338.SZ,600519.SH --pages 1
  python guba_crawl.py --pool                # 日链模式：近30天缠论池活跃股
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "guba")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
THROTTLE = 1.5          # 秒/页
MAX_PAGES = 10          # 硬上限（防失控翻页）
ART_RE = re.compile(r"var article_list=(\{.*?\});?\s*(?:var|</script>)",
                    re.S)


def code6(tc):
    """000338.SZ / sz000338 → 000338。"""
    c = str(tc or "").strip().lower()
    if "." in c:
        c = c.split(".")[0]
    c = re.sub(r"^(sz|sh|bj)", "", c)
    return c if c.isdigit() and len(c) == 6 else ""


def fetch_page(c6, page, timeout=12):
    """单页 → [{核心字段}] 或 []（失败/空页）。"""
    u = ("https://guba.eastmoney.com/list,%s%s.html"
         % (c6, ",%d" % page if page > 1 else ""))
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=timeout)
        m = ART_RE.search(r.text)
        if not m:
            return []
        j = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    for a in j.get("re") or []:
        if not a.get("post_id") or not a.get("post_title"):
            continue
        # 资讯(CMS)/帖子区分：media_type 非空为资讯，保留但打标记
        is_news = bool(a.get("media_type") or a.get("cms_media_type"))
        out.append({
            "post_id": a["post_id"],
            "title": str(a["post_title"]).strip()[:120],
            "ts": a.get("post_publish_time") or a.get("post_display_time"),
            "click": a.get("post_click_count") or 0,
            "comment": a.get("post_comment_count") or 0,
            "user": a.get("user_nickname") or "",
            "bb": a.get("bullish_bearish"),   # 1 看多 -1 看空 None 无
            "news": 1 if is_news else 0,
        })
    return out


def _atomic_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def crawl(tc, pages=2):
    """单只：采 pages 页 → 增量合并 jsonl。Returns (n_new, n_total)。"""
    c6 = code6(tc)
    if not c6:
        print("%s: 代码不合法，跳过" % tc)
        return 0, 0
    path = os.path.join(OUT_DIR, c6 + ".jsonl")
    old, seen = [], set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    seen.add(r["post_id"])
                    old.append(r)
                except Exception:
                    continue
    got = []
    for p in range(1, min(pages, MAX_PAGES) + 1):
        rows = fetch_page(c6, p)
        if not rows:
            print("  %s 第 %d 页空/失败，停止翻页" % (c6, p))
            break
        got += rows
        if p < min(pages, MAX_PAGES):
            time.sleep(THROTTLE)
    n_new = 0
    for r in got:
        if r["post_id"] not in seen:
            old.append(r)
            seen.add(r["post_id"])
            n_new += 1
    old.sort(key=lambda r: r.get("ts") or "")
    _atomic_jsonl(path, old)
    return n_new, len(old)


def pool_codes(pool_days=30, limit=60):
    """近 N 天缠论池有信号的股票（保序去重，上限 limit）。

    活跃股口径：出过 1/2/3 买卖点的股票，舆论采集的价值密度最高；
    池含指数（.SH/.SZ 大盘）与 US/HK 后缀，只收 6 位 A 股码。
    """
    path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
    if not os.path.exists(path):
        return []
    cutoff = (datetime.now() - timedelta(days=pool_days)
              ).strftime("%Y%m%d")
    seen = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            c = str(r.get("code") or "")
            m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", c, re.I)
            if not m:
                continue                     # 指数/US/HK 不采
            if str(r.get("dt") or "").replace("-", "")[:8] < cutoff:
                continue
            seen.setdefault(m.group(1), c)   # 首见序 = 信号日序
    return list(seen.values())[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default=None,
                    help="代码（逗号分隔）；省略且 --pool 时自动取池")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--pool", action="store_true",
                    help="日链模式：近30天缠论池活跃股")
    ap.add_argument("--pool-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if a.pool:
        codes = pool_codes(a.pool_days, a.limit)
        print("池模式：近 %d 天活跃股 %d 只（上限 %d）"
              % (a.pool_days, len(codes), a.limit), flush=True)
        if not codes:
            print("池为空，正常退出")
            return
    else:
        codes = [c.strip() for c in (a.codes or "").split(",") if c.strip()]
    t0, n_all_new = time.time(), 0
    for i, tc in enumerate(codes, 1):
        n_new, n_all = crawl(tc, a.pages)
        n_all_new += n_new
        print("[%d/%d] %s: 新增 %d 累计 %d" % (i, len(codes), tc, n_new, n_all),
              flush=True)
    print("池模式完成：%d 只，新增 %d 帖，用时 %.0fs"
          % (len(codes), n_all_new, time.time() - t0))


if __name__ == "__main__":
    main()
