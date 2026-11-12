# -*- coding: utf-8 -*-
"""雪球按票定向采集器（开源情报补缺：雪球源对队列票覆盖稀疏）。

路线复用 xq_kol.py 判例：活登录 profile + 页内 fetch（credentials
include）+ WAF 挑战自愈（reload 重武装）+ 405/WAF 停轮 + 只读 GET +
限速 2-4s 抖动。接口：/query/v1/symbol/search/status（个股 timeline，
sortId=2 最新优先）。

产物：data/xq_timeline/{code}.jsonl（append，按 status_id 去重）。
覆盖：沪(6/9/5) / 深(0/3/2) / **北(43/83/87/92)** 三市——北交所支持见
`to_symbol()`，2026-11-13 OPS-1113-01 修（旧写法把 92xxxx 拼成 SH920870，
雪球回空列表且 HTTP 200，静默丢 23 只）。
字段：id/code/symbol/ts/text/title/user_id/view/like/reply/link/src
（link 为雪球 permalink 溯源字段，2026-11-11 L3 补；存量由 xq_backfill_link.py 幂等回填）。

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
REG_F = os.path.join(HERE, "selection_universe.json")
PAGES = 2
COUNT = 20
_BODY_CAP = 800

# WAF 优雅降级参数（2026-11-11 裁定：单点抖动不再整轮停轮，仅持续封锁才停）
WAF_COOLDOWN = 300            # 撞 WAF 后冷却秒数（对齐台账"数分钟后自愈"观测）
WAF_RETRY = 3                # 冷却后同码同页重试次数
SUSTAINED_ABORT_PCT = 0.20   # 持续封锁阈值：waf_hits/已处理 > 此值才整轮停（防拉黑）
MAX_DEFERRED_ATTEMPTS = 5    # 单码跨日重试上限，超过则放弃（防冷却死循环）
DEFERRED_F = os.path.join(OUT_DIR, "_deferred.json")  # 跨日自愈 ledger

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
              link: ('https://xueqiu.com/' + ((s.user && s.user.id) ? s.user.id : 'status') + '/' + s.id),
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


def _codes_from_source(src):
    """从注册表声明的某个选股源抽取 6 位代码集合。"""
    f = src.get("file", "")
    path = f if os.path.isabs(f) else os.path.join(HERE, f)
    if not os.path.exists(path):
        # 回退到 aios 仓库根（如 research/... 相对路径）
        alt = os.path.join(os.path.dirname(HERE), f)
        if os.path.exists(alt):
            path = alt
        else:
            return set()
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception:
        return set()
    code_field = src.get("code_field")
    if code_field:  # 如 S12 篮子用 ts_code
        items = cfg.get(src.get("scope") or "basket") or cfg.get("basket") or []
        return {str(b.get(code_field, "")).split(".")[0] for b in items if b.get(code_field)}
    pool = src.get("pool")
    if pool and pool in cfg:  # 如 houfeng candidate_pool
        items = cfg[pool]
    else:
        scope = src.get("scope")
        items = cfg.get(scope) or cfg.get("all") or []
    return {str(i.get("code", "")).split(".")[0] for i in items if i.get("code")}


def to_symbol(code):
    """6 位代码 → 雪球 symbol（市场前缀 + 代码）。

    判据按交易所代码段，**不再用「6 或 9 开头即沪市」**——该口径把北交所 92xxxx
    也拼成 SH（详见工单 OPS-1113-01）：雪球对 SH920870 返回空列表且 HTTP 200，
    静默无内容、不进 deferred，SLA 上线前完全不可见。

      43/83/87/92 → BJ（北交所，含新代码段 920）
      6/9/5       → SH（沪主板、沪 B 900、沪基金 5xx）
      其余        → SZ（深主板 00、创业板 30、深 B 200）
    """
    code = str(code).split(".")[0].zfill(6)
    if code.startswith(("43", "83", "87", "92")):
        return "BJ" + code
    if code.startswith(("6", "9", "5")):
        return "SH" + code
    return "SZ" + code


def load_queue(scope="all", sources=None):
    """B 注册表架构：只读 selection_universe.json，并集所有 xq_include=true 的源。

    sources：可选，指定只取这些源 key 的并集（用于扩源独立分片 run），
    绕过 xq_include 开关（分片 run 由调度显式指定，责任在运维侧）。
    scope 参数保留兼容 CLI，但并集范围由各源注册声明决定（日常 07:00 即全量并集）。
    """
    if not os.path.exists(REG_F):
        # 兜底：旧行为（仅缠论 + 侯风选股），避免注册表缺失时管线断流
        cfg = json.load(open(QUEUE_F, encoding="utf-8"))
        items = cfg.get("all") or []
        codes = {i["code"].split(".")[0] for i in items}
        hfq = os.path.join(HERE, "target_queue_houfeng.json")
        if os.path.exists(hfq):
            hc = json.load(open(hfq, encoding="utf-8"))
            codes |= {i["code"].split(".")[0] for i in (hc.get("all") or [])}
        return sorted(codes)
    reg = json.load(open(REG_F, encoding="utf-8"))
    srcs = reg.get("sources", {})
    if sources:  # 分片模式：只取指定源 key 的并集
        codes = set()
        for key in sources:
            if key in srcs:
                codes |= _codes_from_source(srcs[key])
        return sorted(codes)
    codes = set()
    for key, src in srcs.items():
        if not src.get("xq_include"):
            continue
        codes |= _codes_from_source(src)
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


def _load_deferred():
    """跨日自愈：读取上一轮 deferred ledger（{code: 已重试次数}）。"""
    if not os.path.exists(DEFERRED_F):
        return {}
    try:
        d = json.load(open(DEFERRED_F, encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(d, list):  # 兼容旧格式（纯列表）
        return {str(c): 1 for c in d}
    return {str(k): int(v) for k, v in d.items()}


def _fetch_page(page, symbol, pg):
    """页内 fetch 封装；返回 (ok, data, err)。"""
    data = page.evaluate(API_JS, {"symbol": symbol, "page": pg, "count": COUNT})
    if data.get("ok"):
        return True, data, ""
    return False, data, str(data.get("err") or "")


def _append_items(f, seen, code, symbol, items):
    """把一页 items 去重落盘，返回新增数。"""
    new = 0
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
               "link": it.get("link"),
               "src": "xq.stock_timeline"}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        new += 1
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="")
    ap.add_argument("--queue", choices=["actionable", "all"], default="")
    ap.add_argument("--sources", default="",
                    help="分片模式：逗号分隔注册表源 key，只取这些源（绕过 xq_include）")
    ap.add_argument("--pages", type=int, default=PAGES)
    args = ap.parse_args()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        scope = args.queue or "all"
        src_list = [s.strip() for s in args.sources.split(",") if s.strip()]
        codes = load_queue(scope, sources=src_list or None)
    # 跨日自愈：deferred 码前置重试（仅队列/分片模式；手动指定码不前置）
    deferred_prev = {}
    if not args.codes:
        deferred_prev = _load_deferred()
        if deferred_prev:
            prev_keys = list(deferred_prev.keys())
            codes = prev_keys + [c for c in codes if c not in deferred_prev]
            print("[xq_tl] 跨日自愈：前置 deferred %d 只" % len(prev_keys),
                  flush=True)
    print("[xq_tl] 标的 %d 只，每只 %d 页 x %d 条" % (
        len(codes), args.pages, COUNT), flush=True)

    from playwright.sync_api import sync_playwright
    os.makedirs(OUT_DIR, exist_ok=True)
    total_new = 0
    halted = ""
    waf_hits = 0
    deferred_now = {}          # {code: 已重试次数}，结束写回 ledger
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
        n = len(codes)
        for i, code in enumerate(codes):
            if halted:
                break
            symbol = to_symbol(code)
            path = os.path.join(OUT_DIR, "%s.jsonl" % code)
            seen = load_seen(path)
            new = 0
            waf_this = False
            for pg in range(1, args.pages + 1):
                ok, data, err = _fetch_page(page, symbol, pg)
                if ok:
                    items = data.get("items") or []
                    if not items:
                        break
                    with open(path, "a", encoding="utf-8") as f:
                        new += _append_items(f, seen, code, symbol, items)
                    time.sleep(2 + random.random() * 2)
                    continue
                # 非 ok
                if "WAF_CHALLENGE" in err or err.startswith("HTTP 4"):
                    # 优雅降级：冷却重试，重试耗尽则 defer 续跑，不整轮停
                    waf_this = True
                    recovered = False
                    for _ in range(WAF_RETRY):
                        print("[xq_tl] %s p%d 防御触发(%s)——冷却%d秒后重试"
                              % (code, pg, err[:40], WAF_COOLDOWN), flush=True)
                        time.sleep(WAF_COOLDOWN)
                        ok2, data2, err2 = _fetch_page(page, symbol, pg)
                        if ok2:
                            items = data2.get("items") or []
                            if items:
                                with open(path, "a", encoding="utf-8") as f:
                                    new += _append_items(f, seen, code, symbol, items)
                            recovered = True
                            break
                        err = err2
                    if recovered:
                        time.sleep(2 + random.random() * 2)
                        continue
                    # 重试耗尽：本码 defer，续跑下一码（不整轮停）
                    attempts = deferred_prev.get(code, 0) + 1
                    if attempts > MAX_DEFERRED_ATTEMPTS:
                        print("[xq_tl] %s 重试%d次仍封锁——放弃"
                              % (code, MAX_DEFERRED_ATTEMPTS), flush=True)
                    else:
                        deferred_now[code] = attempts
                        print("[xq_tl] %s 冷却重试%d次仍封锁——defer 下轮续跑"
                              % (code, WAF_RETRY), flush=True)
                    break
                # 其它错误（非 WAF，如 5xx）：打印并跳过本码，续跑下一码
                print("[xq_tl] %s p%d %s" % (code, pg, err[:60]), flush=True)
                break
            if waf_this:
                waf_hits += 1
            total_new += new
            # 持续封锁判定：撞 WAF 比例超阈值 → 整轮停（真·拉黑风险）
            if waf_hits and (waf_hits / max(1, i + 1)) > SUSTAINED_ABORT_PCT:
                halted = "SUSTAINED_WAF_BLOCK"
                if code not in deferred_now:
                    deferred_now[code] = deferred_prev.get(code, 0) + 1
                print("[xq_tl] 持续封锁(%.0f%%>%.0f%%)——整轮停防拉黑"
                      % (100.0 * waf_hits / max(1, i + 1),
                         100.0 * SUSTAINED_ABORT_PCT), flush=True)
                break
            if (i + 1) % 10 == 0 or new:
                print("[xq_tl] %d/%d %s +%d" % (i + 1, n, code, new), flush=True)
        ctx.close()
    # 落 deferred ledger（供下轮前置重试；空则写空对象）
    try:
        with open(DEFERRED_F, "w", encoding="utf-8") as f:
            json.dump(deferred_now, f, ensure_ascii=False)
    except Exception:
        pass
    print("[xq_tl] 完成：新增 %d（halted=%s, deferred=%d）"
          % (total_new, halted[:40], len(deferred_now)), flush=True)
    return 0 if not halted else 2


if __name__ == "__main__":
    sys.exit(main())
