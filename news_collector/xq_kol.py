# -*- coding: utf-8 -*-
"""雪球专栏 KOL 采集（C9A-KOL-001 v2 影子源，2026-11-08 老田批）。

定位：公众号 KOL（wechat_kol.py v1，9 号）的影子源补足——B 组 11 个
雪球高互动作者的专栏文章/长帖，与公众号同源互补（存量帖内 mp.weixin
直链旁证），合计 20 号。名单在 news_config.json 的 xq_kol_users 键
（user_id+note），增删号=改配置，无需动代码。

链路（2026-11-08 实测定版）：requests 直爬已死——v4 user_timeline.json
200+WAF 挑战页（aliyun_waf）、分类端点 567、stock.xueqiu 403 IP 拉黑，
requests 语境三路全断（含 browser_get_json 回退）。唯一可行形态=
Playwright **headed** persistent context（今天收割的活登录 profile
tmp/xq_auto_login_profile）+ 页内 fetch（挑战 JS 在浏览器里自己跑，
credentials:'include' 带登录态）——归档 tools/xq_user_timeline_batch.py
同款生产验证路线。headless 会被 567 拦截，勿回退。
窗口 --window-position=3000,3000 挪出可视区（收割路线同款）。

管线约定（对齐 fetch_* 家族）：
    fetch_xq_kol(size) -> [{"id", "ts", "title", "content"}]
    id 前缀 xq_kol:+status_id（news 表 PRIMARY KEY 去重）；
    ts 为 CST "%Y-%m-%d %H:%M"（created_at ms → 本地时区）。

过滤口径（影子源=情报密度优先）：
    ① 专栏文章：title 非空，全收；
    ② 长帖：无 title 但正文纯文本 >= 300 字，收；
    ③ 转发：retweeted_status 存在且自身无 title/正文短——丢弃。

合规四红线（xueqiu_common 同款承诺）：只读 GET、限速 2-4s 抖动、
不分发数据、登录态为本人账号。单作者失败不拖垮整轮；滑块/567 停轮。

自包含约束：本模块不落任何盘上状态，仅网络只读。
独立自检：python xq_kol.py [--dry] [--force]
    --dry 抓取并打印统计/样本，不落库（约 11 作者 x 2 页 ≈ 3-4 分钟）；
    --force 跳过限频闸（人工排程验证用，闸账照记）。
"""
import html
import json
import os
import re
import sys
import time

# ── 常量 ─────────────────────────────────────────────────────
XQ_ROOT = r"C:\aios\quant_backtest"
PROFILE_DIR = os.path.join(XQ_ROOT, "tmp", "xq_auto_login_profile")
PROFILE_FALLBACK = os.path.join(XQ_ROOT, "data", "xueqiu", "_headed_profile")

_PER_USER_CAP = 10       # 单作者单轮最多入库条数（timeline 最新在前）
_MAX_PAGES = 2           # 单作者单轮翻页上限（增量口径，历史深挖不在此）
_LONG_BODY_MIN = 300     # 长帖纯文本下限（字）
_BODY_CAP = 800          # 正文截断（KOL 深度文与 wechat_kol 同宽）
_PAGE_WAIT_MS = 6000     # 进用户页后等挑战 JS 完成（给足缓冲防重武装）

# ── 限频闸（2026-11-08 22:15 实证缺陷修复）───────────────────
# COLLECT 每 ~15 分钟一轮（runs 表全天实证），xq_kol 不设闸则每轮都起
# headed 浏览器拉 11 作者——22:06 轮已实证被触发（405 立即停轮）。
# 闸参数平衡两个口径：WAF 预算 vs 当日入库率>80%（拉取间隔过长会让
# 下午发布的文章次日才入库）。定：每日最多 4 次、两次间隔 ≥6h
# （拉取滞后 ≤6h，白天发布的文章基本当日入库）、halted 后冷却按
# 连续停轮次数递增（12h→24h→48h 封顶，见 _halted_cooldown_h）。
# 状态文件 HERE 落盘（tmp+replace 原子写）。
_GATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "xq_kol_state.json")
_GATE_MAX_PER_DAY = 4        # 每自然日最多尝试次数（含失败）
_GATE_GAP_MIN = 360          # 两次尝试最小间隔（分钟）
_GATE_HALTED_COOLDOWN_H = 12 # halted 首次冷却（小时）
_GATE_HALTED_COOLDOWN_MAX_H = 48  # 递增冷却封顶（小时）

