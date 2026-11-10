# -*- coding: utf-8 -*-
"""雪球数据采集公共库（xueqiu-collector 公共层）。

设计依据：《雪球数据采集器系统设计》§2.2（xueqiu-collector-design-2026-10-16.md）。
职责：token 注入 / 限速+抖动 / 429 指数退避 / manifest 断点 / 原子写 /
quotec 健康探测 / 符号归一 / SQLite 幂等 upsert / 缠论池取码。

pysnowball 0.1.8 实测口径（本机已装，utls.py 源码核验）：
  · utls.fetch/fetch_without_token 返回 json.loads 的完整 JSON dict
    （结构 {"error_code":0, "data":{...}}），并非 DataFrame；
  · set_token 写进程环境变量 XUEQIUTOKEN，token.get_token 无则 raise；
  · quotec 批量免 token；其余 fetch 系接口需 Cookie=token。

合规四红线（设计 §7.10，代码级承诺）：
  只读不写（GET only）、限速 ≥2s+抖动、不分发数据、不登录他人隐私数据。

自包含铁律（换机失效判例，guba_crawl.py 同款）：所有路径 HERE 相对，
禁止 import kg_paths / 引用 DATA_ROOT / E 盘绝对路径。

被 xueqiu_sync.py（L1/L2）与 xueqiu_crawl.py（L3）复用。
"""
import atexit
import logging
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                     # 平铺同目录互 import（判例）

OUT_DIR = os.path.join(HERE, "data", "xueqiu")          # 雪球仓根
MANIFEST = os.path.join(OUT_DIR, "_manifest.json")       # 断点+健康度
INFO_DB = os.path.join(OUT_DIR, "xueqiu_info.db")        # 舆情三表 SQLite
ENV_LOCAL = os.path.join(HERE, ".env.local")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")   # guba_crawl 同款
REFERER = "https://xueqiu.com/"   # 社区判例（Agent-Reach v1.3.1）：全请求携带

# 限速与硬上限（设计 §4.4 参数表）
THROTTLE_BASE = 2.0        # 秒；未登录 15 次/分钟 ≈ 4s/次，2s+抖动留余量
THROTTLE_JITTER = 2.0      # 0-2s 抖动防节拍特征（P0-2：实际间隔 2-4s，
                           # 对齐 MediaCrawler 调研 uniform(1.5,4) 建议上界，
                           # 进一步推迟触发 IP 风控的临界点）
MAX_RETRY = 4              # 429/超时重试上限
MAX_SYMBOLS = 200          # 逐码批日硬上限
MAX_PAGES = 10             # 直爬翻页硬上限（guba 判例）
QUOTEC_BATCH = 50          # quotec 单请求符号上限（URL 长度约束）
BACKOFF_CAP = 300          # 指数退避封顶（秒）

# ── IP 风控熔断（P0-1，RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E）──
IP_BLOCK_COOLDOWN_MIN = 30   # 熔断冷却期（分钟）：雪球拉黑为临时性质，
                             # 冷却期内绝不再发请求（继续撞只会延长拉黑期）

# SQLite 舆情三表建表 DDL（设计 §3.1：统一 fetched_at + 检索索引）
SQLITE_TABLES = {
    "xq_status": {
        "pk": ["status_id"],
        "ddl": ("CREATE TABLE IF NOT EXISTS xq_status ("
                "status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                "comment_count INTEGER, retweet_count INTEGER, "
                "like_count INTEGER, reward_count INTEGER, source TEXT, "
                "category INTEGER, fetched_at TEXT)"),
        "idx": [("idx_xq_status_cat", "CREATE INDEX IF NOT EXISTS "
                 "idx_xq_status_cat ON xq_status(category, created_at)"),
                ("idx_xq_status_target", "CREATE INDEX IF NOT EXISTS "
                 "idx_xq_status_target ON xq_status(target)")],
    },
    "xq_hot_post": {
        "pk": ["status_id"],
        "ddl": ("CREATE TABLE IF NOT EXISTS xq_hot_post ("
                "status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                "comment_count INTEGER, retweet_count INTEGER, "
                "like_count INTEGER, reward_count INTEGER, source TEXT, "
                "category INTEGER, hot_rank INTEGER, hot_fallback INTEGER, "
                "fetched_at TEXT)"),
        "idx": [("idx_xq_hot_post_rank", "CREATE INDEX IF NOT EXISTS "
                 "idx_xq_hot_post_rank ON xq_hot_post(hot_rank, created_at)")],
    },
    "xq_comment": {
        "pk": ["comment_id"],
        "ddl": ("CREATE TABLE IF NOT EXISTS xq_comment ("
                "comment_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                "comment_count INTEGER, retweet_count INTEGER, "
                "like_count INTEGER, reward_count INTEGER, source TEXT, "
                "category INTEGER, symbol TEXT, fetched_at TEXT)"),
        # (symbol, created_at) 复合索引：nine_dim 按时间窗聚合查询路径
        "idx": [("idx_xq_comment_sym", "CREATE INDEX IF NOT EXISTS "
                 "idx_xq_comment_sym ON xq_comment(symbol, created_at)")],
    },
}


