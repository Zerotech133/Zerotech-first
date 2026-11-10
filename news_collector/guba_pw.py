# -*- coding: utf-8 -*-
"""股吧个股页定向采集·Playwright 渲染版（2026-11-09）。

背景：东财股吧网页版 2026-11-09 起对 requests 下发「身份核实」拦截页
（no_marker），但真实浏览器渲染后页面内嵌 var article_list JSON 完好——
本模块用 Playwright 渲染拿 HTML，复用 guba_crawl 的解析与去重写盘
（ART_RE / _load_posts / _atomic_jsonl / OUT_DIR），行结构完全一致。

用法：python guba_pw.py "301563,920870,..." [--pages 1] [--wait-ms 8000]
     python guba_pw.py --queue   # 读 target_queue_chanlun.json actionable
只采第 1 页（最热 ~80 帖）即可覆盖「今日补齐」口径；每只间 sleep 2s。
"""
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

GUBA_ROOT = r"C:/aios/quant_backtest"
if GUBA_ROOT not in sys.path:
    sys.path.insert(0, GUBA_ROOT)
import guba_crawl as G

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(HERE, "target_queue_chanlun.json")


def queue_codes():
    q = json.load(open(QUEUE_PATH, encoding="utf-8"))
    out = []
    for it in q["actionable"]:
        m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", it["code"], re.I)
        if m:
            out.append(m.group(1))
    return out


def parse_rows(html):
    """渲染 HTML → rows（与 guba_crawl.fetch_page 行结构一致）。"""
    m = G.ART_RE.search(html)
    if not m:
        return None                        # 无内嵌 JSON（仍未通过/改版）
    try:
        j = json.loads(m.group(1))
    except Exception:
        return None
    out = []
    for a in j.get("re") or []:
        if not a.get("post_id") or not a.get("post_title"):
            continue
        is_news = bool(a.get("media_type") or a.get("cms_media_type"))
        out.append({
            "post_id": a["post_id"],
            "title": str(a["post_title"]).strip()[:120],
            "ts": a.get("post_publish_time") or a.get("post_display_time"),
            "click": a.get("post_click_count") or 0,
            "comment": a.get("post_comment_count") or 0,
            "user": a.get("user_nickname") or "",
            "bb": a.get("bullish_bearish"),
            "news": 1 if is_news else 0,
        })
    return out


def main():
    argv = sys.argv[1:]
    wait_ms = 8000
    if "--wait-ms" in argv:
        i = argv.index("--wait-ms")
        wait_ms = int(argv[i + 1])
        del argv[i:i + 2]
    if "--queue" in argv:
        codes = queue_codes()
    else:
        codes = [c.strip() for c in argv[0].split(",") if c.strip()]
    pages = 1
    if "--pages" in argv:
        i = argv.index("--pages")
        pages = int(argv[i + 1])
    print("定向采集 %d 只 × %d 页（Playwright 渲染版）" % (len(codes), pages),
          flush=True)

    ok = err = n_new_total = 0
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False,
                              args=["--window-position=3000,3000"])
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                            " AppleWebKit/537.36 (KHTML, like Gecko)"
                            " Chrome/157.0.0.0 Safari/537.36", locale="zh-CN")
        page = ctx.new_page()
        for i, c6 in enumerate(codes, 1):
            path = os.path.join(G.OUT_DIR, c6 + ".jsonl")
            old, seen = G._load_posts(path)
            got, status = [], "ok"
            try:
                for pg in range(1, pages + 1):
                    u = "https://guba.eastmoney.com/list,%s%s.html" % (
                        c6, ",%d" % pg if pg > 1 else "")
                    page.goto(u, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(wait_ms)
                    rows = parse_rows(page.content())
                    if rows is None:
                        status = "no_marker"
                        break
                    got.extend(rows)
            except Exception as e:
                status = "exception:%s" % str(e)[:60]
            if status != "ok":
                err += 1
                print("[%d/%d] %s: FAIL %s" % (i, len(codes), c6, status),
                      flush=True)
            else:
                n_new = 0
                for r in got:
                    if r["post_id"] not in seen:
                        old.append(r)
                        seen.add(r["post_id"])
                        n_new += 1
                if n_new > 0:
                    old.sort(key=lambda r: r.get("ts") or "")
                    G._atomic_jsonl(path, old)
                ok += 1
                n_new_total += n_new
                print("[%d/%d] %s: ok 新增 %d 库内 %d" % (i, len(codes), c6,
                                                          n_new, len(old)),
                      flush=True)
            time.sleep(2.0)
        ctx.close()
        b.close()
    print("DONE ok=%d fail=%d 新增=%d 耗时=%.0fs" % (ok, err, n_new_total,
                                                     time.time() - t0))


if __name__ == "__main__":
    main()