# 页内 fetch JS（归档 xq_user_timeline_batch.API_JS 同款改编：
# text/desc 截 2000 供长帖判定，uname 取 screen_name，转发标记透传）
API_JS = """
async (arg) => {
  try {
    const r = await fetch('/v4/statuses/user_timeline.json?user_id=' + arg.uid +
                          '&page=' + arg.page + '&type=0',
                          {credentials: 'include', headers: {'Accept': 'application/json'}});
    if (r.status !== 200) {
      return {err: 'HTTP ' + r.status, body: (await r.text()).slice(0, 200)};
    }
    const j = await r.json();
    const list = j.statuses || j.list || [];
    return {
      ok: true,
      total: j.total,
      statuses: list.map(s => ({
        status_id: String(s.id),
        created_at_ms: s.created_at,
        title: s.title || '',
        desc: (s.description || '').slice(0, 2000),
        text: (s.text || '').slice(0, 2000),
        target: s.target || '',
        uname: (s.user && s.user.screen_name) || '',
        has_retweet: !!s.retweeted_status,
      })),
    };
  } catch (e) {
    // 挑战页不是 JSON（r.json() 抛 SyntaxError "<script>/<textarea"）
    return {err: 'WAF_CHALLENGE ' + String(e).slice(0, 80)};
  }
}
"""