# ── token（三级 cookie 链，Agent-Reach v1.3.1 判例）──────────
def load_token_from_browser() -> str:
    """从本机浏览器自动提取 xq_a_token（rookiepy，DPAPI 解密）。

    三级链第 2 级：用户在 Edge/Chrome 登录 xueqiu.com 一次即可，之后
    每次运行自动取最新登录态 cookie（token 有效期 20-30 天，过期重登
    浏览器即可续命，无需再手取）。支持 Edge→Chrome→Firefox 依序探测。
    绝不打印 cookie 值；任何失败静默返回 ""（不影响后续降级）。
    """
    try:
        import rookiepy
    except ImportError:
        return ""
    browsers = (rookiepy.edge, rookiepy.chrome, rookiepy.firefox)
    for getter in browsers:
        try:
            cookies = getter([".xueqiu.com"])
        except Exception:
            continue
        if not isinstance(cookies, list):
            continue
        tok = next((c.get("value", "") for c in cookies
                    if c.get("name") == "xq_a_token" and c.get("value")), "")
        if tok:
            return str(tok)
    return ""


def load_token(env_path: str | None = None) -> str:
    """三级链装配 token → 写 os.environ['XUEQIUTOKEN']。

    第 1 级：.env.local 的 XQ_A_TOKEN（手填，容忍等号两侧空格/BOM）；
    第 2 级：本机浏览器登录态自动提取（load_token_from_browser）；
    第 3 级：无凭证返回 ""（调用方降级 skip，不 raise——降级≠失败，
    设计 §4.1 缺 token 降级矩阵）。

    pysnowball 约定从进程环境变量 XUEQIUTOKEN 取 token（ball.set_token
    同款机制），凡取到即写环境变量，L1/L3 共用同一凭证源。
    """
    path = env_path or ENV_LOCAL
    token = ""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                # utf-8-sig 兼容带/不带 BOM 两种保存形态（QA Round-1
                # Minor-2：防手填 token 时编辑器加 BOM 静默失效）
                for ln in f:
                    s = ln.strip().lstrip("\ufeff")
                    if s.replace(" ", "").startswith("XQ_A_TOKEN="):
                        # 容忍 "XQ_A_TOKEN = 值"（等号两侧空格的手填形态）
                        token = s.split("=", 1)[1].strip()
                        break
        except Exception:
            token = ""
    if not token:
        token = load_token_from_browser()
    if token:
        os.environ["XUEQIUTOKEN"] = token
    return token


