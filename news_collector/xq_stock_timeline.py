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
CONSEC_FAIL_HALT = 3         # 连续整只失败达此数 → 判会话被彻底拒，整轮停
CONSEC_FAIL_COOLDOWN = 60    # 近期已出现失败后的快冷却秒数（长冷却已被实测证明无效）
SESSION_WINDOW = 20          # 会话健康度滑动窗口（只数）
SESSION_MIN_SAMPLES = 10     # 窗口样本不足不做坏率判定
SESSION_BAD_RATIO = 0.30     # 窗口坏率 ≥ 此值 → 判限流态，整轮停（换时段再跑，不硬刚）
RATE_FLOOR = 5.0             # 首次撞限流后启用的额外间隔（秒）
RATE_CAP = 30.0              # 额外间隔上限（秒）——再慢也不会无限拖
RATE_DECAY = 1.0             # 成功后每只回调的秒数（逐步恢复原速）
DEFERRED_F = os.path.join(OUT_DIR, "_deferred.json")  # 跨日自愈 ledger
CURSOR_F = os.path.join(OUT_DIR, "_cursor.json")      # 断点游标（尾部饥饿治理）

# ── 断点游标（2026-11-13，判例：11-12 07:00 与 21:00 两轮均被强杀，队列尾部
# 600780 起连续 136 只 52.8h 未刷新）。每轮从上次结束处继续，配额限制单轮时长；
# 游标每码落一次盘（原子替换），中途 SIGTERM 也能从断点续跑。
DEFAULT_QUOTA = int(os.environ.get("XQ_SHARD_QUOTA") or 0)   # 0=不限（兼容旧行为）

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