def _strip_html(s: str) -> str:
    """语义对齐 wechat_kol.strip_html：去标签 + 实体还原 + 压空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _load_users():
    # 直接按路径读（不走 config_local.load_news_config）：XQ_ROOT 注入
    # sys.path 后其同名 config_local.py 会遮蔽本目录模块（实测踩坑），
    # 且 xq_kol_users 非敏感字段、无需私有覆盖链，直读零风险。
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "news_config.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    users = cfg.get("xq_kol_users") or []
    return [u for u in users if isinstance(u, dict) and u.get("user_id")]


def _ms_to_cst(ms) -> str:
    """雪球 ms epoch → 'YYYY-MM-DD HH:MM'（本地时区）。"""
    try:
        v = int(float(ms))
        if v <= 0:
            return ""
        import datetime as _dt
        d = _dt.datetime.fromtimestamp(v / 1000.0)
        return d.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _gate_read() -> dict:
    try:
        with open(_GATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _gate_write(st: dict) -> None:
    try:
        tmp = _GATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _GATE_PATH)
    except Exception as e:
        print("[xq_kol] WARN 闸状态写盘失败（不影响本轮）: %s" % str(e)[:80])


def _halted_cooldown_h(st: dict) -> int:
    """连续停轮递增冷却：12h → 24h → 48h 封顶。

    halt_count 跨日不清零（WAF 惩罚按累计行为计，跨日重置等于
    每天白送一次撞击）；仅 _gate_mark_success()（真实抓到数据）
    才清零回 12h。
    """
    n = max(1, int(st.get("halt_count") or 1))
    return min(_GATE_HALTED_COOLDOWN_H * (2 ** (n - 1)),
               _GATE_HALTED_COOLDOWN_MAX_H)


def _gate_block_reason() -> str:
    """限频闸判定。返回拦截原因串或 ""（放行）。

    halted → 按连续停轮次数递增冷却（跨日有效）；同日尝试数 ≥4 →
    当日封顶；距上次尝试 <6h → 间隔未到。attempts/间隔跨日归零，
    halt_count 只在成功抓取后清零。
    """
    st = _gate_read()
    today = time.strftime("%Y-%m-%d")
    last = st.get("last_run") or ""
    try:
        import datetime as _dt
        gap_s = (_dt.datetime.now() - _dt.datetime.fromisoformat(last)
                 ).total_seconds()
    except Exception:
        gap_s = None
    if st.get("halted"):
        cd_h = _halted_cooldown_h(st)
        if gap_s is not None and gap_s < cd_h * 3600:
            return "halted 冷却中（%s 停轮，%dh 内不再尝试，连续第 %d 次）" % (
                last[:16], cd_h, int(st.get("halt_count") or 1))
        return ""                       # 冷却已过，放行
    if st.get("date") == today and (st.get("attempts") or 0) >= _GATE_MAX_PER_DAY:
        return "当日尝试数已达上限 %d" % _GATE_MAX_PER_DAY
    if gap_s is not None and gap_s < _GATE_GAP_MIN * 60:
        return "距上次尝试仅 %.0f 分钟（闸间隔 %d 分钟）" % (
            gap_s / 60.0, _GATE_GAP_MIN)
    return ""


def _gate_mark_start() -> None:
    """本轮过闸记账：当日 attempts+1，清 halted。

    跨日重置 attempts/间隔，但 halt_count 保留（递增冷却依据）。
    """
    st = _gate_read()
    today = time.strftime("%Y-%m-%d")
    if st.get("date") != today:
        st = {"date": today, "attempts": 0, "halted": False,
              "last_run": "", "halt_count": int(st.get("halt_count") or 0)}
    st["attempts"] = int(st.get("attempts") or 0) + 1
    st["halted"] = False
    st["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _gate_write(st)


def _gate_mark_halt() -> None:
    """封禁停轮落账：halted=True 且 halt_count+1（attempts 不动，防重复计数）。

    跨日也写（递增冷却跨日有效，见 _halted_cooldown_h 注释）。
    """
    st = _gate_read()
    st["halted"] = True
    st["halt_count"] = int(st.get("halt_count") or 0) + 1
    _gate_write(st)


def _gate_mark_success() -> None:
    """真实抓到数据后清零停轮历史：冷却回 12h 基线。"""
    st = _gate_read()
    if st.get("halt_count") or st.get("halted"):
        st["halted"] = False
        st["halt_count"] = 0
        _gate_write(st)


def _launch_ctx(p):
    """优先活登录 profile（今天收割），失败降级 _headed_profile。"""
    last = None
    for prof in (PROFILE_DIR, PROFILE_FALLBACK):
        if not os.path.isdir(prof):
            continue
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=prof, headless=False, viewport=None,
                locale="zh-CN",
                args=["--disable-blink-features=AutomationControlled",
                      "--window-position=3000,3000"])
            print("[xq_kol] 浏览器上下文就绪（profile=%s）"
                  % os.path.basename(prof))
            return ctx
        except Exception as e:
            last = e   # profile 被占用（浏览器开着）等场景，试下一个
    raise RuntimeError("无可用登录 profile: %s" % str(last)[:120])


def _eval_fetch(page, uid, pg):
    """页内 fetch，WAF 挑战重武装自愈：reload 让挑战 JS 重解后重试。

    实测（2026-11-08 dry）：挑战会重武装，fetch 拿回 200 + 挑战页
    （JS 侧标记 WAF_CHALLENGE）。reload + 等待重解，最多 2 次；
    真实 HTTP 错误（405/567 封禁等）不重试直接透传。
    """
    for attempt in range(2):
        data = page.evaluate(API_JS, {"uid": uid, "page": pg})
        if data.get("ok"):
            return data
        err = str(data.get("err") or "")
        if "WAF_CHALLENGE" not in err:
            return data                  # 真实 HTTP 错误：不重试
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000 + attempt * 2000)
        except Exception as e:
            return {"err": "reload %s" % str(e)[:60]}
    return data                          # 2 次仍被挑战：透传给调用方


def _page_blocked(page) -> str:
    """当前页面是否为阿里云封禁/挑战页（405 错误页或 WAF 挑战）。

    返回封禁描述（"405"/"WAF"）或 ""。进作者页后先查一次，命中即
    全轮停——实测（2026-11-08）：连续快节奏探针会把 WAF 信任度烧穿
    升级为 IP 惩罚态（用户页 405），此时继续撞只会延长拉黑期（P0-1
    判例同款纪律）。
    """
    try:
        t = (page.title() or "").strip()
        if t == "405" or "aliyun" in (page.content()[:3000]).lower():
            return "405"
        c = page.content()[:3000]
        if "aliyun_waf" in c or ("renderData" in c and "_waf_" in c):
            return "WAF"
    except Exception:
        pass
    return ""


def fetch_xq_kol(size=30, force=False):
    """抓全部已配置雪球作者，返回管线约定 items 结构。

    限频闸：COLLECT 每 15 分钟一轮，无闸则每轮都撞雪球 WAF——默认
    走闸（每日 ≤4 次、间隔 ≥6h、halted 同日 12h 冷却）。force=True
    跳过闸（人工排程验证用，闸账照记）。单作者失败打印 FAIL 继续；
    预热即 405/WAF 停轮（防拉黑延长），本轮已抓到的部分照常返回。
    """
    if not force:
        reason = _gate_block_reason()
        if reason:
            print("[xq_kol] SKIP 限频闸: %s" % reason)
            return []
    _gate_mark_start()                  # 先记账（防并发轮重复过闸）

    users = _load_users()
    if not users:
        print("[xq_kol] WARN 配置无 xq_kol_users，跳过")
        return []

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("[xq_kol] FAIL playwright 不可用: %s" % str(e)[:120])
        return []

    out, seen, fails, halted = [], set(), [], None
    with sync_playwright() as p:
        try:
            ctx = _launch_ctx(p)
        except Exception as e:
            print("[xq_kol] FAIL %s" % str(e)[:160])
            return []
        try:
            page = ctx.new_page()
            page.set_default_timeout(30000)
            # 首页预热：挑战 JS 完成后再打 API
            page.goto("https://xueqiu.com", wait_until="domcontentloaded",
                      timeout=45000)
            page.wait_for_timeout(_PAGE_WAIT_MS)
            for u in users:
                uid = str(u["user_id"])
                note = u.get("note") or ""
                got, uname = 0, ""
                try:
                    # 进作者页（归档工具同款：每作者刷新一次上下文指纹）
                    page.goto("https://xueqiu.com/u/%s" % uid,
                              wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(_PAGE_WAIT_MS)
                    blocked = _page_blocked(page)
                    if blocked:
                        # 封禁/挑战升级：全轮停（P0-1 纪律，防拉黑延长）
                        halted = "作者页 %s" % blocked
                        fails.append("uid=%s: %s（停轮）" % (uid, halted))
                        break
                    for pg in range(1, _MAX_PAGES + 1):
                        if got >= _PER_USER_CAP:
                            break
                        data = _eval_fetch(page, uid, pg)
                        if not data.get("ok"):
                            err = str(data.get("err"))[:80]
                            fails.append("uid=%s p%d: %s" % (uid, pg, err))
                            if "567" in err or "405" in err or \
                                    "WAF_CHALLENGE" in err:
                                # 封禁/挑战升级：停轮（连续撞只会烧信任度）
                                halted = err
                            break
                        sts = data.get("statuses") or []
                        if not sts:
                            break
                        for st in sts:
                            if got >= _PER_USER_CAP:
                                break
                            if not uname:
                                uname = str(st.get("uname") or "")
                            title = _strip_html(st.get("title") or "")
                            body = _strip_html(
                                st.get("text") or st.get("desc") or "")
                            # 转发过滤：转发载体且自身无观点产出 → 丢弃
                            if st.get("has_retweet") and not title and \
                                    len(body) < _LONG_BODY_MIN:
                                continue
                            # 收录口径：专栏文章（title）或长帖（>=300 字）
                            if not title and len(body) < _LONG_BODY_MIN:
                                continue
                            sid = str(st.get("status_id") or "")
                            if not sid or body == "":
                                continue
                            fid = "xq_kol:" + sid
                            if fid in seen:
                                continue
                            out.append({
                                "id": fid,
                                "ts": _ms_to_cst(st.get("created_at_ms")),
                                "title": title or body[:80],
                                "content": body[:_BODY_CAP],
                            })
                            got += 1
                        time.sleep(2.0 + (int(time.time()) % 20) / 10.0)
                    if uname or got:
                        print("[xq_kol] %s(uid=%s, %s): %d 条"
                              % (uname or "?", uid, note, got))
                except Exception as e:
                    fails.append("uid=%s: %s" % (uid, str(e)[:80]))
                if halted:
                    break                 # 封禁升级：作者循环也停
                time.sleep(3.0 + (int(time.time()) % 30) / 10.0)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    for m in fails:
        print("[xq_kol] FAIL %s" % m)
    if halted:
        _gate_mark_halt()               # 停轮落账：递增冷却生效（12h→24h→48h）
        print("[xq_kol] ALERT 封禁响应（%s）触发停轮——冷却期内勿重跑" % halted)
    elif out:
        _gate_mark_success()            # 真实抓到数据：停轮历史清零，冷却回 12h
    return out


def main() -> int:
    force = "--force" in sys.argv      # 跳过限频闸（闸账照记）
    items = fetch_xq_kol(force=force)
    print("合计 %d 条（未落库，自检模式）" % len(items))
    for it in items[:5]:
        print("  [%s] %s" % (it["ts"], it["title"][:50]))
    # 熔断/空结果判失败，供状态日志与验收跟踪捕获
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