def _merge_cached_browser_cookies(sess: "requests.Session") -> bool:
    """把 xq_browser 匿名预热缓存 cookie 注入 session（WAF 伴生 cookie）。

    2026-10-26 EXEC-R29 实证：社区 API 只带 xq_a_token 会 302 到 www 的
    interfaceacting.js WAF 挑战页；补上缓存里的 acw_tc/ssxmod_itna 等伴生
    cookie + Referer 后，`/v4/statuses/public_timeline_by_category.json`
    直连 200 JSON（9 items）。此前 token 分支提前 return 漏了伴生 cookie。
    缓存 TTL 过期也读原始 cache 兜底（伴生 cookie 实测跨 5 天仍可用），
    全部失败静默 False，不阻塞采集。返回是否至少注入一条。
    """
    cookies: list = []
    # 2026-11-08：登录存档（真实登录态全量 cookie，含伴生）优先——
    # 匿名预热缓存 2026-11-03 起过期且匿名链被 WAF 打死（P2-8 判例）；
    # 登录存档由 xq_cookie_harvest 收割件维护，缺失/解析失败静默降级。
    try:
        import json as _json
        _login_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "config", "xq_login_cookies_u.json")
        with open(_login_path, encoding="utf-8-sig") as _f:
            _data = _json.load(_f)
        _login_cks = [c for c in _data.get("cookies", []) if isinstance(c, dict)]
        if _login_cks:
            cookies = _login_cks
            print("[xq_common] 登录存档伴生 cookie 已采用（%d 条，新鲜登录态）"
                  % len(cookies))
    except Exception:
        cookies = []
    if not cookies:
        try:
            from xq_browser import _load_cache
            cookies = _load_cache() or []
        except Exception:
            cookies = []
    if not cookies:
        try:
            import json as _json
            from xq_browser import CACHE_PATH as _CACHE
            with open(_CACHE, encoding="utf-8-sig") as _f:
                _data = _json.load(_f)
            cookies = [c for c in _data.get("cookies", []) if isinstance(c, dict)]
            if cookies:
                print("[xq_common] 预热缓存已过期，仍合并伴生 cookie（%d 条）"
                      % len(cookies))
        except Exception:
            cookies = []
    merged = 0
    for c in cookies:
        name, value = c.get("name"), c.get("value")
        if not name or value is None:
            continue
        try:
            sess.cookies.set(str(name), str(value),
                             domain=c.get("domain") or ".xueqiu.com")
            merged += 1
        except Exception:
            continue
    if merged:
        print("[xq_common] WAF 伴生 cookie 已合并：%d 条" % merged)
    return merged > 0


def ensure_session_cookies(sess: "requests.Session", token: str = "") -> str:
    """把四级链 cookie 注入直爬 Session（L3 专用），返回生效 token。

    1) 显式传入的 token（或 load_token 已取到的）→ 注入 xq_a_token；
    2) 无 token 时再从浏览器提取全部 .xueqiu.com cookie 注入
       （xq_a_token/xqat/xq_r_token 等伴生 cookie 一并带上更稳）；
    3) 仍无 → Playwright 匿名预热（xq_browser.harvest_cookies，
       2026-10-17 spike 实测：headless Edge 过首页 WAF 拿匿名
       xq_a_token——复活被 WAF 废掉的匿名通路，缓存 TTL 内免开
       浏览器；失败返回 [] 零风险降级）；
    4) 最后 → 首页 warm 兜底（现只能拿 acw_tc，留作公开端点降级）。
    同时给 Session 统一补 Referer 头（社区判例：全请求携带）。
    """
    sess.headers.setdefault("Referer", REFERER)
    tok = token or ""
    if tok:
        sess.cookies.set("xq_a_token", tok, domain=".xueqiu.com")
        # EXEC-R29：token 存在也必须合并 WAF 伴生 cookie，否则社区 API 302。
        _merge_cached_browser_cookies(sess)
        return tok
    # EXEC-R29：无 token 也先试预热缓存（匿名 xq_a_token + 伴生 cookie）
    if _merge_cached_browser_cookies(sess):
        tok = sess.cookies.get("xq_a_token", "")
        if tok:
            os.environ["XUEQIUTOKEN"] = tok
            return tok
    # 第 2 级：浏览器全量 cookie
    try:
        import rookiepy
        for getter in (rookiepy.edge, rookiepy.chrome, rookiepy.firefox):
            try:
                cookies = getter([".xueqiu.com"])
            except Exception:
                continue
            got = False
            for c in cookies or []:
                name, value = c.get("name"), c.get("value")
                if name and value is not None:
                    sess.cookies.set(name, str(value), domain=".xueqiu.com")
                    if name == "xq_a_token":
                        tok, got = str(value), True
            if got:
                os.environ["XUEQIUTOKEN"] = tok
                return tok
    except ImportError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
    # 第 3 级：Playwright 匿名预热（四级链，2026-10-17 立项落地）
    try:
        from xq_browser import harvest_cookies
        for c in harvest_cookies():
            name, value = c.get("name"), c.get("value")
            if name and value is not None:
                sess.cookies.set(name, str(value),
                                 domain=c.get("domain") or ".xueqiu.com")
        tok = sess.cookies.get("xq_a_token", "")
        if tok:
            os.environ["XUEQIUTOKEN"] = tok
            return tok
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
    # 第 4 级：首页 warm（历史行为保留；WAF 化后仅得 acw_tc，
    # 保留为公开端点最后兜底）
    warm_token = warm_session(sess)
    return warm_token or tok