def _load_cursor():
    """读取断点游标。结构：{scope, sources, last_code, queue_len, round, quota, updated_at}。"""
    if not os.path.exists(CURSOR_F):
        return {}
    try:
        d = json.load(open(CURSOR_F, encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _save_cursor(last_code, scope, sources, queue_len, rnd, quota, done):
    """原子写游标（tmp + fsync + os.replace），保证被强杀也不会写出半截 JSON。"""
    payload = {
        "scope": scope, "sources": sources or "",
        "last_code": last_code, "queue_len": queue_len,
        "round": rnd, "quota": quota, "done_this_run": done,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = CURSOR_F + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CURSOR_F)
    except Exception:
        pass


def rotate_from(codes, last_code):
    """把顺序队列旋转成「上次结束处之后」开头。

    返回 (ordered, wrapped, start_idx)。last_code 不在队列（名单变了/首次）则原序。
    wrapped=True 表示上次恰好跑到队尾，本轮是新一轮的起点。
    """
    codes = list(codes)
    if not last_code or last_code not in codes:
        return codes, False, -1
    i = codes.index(last_code)
    ordered = codes[i + 1:] + codes[:i + 1]
    return ordered, (i + 1 >= len(codes)), i


def window_bad_ratio(outcomes):
    """最近 N 只的失败占比（outcomes: 1=成功 / 0=整只失败，尾部为最新）。"""
    if not outcomes:
        return 0.0
    return 1.0 - (sum(outcomes) / float(len(outcomes)))


def recent_fail_count(outcomes, k=3):
    """最近 k 只里的失败只数——用来决定冷却策略。"""
    return sum(1 for x in outcomes[-k:] if not x)


def retry_policy(recent_fail):
    """最近 k 只的失败只数 → (重试次数, 冷却秒数)。

    一只都没失败：按 WAF_RETRY × WAF_COOLDOWN 走，给瞬时抖动留自愈机会。
    近期出现过失败：长冷却已被实测证明无效——2026-11-13 生产轮两次独立会话都在
    **第 401 次请求**撞墙，且撞墙后 3 × 300 秒冷却只换来 1 只通过——此时降级为
    单次快冷却，把「每只空耗 15 分钟」压到 1 分钟内。
    """
    if recent_fail <= 0:
        return WAF_RETRY, WAF_COOLDOWN
    return 1, CONSEC_FAIL_COOLDOWN


def next_interval(cur, waf_hit):
    """礼貌降速：撞限流后主动放慢，而不是换会话绕过去。

    语义（判例 2026-11-13 §16.6，老田裁定「不把撞墙就换会话固化成策略」）：
      - 首次撞墙：额外间隔从 `RATE_FLOOR`（5 秒）起；
      - 再撞：翻倍（5→10→20→30），上限 `RATE_CAP`；
      - 成功后每只回调 `RATE_DECAY`（1 秒），回落到 0 即恢复原速。

    单位是「每只额外等待秒数」，与既有的 2~4 秒随机 sleep 叠加。
    降速是**降速自律**，与「换会话规避」是两件事：后者会让对方看到持续高压，
    本函数让对方看到的是我们主动减速。
    """
    if waf_hit:
        nxt = RATE_FLOOR if cur <= 0 else cur * 2
        return min(RATE_CAP, nxt)
    if cur > 0:
        return max(0.0, cur - RATE_DECAY)
    return 0.0


def should_halt(outcomes):
    """轮内自停判据，返回停止原因（空串=继续）。

    两档：
      SESSION_DEAD      连续 CONSEC_FAIL_HALT 只全灭 —— 会话被彻底拒，立刻停；
      SESSION_DEGRADED  成败交替但窗口坏率 ≥ SESSION_BAD_RATIO —— 典型限流态。
                        必须用坏率而非「连续失败」：2026-11-13 实测撞墙后是
                        「失败-成功-失败」交替，连续计数被成功不断清零，永不触发，
                        结果以 900 秒/只的代价空转到 15 小时。
    """
    if len(outcomes) >= CONSEC_FAIL_HALT and \
            outcomes[-CONSEC_FAIL_HALT:] == [0] * CONSEC_FAIL_HALT:
        return "SESSION_DEAD"
    if len(outcomes) >= SESSION_MIN_SAMPLES and \
            window_bad_ratio(outcomes) >= SESSION_BAD_RATIO:
        return "SESSION_DEGRADED"
    return ""


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
    ap.add_argument("--quota", type=int, default=DEFAULT_QUOTA,
                    help="单轮最多处理只数（0=不限）；配合断点游标把长跑切成多轮")
    ap.add_argument("--no-cursor", action="store_true",
                    help="忽略断点游标，本轮从队列头部开始（排障用）")
    ap.add_argument("--reset-cursor", action="store_true",
                    help="不读旧游标（本轮顺序照常），用于名单大改后重排")
    ap.add_argument("--plan-only", action="store_true",
                    help="只打印本轮解析出的顺序/配额/游标，不启浏览器（验配置用）")
    args = ap.parse_args()
    scope, src_key, cursor_on, rnd, queue_len = "", "", False, 1, 0
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        queue_len = len(codes)
    else:
        scope = args.queue or "all"
        src_list = [s.strip() for s in args.sources.split(",") if s.strip()]
        src_key = ",".join(src_list)
        base = load_queue(scope, sources=src_list or None)
        queue_len = len(base)
        cursor_on = not args.no_cursor
        cur = {} if args.reset_cursor else _load_cursor()
        # 游标按 (scope, sources) 分域：全量轮与分片轮的断点互不串味
        if cursor_on and cur and cur.get("scope") == scope \
                and (cur.get("sources") or "") == src_key:
            rnd = int(cur.get("round") or 1)
            last = str(cur.get("last_code") or "")
            if last:
                base, wrapped, idx = rotate_from(base, last)
                if wrapped:
                    rnd += 1
                print("[xq_tl] 断点续跑：上次止于 %s（队列第 %d/%d）→ 本轮自 %s 起%s"
                      % (last, idx + 1, queue_len, base[0] if base else "-",
                         "，进入第 %d 轮" % rnd if wrapped else ""), flush=True)
        if args.quota and args.quota > 0 and len(base) > args.quota:
            print("[xq_tl] 配额：本轮 %d/%d 只，余下由下轮从断点续跑"
                  % (args.quota, len(base)), flush=True)
            base = base[:args.quota]
        codes = list(base)
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
    if args.plan_only:
        head = ",".join(codes[:6]) + ("…" if len(codes) > 6 else "")
        tail = ",".join(codes[-3:]) if len(codes) > 3 else ""
        print("[xq_tl] PLAN scope=%s sources=%s quota=%s cursor=%s round=%d "
              "queue_len=%d 本轮=%d 起=%s 止=%s"
              % (scope or "-", src_key or "-", args.quota,
                 "off" if not cursor_on else "on", rnd, queue_len,
                 len(codes), head, tail or "-"), flush=True)
        return 0

    from playwright.sync_api import sync_playwright
    os.makedirs(OUT_DIR, exist_ok=True)
    total_new = 0
    halted = ""
    waf_hits = 0
    outcomes = []              # 最近 N 只成败（1/0），用于会话健康度判定
    rate_sleep = 0.0           # 限流自适应额外间隔（秒/只），0=原速
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
            code_dead = False       # 本只「重试耗尽仍被拒」→ 会话失效信号
            for pg in range(1, args.pages + 1):
                ok, data, err = _fetch_page(page, symbol, pg)
                if ok:
                    items = data.get("items") or []
                    if not items:
                        break
                    with open(path, "a", encoding="utf-8") as f:
                        new += _append_items(f, seen, code, symbol, items)
                    time.sleep(2 + random.random() * 2 + rate_sleep)
                    continue
                # 非 ok
                if "WAF_CHALLENGE" in err or err.startswith("HTTP 4"):
                    # 优雅降级：冷却重试，重试耗尽则 defer 续跑，不整轮停
                    waf_this = True
                    recovered = False
                    n_fail = recent_fail_count(outcomes)
                    n_try, n_cool = retry_policy(n_fail)
                    for _ in range(n_try):
                        print("[xq_tl] %s p%d 防御触发(%s)——冷却%d秒后重试%s"
                              % (code, pg, err[:40], n_cool,
                                 "（近 3 只已失败 %d 只，降级快冷却）" % n_fail
                                 if n_fail else ""), flush=True)
                        time.sleep(n_cool)
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
                        time.sleep(2 + random.random() * 2 + rate_sleep)
                        continue
                    # 重试耗尽：本码 defer，续跑下一码（不整轮停）
                    code_dead = True
                    attempts = deferred_prev.get(code, 0) + 1
                    if attempts > MAX_DEFERRED_ATTEMPTS:
                        print("[xq_tl] %s 重试%d次仍封锁——放弃"
                              % (code, MAX_DEFERRED_ATTEMPTS), flush=True)
                    else:
                        deferred_now[code] = attempts
                        print("[xq_tl] %s 冷却重试%d次仍封锁——defer 下轮续跑"
                              % (code, n_try), flush=True)
                    break
                # 其它错误（非 WAF，如 5xx）：打印并跳过本码，续跑下一码
                print("[xq_tl] %s p%d %s" % (code, pg, err[:60]), flush=True)
                break
            if waf_this:
                waf_hits += 1
            outcomes.append(0 if code_dead else 1)
            del outcomes[:-SESSION_WINDOW]
            # 限流自适应降速：撞一次放慢一档，连续成功再逐步回调（不换会话提速重冲）
            prev_rate = rate_sleep
            rate_sleep = next_interval(rate_sleep, waf_this)
            if abs(rate_sleep - prev_rate) >= 1e-6:
                print("[xq_tl] 降速档位 %.0f → %.0f 秒/只（每只额外等待）"
                      % (prev_rate, rate_sleep), flush=True)
            total_new += new
            # 逐码落游标：被强杀（含关机）时下一轮从此码之后继续，尾部不再饿死
            if cursor_on and code:
                _save_cursor(code, scope, src_key, queue_len, rnd,
                             args.quota, i + 1)
            # 会话健康度判定：连续全灭 或 窗口坏率过高 → 停轮（换时段再跑，不硬刚）。
            # （比例阈值 SUSTAINED_ABORT_PCT 对「慢速滴漏」无效：分母含成功数，
            #   2026-11-13 实测要累积 86 只、约 15 小时才踩到 20%，护栏形同虚设。）
            halt_reason = should_halt(outcomes)
            if halt_reason:
                halted = halt_reason
                print("[xq_tl] 停轮 %s：近 %d 只坏率 %.0f%%（%d/%d 只被拒）"
                      "——冷却自愈无效，游标已落在 %s 之后，换时段/新会话续跑"
                      % (halt_reason, len(outcomes),
                         100.0 * window_bad_ratio(outcomes),
                         len(outcomes) - sum(outcomes), len(outcomes), code),
                      flush=True)
                break
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
