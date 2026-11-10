# -*- coding: utf-8 -*-
"""雪球按票定向采集器（开源情报补缺：雪球源对队列票覆盖稀疏）。

路线复用 xq_kol.py 判例：活登录 profile + 页内 fetch（credentials
include）+ WAF 挑战自愈（reload 重武装）+ 405/WAF 停轮 + 只读 GET +
限速 2-4s 抖动。接口：/query/v1/symbol/search/status（个股 timeline，
sortId=2 最新优先）。

产物：data/xq_timeline/{code}.jsonl（append，按 status_id 去重）。
字段：id/code/name/ts/text/title/user_id/view/like/comment/reply。

CLI：
  python xq_stock_timeline.py --queue actionable   # 65 只先验
  python xq_stock_timeline.py --queue all          # 全队列
  python xq_stock_timeline.py 600519,000858        # 指定票
"""
import argparse
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime

from xq_kol import PROFILE_DIR, PROFILE_FALLBACK

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "xq_timeline")
QUEUE_F = os.path.join(HERE, "target_queue_chanlun.json")
PAGES = 2
COUNT = 20
_BODY_CAP = 800

API_JS = """
async (arg) => {
  try {
    const r = await fetch('/query/v1/symbol/search/status?sortId=2&count=' +
        arg.count + '&comment=0&symbol=' + arg.symbol +
        '&hl=0&source=all&sort=&page=' + arg.page + '&q=&type=',
        {credentials: 'include', headers: {'Accept': 'application/json'}});
    if (r.status !== 200) {
      return {err: 'HTTP ' + r.status, body: (await r.text()).slice(0, 200)};
    }
    const j = await r.json();
    const list = j.list || j.statuses || [];
    return {ok: true, total: j.total,
            items: list.map(s => ({
              id: String(s.id),
              created_at_ms: s.created_at,
              title: (s.title || '').slice(0, 300),
              text: (s.text || s.description || '').slice(0, 2000),
              user_id: (s.user && s.user.id) || null,
              view_count: s.view_count || 0,
              like_count: s.like_count || 0,
              reply_count: s.reply_count || 0,
              retweet_count: s.retweet_count || 0,
            }))};
  } catch (e) {
    return {err: 'WAF_CHALLENGE ' + String(e).slice(0, 80)};
  }
}
"""


def _strip_html(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:_BODY_CAP]


def _ms_to_cst(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def load_queue(scope):
    cfg = json.load(open(QUEUE_F, encoding="utf-8"))
    items = cfg.get(scope) or cfg.get("all") or []
    codes = {i["code"].split(".")[0] for i in items}
    # 侯风桥(HOUFENG)：并集合并侯风选股队列，纯增量，不削减现有缠论队列覆盖
    hfq = os.path.join(HERE, "target_queue_houfeng.json")
    if os.path.exists(hfq):
        try:
            hc = json.load(open(hfq, encoding="utf-8"))
            hitems = hc.get(scope) or hc.get("all") or []
            codes |= {i["code"].split(".")[0] for i in hitems}
            # 候选池(影子池全量)默认不并入 07:00 有头采集管线；
            # 开关 xq_include_candidates 由上级裁定后置 true 才生效（489 -> 593）
            if hc.get("xq_include_candidates") is True:
                ext = hc.get("candidate_pool") or []
                codes |= {i["code"].split(".")[0] for i in ext}
        except Exception:
            pass
    return sorted(codes)


def load_seen(path):
    seen = set()
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            try:
                seen.add(json.loads(ln)["id"])
            except Exception:
                continue
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="")
    ap.add_argument("--queue", choices=["actionable", "all"], default="")
    ap.add_argument("--pages", type=int, default=PAGES)
    args = ap.parse_args()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        scope = args.queue or "actionable"
        codes = load_queue(scope)
    print("[xq_tl] 标的 %d 只，每只 %d 页 x %d 条" % (
        len(codes), args.pages, COUNT), flush=True)

    from playwright.sync_api import sync_playwright
    os.makedirs(OUT_DIR, exist_ok=True)
    total_new = 0
    halted = ""
    with sync_playwright() as p:
        ctx = None
        for prof in (PROFILE_DIR, PROFILE_FALLBACK):
            if not os.path.isdir(prof):
                continue
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=prof, headless=False, viewport=None,
                    locale="zh-CN",
                    args=["--disable-blink-features=AutomationControlled",
                          "--window-position=3000,3000"])
                break
            except Exception:
                continue
        if ctx is None:
            print("FAIL 无可用登录 profile")
            return 1
        page = ctx.new_page()
        page.goto("https://xueqiu.com", wait_until="domcontentloaded",
                  timeout=45000)
        page.wait_for_timeout(6000)
        for i, code in enumerate(codes):
            if halted:
                break
            symbol = ("SH" if code.startswith(("6", "9")) else "SZ") + code
            path = os.path.join(OUT_DIR, "%s.jsonl" % code)
            seen = load_seen(path)
            new = 0
            for pg in range(1, args.pages + 1):
                data = page.evaluate(
                    API_JS, {"symbol": symbol, "page": pg, "count": COUNT})
                if not data.get("ok"):
                    err = str(data.get("err") or "")
                    if "WAF_CHALLENGE" in err or err.startswith("HTTP 4"):
                        print("[xq_tl] %s p%d 防御触发(%s)——停轮防拉黑"
                              % (code, pg, err[:40]), flush=True)
                        halted = err
                        break
                    print("[xq_tl] %s p%d %s" % (code, pg, err[:60]),
                          flush=True)
                    break
                items = data.get("items") or []
                if not items:
                    break
                with open(path, "a", encoding="utf-8") as f:
                    for it in items:
                        if it["id"] in seen:
                            continue
                        seen.add(it["id"])
                        rec = {"id": "xqtl:%s:%s" % (code, it["id"]),
                               "code": code, "symbol": symbol,
                               "ts": _ms_to_cst(it.get("created_at_ms")),
                               "title": _strip_html(it.get("title")),
                               "text": _strip_html(it.get("text")),
                               "user_id": it.get("user_id"),
                               "view": it.get("view_count"),
                               "like": it.get("like_count"),
                               "reply": it.get("reply_count"),
                               "src": "xq.stock_timeline"}
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        new += 1
                time.sleep(2 + random.random() * 2)
            total_new += new
            if (i + 1) % 10 == 0 or new:
                print("[xq_tl] %d/%d %s +%d" % (i + 1, len(codes), code, new),
                      flush=True)
        ctx.close()
    print("[xq_tl] 完成：新增 %d（halted=%s）" % (total_new, halted[:40]),
          flush=True)
    return 0 if not halted else 2


if __name__ == "__main__":
    sys.exit(main())