# ── 符号归一 ─────────────────────────────────────────────────
def norm_symbol(tc: str) -> str:
    """000338.SZ / sz000338 / 000338 → SZ000338（雪球大小写敏感格式）。

    仅认 A 股 6 位码（SZ/SH/BJ）；非法输入返回 ""。裸 6 位码按段推断
    交易所（6/9/5 头=沪，0/2/3 头=深）；北交所 BJ 显式后缀可转，
    裸码不推断 BJ（池模式只收沪深，guba pool_codes 同口径）。
    """
    c = str(tc or "").strip()
    if not c:
        return ""
    up = c.upper()
    m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", up)
    if m:                                   # 000338.SZ → SZ000338
        return m.group(2) + m.group(1)
    m = re.match(r"^(SZ|SH|BJ)(\d{6})$", up)
    if m:                                   # sz000338 → SZ000338
        return m.group(1) + m.group(2)
    if up.isdigit() and len(up) == 6:       # 裸 6 位码：按段推断交易所
        if up.startswith("6"):
            return "SH" + up                # 沪主板
        # "9" 头不再归 SH（QA Round-1 BUG-3）：900xxx=B 股、920xxx=北交所
        # 新段，均非本采集器目标；B 股代码 "900" 显式 .SH 后缀仍可过
        # 上面的正则分支；裸 9 头码一律拒绝，防误归沪产生畸形文件
        if up.startswith(("0", "2", "3")):
            return "SZ" + up                # 深主板/债/创业板
        if up.startswith("5"):
            return "SH" + up                # 沪基金/ETF 段（norm 宽松保留）
    return ""


def to_ts_code(xq_symbol: str) -> str:
    """SZ000338 → 000338.SZ（落库统一 ts_code 口径，与全仓对齐）。"""
    m = re.match(r"^(SZ|SH)(\d{6})$", str(xq_symbol or "").upper())
    return m.group(2) + "." + m.group(1) if m else ""


def ms_to_dt(ms) -> str:
    """雪球 ms epoch → 'YYYY-MM-DD HH:MM:SS'（本地时区，设计 §8 共享知识）。"""
    try:
        v = int(float(ms))
        if v <= 0:
            return ""
        return datetime.fromtimestamp(v / 1000.0).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def ms_to_ymd(ms) -> str:
    """雪球 ms epoch → 'YYYYMMDD'（快照 trade_date 口径）。"""
    s = ms_to_dt(ms)
    return s[:10].replace("-", "") if s else ""


# ── 限速 / 退避 ──────────────────────────────────────────────
class XqIpBlocked(RuntimeError):
    """IP 被雪球风控拉黑（403 Seek IP Blacklisted）。

    不可重试——冷却期内继续请求只会延长拉黑期（P0-1 判例）。区别于
    xq_get 普通失败返回 None：本异常必须向上传播，由调用方熔断停轮。
    """


def looks_ip_blocked(exc: BaseException | None) -> bool:
    """异常文本是否疑似 IP 拉黑（pysnowball 侧不暴露 status，文本判定）。

    quotec/kline 等走 pysnowball，403 时 resp.json() 解析失败或抛
    含 "Seek IP Blacklisted" 的异常；特征词 blacklisted 唯一性强。
    """
    return "blacklisted" in str(exc or "").lower()


def ip_block_trip(mf: "XqManifest",
                  minutes: int = IP_BLOCK_COOLDOWN_MIN) -> str:
    """触发熔断：写 health.ip_blocked_until = now+minutes，返回到期串。

    同时记 ip_blocked_at 便于审计；调用方随后应 save() 落盘并停轮。
    P0-3 联动：出口代理池启用时，把当前出口标记进冷却并轮换到下一
    健康出口（本轮仍停，下轮新出口生效）——钩子失败绝不影响熔断本身。
    """
    until = (datetime.now() + timedelta(minutes=max(1, minutes))
             ).isoformat(timespec="seconds")
    mf.mark_health("ip_blocked_until", until)
    mf.mark_health("ip_blocked_at",
                   datetime.now().isoformat(timespec="seconds"))
    try:
        from xq_proxy import notify_blocked
        notify_blocked()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
    return until


