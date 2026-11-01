# -*- coding: utf-8 -*-
"""入口探针：近期线（2024-2026 空窗）候选入口可达性与年代测定（只读）。

老田 2026-10-18 拍板三项之②③的前置探针：
  E1/E2  category feed 首页年代（102 沪深 / 104 港股）——若首页即 2026 当下，
         近期线无需新入口，直接挖 feed 头部即可；
  E3     hot/listV2 热榜（近端第二入口候选）；
  E4     search.json 个股讨论（v1 复盘判 404，复测定谳）；
  E5     user_timeline.json 匿名（v1 判 WAF 拦，复测定谳）。

合规：只读 GET、页间 2s 抖动、不写库、不落盘除本报告 stdout。
用法：python tools/xq_probe_entries.py
"""

import sys
import time
from datetime import datetime

HERE = r"C:\项目\老田的AI助手\quant_backtest"
sys.path.insert(0, HERE)

from xueqiu_crawl import XqCrawler  # noqa: E402

SOCIAL = "https://xueqiu.com"


def year_range(rows):
    ds = [r.get("created_at") or "" for r in rows if r.get("created_at")]
    return (min(ds), max(ds)) if ds else ("-", "-")


def main() -> int:
    cr = XqCrawler()
    out = []

    for cat, name in ((102, "E1 沪深 feed 首页"), (104, "E2 港股 feed 首页")):
        rows = cr.fetch_timeline(category=cat, count=20, max_pages=1)
        if rows is None:
            out.append((name, "FAIL(请求失败/风控)", "-", "-", 0))
        else:
            mn, mx = year_range(rows)
            out.append((name, "OK", mn, mx, len(rows)))
        time.sleep(2)

    rows = cr.fetch_hot(count=30)
    if rows is None:
        out.append(("E3 热榜 listV2", "FAIL", "-", "-", 0))
    else:
        mn, mx = year_range(rows)
        out.append(("E3 热榜 listV2", "OK", mn, mx, len(rows)))
    time.sleep(2)

    rows = cr.fetch_comments("SH600519", max_pages=1)
    if rows is None:
        out.append(("E4 个股讨论 search.json", "FAIL(疑似仍404)", "-", "-", 0))
    else:
        mn, mx = year_range(rows)
        tag = "OK(复话)" if rows else "OK但空"
        out.append(("E4 个股讨论 search.json", tag, mn, mx, len(rows)))
    time.sleep(2)

    # E5 user_timeline 匿名复测（v1 判 WAF 拦）：只发 1 发，40x 即定谳
    try:
        r = cr.sess.get(
            SOCIAL + "/v4/statuses/user_timeline.json",
            params={"user_id": 9222060866, "count": 5, "page": 1},
            timeout=15,
        )
        body = r.text[:80].replace("\n", " ")
        out.append(
            ("E5 user_timeline 匿名", "HTTP %d %s" % (r.status_code, body), "-", "-", 0)
        )
    except Exception as e:
        out.append(("E5 user_timeline 匿名", "EXC %r" % e, "-", "-", 0))

    print("=== 入口探针 %s ===" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    for name, tag, mn, mx, n in out:
        print("%-26s %-28s rows=%-4d span=%s ~ %s" % (name, tag, n, mn, mx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