def ip_blocked_active(mf: "XqManifest") -> str:
    """熔断冷却期是否生效。生效返回描述串（含到期与剩余分钟），否则 ""。

    读 health.ip_blocked_until；过期/缺失/格式坏均返回 ""（熔断自愈）。
    """
    until = str((mf.health() or {}).get("ip_blocked_until") or "")
    if not until:
        return ""
    try:
        t = datetime.fromisoformat(until)
    except ValueError:
        return ""
    now = datetime.now()
    if now >= t:
        return ""
    remain = int((t - now).total_seconds() // 60) + 1
    return "until %s（剩约 %d 分钟）" % (until, remain)


def jitter_sleep(base: float = THROTTLE_BASE,
                 jitter: float = THROTTLE_JITTER) -> None:
    """sleep(base + random()*jitter)。所有请求间统一走这里（≥2s 强制）。

    P0-2 约定：凡新增请求路径（页循环/批循环/批间衔接）必须经本函数
    节奏化——雪球 IP 风控按请求频率画像，裸奔连发会提前触发拉黑；
    配合 P0-1 熔断（403 停轮）构成「节奏预防 + 拉黑止损」双防线。
    """
    time.sleep(max(0.0, base) + random.random() * max(0.0, jitter))


def backoff_sleep(attempt: int, cap: int = BACKOFF_CAP) -> None:
    """429/超时指数退避：sleep(min(2^attempt * 10, cap) + random()*2)。"""
    delay = min((2 ** max(0, attempt)) * 10, cap)
    time.sleep(delay + random.random() * 2)


# ── 浏览器回退（2026-10-26 EXEC-R31）──────────────────────────────────────
# WAF 302 挑战页落在 requests 上：实测同一 cookie 集，requests 302 / 浏览器
# context.request 200（TLS/HTTP2/JS 指纹差异）。xq_get 在 3xx/非 JSON 时
# 懒启动一次 Playwright(msedge→chromium) 并预热首页，用 context.request
# 重取同一 URL；失败静默返回 None（不阻断调用方，不影响原降级链）。
_BROWSER_STATE: dict = {"pw": None, "browser": None, "ctx": None, "failed": False}


def _close_browser() -> None:
    st = _BROWSER_STATE
    for key in ("browser", "pw"):
        obj = st.get(key)
        try:
            if obj is not None:
                if key == "browser":
                    obj.close()
                else:
                    obj.stop()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
        st[key] = None
    st["ctx"] = None


def _browser_context():
    """懒启动浏览器并预热 xueqiu.com；失败标记 failed，后续直接跳过。"""
    st = _BROWSER_STATE
    if st["ctx"] is not None:
        return st["ctx"]
    if st["failed"]:
        return None
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except Exception:  # noqa: BLE001
            browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto("https://xueqiu.com/", timeout=45000,
                  wait_until="domcontentloaded")
        st.update({"pw": pw, "browser": browser, "ctx": ctx})
        atexit.register(_close_browser)
        return ctx
    except Exception:  # noqa: BLE001
        st["failed"] = True
        return None


def browser_get_json(url: str, params: dict | None = None,
                     timeout: float = 30.0) -> dict | None:
    """用浏览器上下文 GET 同一 URL 并解析 JSON（WAF 回退）。"""
    ctx = _browser_context()
    if ctx is None:
        return None
    try:
        resp = ctx.request.get(
            url, params=params, timeout=int(timeout * 1000),
            headers={"Referer": REFERER,
                     "Accept": "application/json, text/plain, */*"})
    except Exception:  # noqa: BLE001
        return None
    if getattr(resp, "status", None) != 200:
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("error_code") not in (None, 0):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


# ── 直爬 HTTP ────────────────────────────────────────────────
def xq_get(sess: requests.Session, url: str, params: dict | None = None,
           max_retry: int = MAX_RETRY, timeout: float = 15.0) -> dict | None:
    """直爬统一 GET：403→raise XqIpBlocked（熔断，绝不重试）；
    429/5xx/超时→backoff_sleep 重试；error_code!=0→None。

    返回 data 子 dict（如 {"items":…}）；接口顶层即为业务负载时
    （statuses 族）返回完整 dict。重试到顶仍失败返回 None（调用方记
    failed 继续，绝不 raise 阻断整批——设计 §4.3）。
    唯一例外：403 IP 拉黑（P0-1）必须 raise——继续请求只会延长拉黑期，
    由调用方捕获后熔断停轮（ip_block_trip + 停止本轮全部请求）。
    """
    for attempt in range(max_retry):
        try:
            resp = sess.get(url, params=params, timeout=timeout,
                            headers={"Referer": REFERER})
        except Exception:                   # 超时/连接错
            backoff_sleep(attempt)
            continue
        if resp.status_code == 403:
            # P0-1 熔断：IP 拉黑（Seek IP Blacklisted）。body 读取容错
            # （测试伪响应可能无 text 属性）
            body = str(getattr(resp, "text", "") or "")[:150].strip()
            raise XqIpBlocked("403 %s" % body)
        if resp.status_code == 429:
            backoff_sleep(attempt)
            continue
        if resp.status_code >= 500:
            backoff_sleep(attempt)
            continue
        if resp.status_code in (301, 302, 303, 307, 308):
            # WAF/路由跳转挑战页：同一 URL 用浏览器上下文重取一次。
            return browser_get_json(url, params, timeout)
        if resp.status_code != 200:
            return None                     # 4xx 非 429：不重试（cookie 缺失等）
        try:
            j = resp.json()
        except Exception:
            return browser_get_json(url, params, timeout)  # WAF 挑战页回退
        if not isinstance(j, dict):
            return None
        if j.get("error_code") not in (None, 0):
            return None                     # 雪球业务错（如 400016 token 失效）
        data = j.get("data")
        return data if isinstance(data, dict) else j
    return None


def looks_waf_challenged(text: str) -> bool:
    """响应体是否阿里云 WAF JS 挑战页（P2-8 探测实证 2026-10-17）。

    雪球全站 HTML 页面（首页/个股页）已 WAF 化：裸 requests 只返回
    200 + JS 挑战页（aliyun_waf_* meta + renderData textarea），无
    SSR 数据可解析——HTML 降级通路在 requests 语境不可行（唯一可行
    形态=Playwright 无头过挑战，L 级暂缓）。API JSON 端点未被 WAF
    拦截（同 Session 实测 /v4/statuses 返回真 JSON）。特征判前 3KB
    足够（挑战页特征全在头部）。
    """
    if not text:
        return False
    head = text[:3000]
    return "aliyun_waf" in head or ("renderData" in head and "_waf_" in head)


def warm_session(sess: requests.Session) -> str:
    """GET xueqiu.com 首页种 cookie（xq_a_token 40 位 hex），返回 token 或 ""。

    未登录首页响应 Set-Cookie: xq_a_token=<40位hex>；这是直爬层 L3 的
    匿名会话凭证（不依赖 .env.local 的登录 token）。失败返回 ""，调用方
    仍可尝试裸请求（无 cookie 时 v4 接口 400，由 xq_get 返回 None 兜底）。

    P2-8 探测实证（2026-10-17）：首页已 WAF 化——200 但为 JS 挑战页，
    acw_tc 会种上而 xq_a_token 不可得（三级链第 3 级失效）。此处检测
    挑战页并打印告警（一次性情报，不 raise——调用方继续后续降级链）。
    """
    try:
        resp = sess.get("https://xueqiu.com/", timeout=15)
        tok = sess.cookies.get("xq_a_token", "")
        if tok:
            return str(tok)
        if looks_waf_challenged(str(getattr(resp, "text", "") or "")):
            print("[warm_session] 首页已被 WAF 挑战页拦截（无匿名 "
                  "xq_a_token 可种）：L3 匿名通路失效，需登录 cookie"
                  "（浏览器登录 xueqiu.com）")
        return ""
    except Exception:
        return ""


def quotec_probe(symbol: str = "SH000001") -> dict:
    """免 token 健康探测（pysnowball.quotec，fetch_without_token 通道）。

    返回 {ok, latency_ms, ts, err}；ok=True 时网络与雪球服务均可达，
    与 token 批成败组合可区分「网络坏」vs「token 坏」（设计 §4.2）。
    探测结果由调用方写 manifest['health']。
    """
    t0 = time.time()
    out = {"ok": False, "latency_ms": 0,
           "ts": datetime.now().isoformat(timespec="seconds"), "err": ""}
    try:
        import pysnowball as ball           # 延迟 import（未装则报 err）
        j = ball.quotec(symbol)             # 返回完整 JSON dict
        items = ((j.get("data") or {}).get("items")
                 if isinstance(j, dict) else None)
        if items:
            out["ok"] = True
        else:
            out["err"] = "empty_response"
    except ModuleNotFoundError:
        out["err"] = "pysnowball_not_installed"
    except Exception as e:
        out["err"] = str(e)[:120]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def token_probe(symbol: str = "SH600519") -> dict:
    """登录态探测（P1-5，MediaCrawler pong 等价物）：kline count=2 单请求。

    返回 {ok, valid, latency_ms, ts, err}：
    - ok=True：请求往返正常（HTTP 200 + JSON dict），valid 判定可信；
    - valid=True：error_code==0，token 有效；
    - valid=False：ok=True 时 token 明确失效（400016 等业务错码）；
      ok=False 时网络/服务故障，token 状态未知（valid 仅在 ok=True
      有意义，调用方不得据 ok=False 跳批——沿用各批如实失败路径）。

    设计动机：token 失效常态是 HTTP 200 + error_code=400016，此前
    要等各 token 批逐码失败才暴露（N 码 = N 次浪费请求 + 节奏时间）；
    预检一次即可跳批。与 quotec_probe 组成两级探测矩阵（§4.2）：
    quotec=网络可达性（免 token），token_probe=登录态（需 token）。
    探测结果由调用方写 manifest['health']。
    """
    t0 = time.time()
    out = {"ok": False, "valid": False, "latency_ms": 0,
           "ts": datetime.now().isoformat(timespec="seconds"), "err": ""}
    try:
        import pysnowball as ball           # 延迟 import（未装则报 err）
        j = ball.kline(symbol, period="day", count=2)
        out["latency_ms"] = int((time.time() - t0) * 1000)
        if isinstance(j, dict):
            out["ok"] = True
            err = j.get("error_code")
            if err in (None, 0):
                out["valid"] = True
            else:
                out["err"] = "error_code=%s %s" % (
                    err, str(j.get("error_description"))[:40])
        else:
            out["err"] = "bad_response"
    except ModuleNotFoundError:
        out["err"] = "pysnowball_not_installed"
    except Exception as e:
        out["err"] = str(e)[:120]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


# ── manifest 断点 ────────────────────────────────────────────
class XqManifest:
    """断点管理（tushare_sync._manifest.json 同构 + health 扩展）。

    结构 {表名: {done, rows, file, last_run, status, note}, health: {...}}；
    status 枚举：ok / partial / empty / failed / skip_no_token / degraded。
    """

    def __init__(self, path: str | None = None) -> None:
        # 动态取模块常量（默认=MANIFEST；运行时可被测试重定向——
        # 默认参数在 def 时绑定会导致 patch 失效）
        self.path = path or MANIFEST
        self._data: dict = {"health": {}}

    def load(self) -> dict:
        """读盘合并（文件缺失/损坏返回初始骨架，不 raise）。"""
        base = {"health": {}}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    base.update(loaded)
                    base.setdefault("health", {})
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
        self._data = base
        return self._data

    def get(self, tbl: str) -> dict:
        """取单表断点（无记录返回 {}）。"""
        v = self._data.get(tbl)
        return v if isinstance(v, dict) else {}

    def mark(self, tbl: str, *, done: bool | None = None,
             rows: int | None = None, file: str | None = None,
             status: str = "ok", note: str = "") -> None:
        """增量更新单表断点（未给字段保留原值；last_run 恒刷新）。"""
        ent = self._data.get(tbl)
        if not isinstance(ent, dict):
            ent = {}
        if done is not None:
            ent["done"] = done
        if rows is not None:
            ent["rows"] = rows
        if file is not None:
            ent["file"] = file
        ent["status"] = status
        ent["note"] = note
        ent["last_run"] = datetime.now().isoformat(timespec="seconds")
        self._data[tbl] = ent

    def mark_health(self, key: str, val: object) -> None:
        """写 health.<key>（quotec_probe / token_present / 批次计数）。"""
        self._data.setdefault("health", {})[key] = val

    def health(self) -> dict:
        """读 health 块（无返回 {}）。"""
        h = self._data.get("health")
        return h if isinstance(h, dict) else {}

    def save(self) -> bool:
        """tmp+os.replace 原子写。失败返回 False（不 raise）。"""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False


# ── 原子写 / SQLite 幂等 ─────────────────────────────────────
def atomic_parquet(df: pd.DataFrame, path: str) -> None:
    """tmp+os.replace 原子写 parquet（含 .tmp 清理）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):             # 异常时清理半成品
            try:
                os.remove(tmp)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)


def upsert_sqlite(db_path: str, table: str, rows: list[dict],
                  pk: list[str]) -> int:
    """SQLite INSERT OR REPLACE（幂等），返回实写行数。

    自动建表（按首行键集合）+ 建索引（SQLITE_TABLES 已登记的表）。
    空行列表返回 0；后续行缺键补 None（schema 以首行为准）。
    """
    if not rows:
        return 0
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    spec = SQLITE_TABLES.get(table, {})
    cols = list(rows[0].keys())
    n = 0
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        if spec:
            conn.execute(spec["ddl"])
            # 轻量列迁移（2026-10-26 EXEC-R31）：代码先行加列（如
            # hot_fallback）而老库没有时，按首行键集合幂等 ALTER 补列；
            # 不加索引、不动已有数据，失败忽略（后续 INSERT 会明确报错）。
            existing = {row[1] for row in
                        conn.execute('PRAGMA table_info("%s")' % table)}
            for c in cols:
                if c in existing:
                    continue
                col_type = "INTEGER" if c in (
                    "hot_rank", "hot_fallback", "view_count", "comment_count",
                    "retweet_count", "like_count", "reward_count") else "TEXT"
                try:
                    conn.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s'
                                 % (table, c, col_type))
                except sqlite3.OperationalError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "xueqiu_common.py", exc)
            for _name, idx_sql in spec.get("idx", []):
                conn.execute(idx_sql)
        else:
            col_defs = ", ".join(
                ['"%s" TEXT PRIMARY KEY' % c if c in pk
                 else '"%s" TEXT' % c for c in cols])
            conn.execute('CREATE TABLE IF NOT EXISTS "%s" (%s)'
                         % (table, col_defs))
        for r in rows:
            vals = [r.get(c) for c in cols]
            if all(v in (None, "") for v in vals):
                continue                    # 全空行跳过（脏数据防御）
            ph = ", ".join(["?"] * len(cols))
            quoted = ", ".join('"%s"' % c for c in cols)
            conn.execute('INSERT OR REPLACE INTO "%s" (%s) VALUES (%s)'
                         % (table, quoted, ph), vals)
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


# ── 缠论池取码 ───────────────────────────────────────────────
def pool_codes(pool_days: int = 30, limit: int = MAX_SYMBOLS) -> list[str]:
    """缠论池活跃股→雪球符号（复用 guba_crawl.pool_codes 逻辑）。

    输出 SZ/SH 前缀雪球符号（norm_symbol 归一）；池文件缺失/为空返回 []。
    """
    path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
    if not os.path.exists(path):
        return []
    cutoff = (datetime.now() - timedelta(days=pool_days)
              ).strftime("%Y%m%d")
    seen: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                c = str(r.get("code") or "")
                m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", c, re.I)
                if not m:
                    continue                # 指数/US/HK 不采（guba 同口径）
                if m.group(2).upper() == "BJ":
                    continue                # 北交所雪球符号段不稳，跳过
                if str(r.get("dt") or "").replace("-", "")[:8] < cutoff:
                    continue
                xq = norm_symbol(m.group(1) + "." + m.group(2))
                if xq:
                    seen.setdefault(m.group(1), xq)   # 首见序=信号日序
    except Exception:
        return []
    return list(seen.values())[:limit]
