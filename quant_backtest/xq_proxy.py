# -*- coding: utf-8 -*-
"""雪球出口代理池（P0-3，RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E）。

设计裁剪（对照 MediaCrawler proxy/ 模块）：
- 供应商 = 静态出口列表（config/xq_proxies.json），商业隧道代理可后续
  以同接口接入（隧道本身自带轮换，静态池足够当前规模）；
- 空配置/文件缺失/格式坏 → 降级直连（与未引入本模块行为一致，零风险）；
- 熔断联动：xueqiu_common.ip_block_trip() 钩子 notify_blocked()——被拉黑
  出口视为脏，自动轮换到下一健康出口；本轮仍按 P0-1 停轮，下轮生效
  （批任务进程分钟级结束，无需进程内热切换）；
- sync 层 pysnowball 零侵入：apply_proxy_env() 设 HTTP(S)_PROXY 环境
  变量，requests trust_env 自动走代理（crawl 层走 Session.proxies 注入）。

本模块不 import 项目内其他模块（供 common/crawl/sync 单向引用，无环）。
配置绝不打印 url（可能含代理认证），只打印 name。
"""
import json
import os
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config", "xq_proxies.json")

# 出口被拉黑后的进程内冷却（秒）：轮换时跳过冷却中的出口，
# 全部冷却中 → 返回 None（直连降级，绝不硬选脏出口）
EXIT_COOLDOWN_SEC = 30 * 60

# ── 出口-指纹粘性（P2-7，RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md
#    §E P2-7「同一出口 IP 固定 UA/请求头组合」）──────────────────
# 真实浏览器 UA 小变体池：同一出口按 name 确定性绑定其一（crc32
# 稳定哈希——内建 hash() 有进程级随机化，跨进程不稳定，禁用），
# 跨进程/重启后同出口仍得同 UA，构成稳定的「IP+UA」组合画像；
# 换出口即换 UA（配合轮换语义，指纹随之切换）。
# sync 层边界：pysnowball 0.1.8 的 UA 硬编码在各 fetch 函数体局部
# HEADERS（'Xueqiu iPhone …' App 端 UA，utls.py 源码核验），运行时
# 不可注入——本粘性仅覆盖 crawl 层直爬 Session（浏览器端通路），
# 两端 UA 不同域，风控画像独立，互不干扰。
UA_VARIANTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36 Edg/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0",
)
DEFAULT_UA = UA_VARIANTS[0]     # 与 xueqiu_common.UA 同串（guba 判例）


@dataclass
class ProxyEntry:
    """单个静态出口。url 形如 http://[user:pass@]host:port。

    ua 为出口绑定指纹（P2-7）：空串=加载时按 name 确定性分配；
    配置显式给 ua 字段则优先（人工指定精确版本时用）。
    """
    name: str
    url: str
    ua: str = ""
    fails: int = 0
    cooldown_until: float = 0.0    # time.time() 纪（进程内即可）

    def healthy(self) -> bool:
        return time.time() >= self.cooldown_until


class ProxyPool:
    """静态出口池：顺序轮换 + 失败冷却 + 空池降级直连。"""

    def __init__(self, entries: list[ProxyEntry] | None = None) -> None:
        self.entries: list[ProxyEntry] = list(entries or [])
        self.idx = 0               # 当前出口下标（entries 为空时无意义）

    def enabled(self) -> bool:
        return bool(self.entries)

    def current(self) -> ProxyEntry | None:
        """当前出口（空池 → None=直连；当前出口冷却中 → 自动轮换）。"""
        if not self.entries:
            return None
        cur = self.entries[self.idx % len(self.entries)]
        if not cur.healthy():
            return self.rotate("cooldown-skip")
        return cur

    def rotate(self, reason: str = "") -> ProxyEntry | None:
        """切到下一个健康出口；全冷却/空池 → None（直连降级）。"""
        if not self.entries:
            return None
        n = len(self.entries)
        for step in range(1, n + 1):
            cand = self.entries[(self.idx + step) % n]
            if cand.healthy():
                self.idx = (self.idx + step) % n
                print("[proxy] 出口轮换（%s）→ %s" % (reason, cand.name))
                return cand
        print("[proxy] 全部 %d 出口冷却中（%s）→ 直连降级" % (n, reason))
        return None

    def mark_fail(self, entry: ProxyEntry | None = None,
                  cooldown: int = EXIT_COOLDOWN_SEC) -> None:
        """标记出口失败进冷却（403 拉黑等脏出口场景）。"""
        e = entry or self.current()
        if e is None:
            return
        e.fails += 1
        e.cooldown_until = time.time() + cooldown

    def proxies(self) -> dict | None:
        """requests Session.proxies 形态；直连返回 None（不注入）。"""
        e = self.current()
        return {"http": e.url, "https": e.url} if e else None


def ua_for(entry: ProxyEntry | None) -> str:
    """出口-指纹粘性（P2-7）：出口绑定专属 UA。

    规则：entry.ua 非空（配置显式指定）优先 → crc32(name) 确定性
    选 UA_VARIANTS 之一（同出口跨进程恒同 UA）→ 直连（None）用
    DEFAULT_UA。绝不 raise。
    """
    try:
        if entry is None:
            return DEFAULT_UA
        if entry.ua:
            return entry.ua
        import zlib
        return UA_VARIANTS[zlib.crc32(entry.name.encode("utf-8"))
                           % len(UA_VARIANTS)]
    except Exception:
        return DEFAULT_UA


def load_pool(path: str | None = None) -> ProxyPool:
    """从 JSON 配置加载静态出口池。

    配置形态：[{"name": "home-1", "url": "http://user:pass@ip:port",
    "ua": "可选：该出口专属 UA（缺省按 name 确定性分配）"}, …]
    文件缺失/JSON 坏/条目缺 url → 静默降级空池（直连），绝不 raise。
    """
    p = path or CONFIG_PATH
    entries: list[ProxyEntry] = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, list):
                for it in data:
                    if isinstance(it, dict) and it.get("name") \
                            and it.get("url"):
                        entries.append(ProxyEntry(
                            name=str(it["name"]), url=str(it["url"]),
                            ua=str(it.get("ua") or "")))
        except Exception:
            entries = []
    if entries:
        print("[proxy] 出口池已启用：%d 个静态出口（%s）"
              % (len(entries), ",".join(e.name for e in entries)))
    return ProxyPool(entries)


_POOL: ProxyPool | None = None


# ── 动态代理池（快代理隧道 API，2026-10-17 用户供给）──────────────
# 静态池（config/xq_proxies.json）为空时启用：从 API 按需拉取出口，
# 单 IP 寿命 1-5 分钟，策略=「用到被封/到期再换下一个」：
#   - 403 拉黑 → notify_blocked → rotate 立即拉新（不等 TTL）；
#   - TTL 到期 → current() 下次取用时被动换新；
#   - 拉取失败 → 60s 限频冷却后重试，期间直连降级（绝不刷爆配额）。
DYNAMIC_CONFIG_PATH = os.path.join(HERE, "config", "xq_proxy_api.json")
DYNAMIC_TTL_SEC = 240          # 1-5 分钟寿命取保守主动换值
FETCH_COOLDOWN_SEC = 60        # API 拉取失败后的限频冷却
VERIFY_TIMEOUT = 8             # 新出口验活超时（秒）
VERIFY_URL = "https://xueqiu.com/robots.txt"   # 轻量端点（雪球自判可达性）


def load_dynamic_config(path: str | None = None) -> dict:
    """读动态池配置；缺失/坏文件返回 {}（调用方降级直连）。"""
    p = path or DYNAMIC_CONFIG_PATH
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def parse_proxy_line(line: str) -> str:
    """ip:port[:user:pass] → requests 代理 url；非法返回 ""。

    认证段允许含冒号外的任意字符；user:pass 中冒号只按第一个拆
    （快代理格式固定四段）。绝不打印原文（含密码）。
    """
    s = str(line or "").strip()
    if not s:
        return ""
    parts = s.split(":")
    if len(parts) == 2:                     # ip:port（免认证）
        ip, port = parts
    elif len(parts) >= 4:                   # ip:port:user:pass
        ip, port, user, pwd = parts[0], parts[1], parts[2], ":".join(parts[3:])
        if not user or not re_match_ip_port(ip, port):
            return ""
        return "http://%s:%s@%s:%s" % (user, pwd, ip, port)
    else:
        return ""
    if not re_match_ip_port(ip, port):
        return ""
    return "http://%s:%s" % (ip, port)


def re_match_ip_port(ip: str, port: str) -> bool:
    import re as _re
    if not (_re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip or "")
            and _re.match(r"^\d{1,5}$", port or "")):
        return False
    return all(int(o) <= 255 for o in ip.split(".")) and int(port) <= 65535


class DynamicPool:
    """快代理动态出口池：与 ProxyPool 同 duck-type 接口。

    - current()：无出口/TTL 过期 → 拉新（验活通过才启用，最多试 2 个）；
    - rotate()：强制拉新（403 熔断联动入口）；
    - mark_fail()：仅记 fails 计数（动态 IP 寿命极短，冷却无意义）；
    - 拉取失败/全坏 → current() 返回 None 直连降级 + FETCH_COOLDOWN
      限频（防刷爆 API 配额）。
    纪律：打印只打 ip:port（entry.name），绝不打印带密码 url；
    拉取 API 本身用 trust_env=False 独立会话（防环境变量代理回环）。
    """

    def __init__(self, api_url: str, ttl_sec: int = DYNAMIC_TTL_SEC,
                 fetch_timeout: int = 15) -> None:
        self.api_url = api_url
        self.ttl_sec = max(30, int(ttl_sec))
        self.fetch_timeout = max(5, int(fetch_timeout))
        self._entry: ProxyEntry | None = None
        self._expires: float = 0.0
        self._fetch_cooldown_until: float = 0.0
        self.rotations = 0

    def enabled(self) -> bool:
        return bool(self.api_url)

    def _fetch_raw(self) -> str:
        """调供应商 API 拉一个出口原始串；失败/限频返回 ""。"""
        if time.time() < self._fetch_cooldown_until:
            return ""
        try:
            import requests as _rq
            sess = _rq.Session()
            sess.trust_env = False          # 绕开 HTTP(S)_PROXY 环境变量
            r = sess.get(self.api_url, timeout=self.fetch_timeout)
            return r.text.strip() if r.status_code == 200 else ""
        except Exception as e:
            print("[proxy] 动态拉取失败: %s" % str(e)[:80])
            return ""

    def _fetch_new(self, reason: str) -> ProxyEntry | None:
        """拉新出口 + 雪球验活（最多 2 个候选），全坏/失败返回 None。"""
        for _ in range(2):
            raw = self._fetch_raw()
            if not raw:
                self._fetch_cooldown_until = (
                    time.time() + FETCH_COOLDOWN_SEC)
                return None
            url = parse_proxy_line(raw.splitlines()[0] if raw else "")
            if not url:
                continue                    # 格式坏：再试一个候选
            ip_port = url.split("@")[-1]
            entry = ProxyEntry(name="kdl-%s" % ip_port, url=url)
            if self._verify(entry):
                self._entry = entry
                self._expires = time.time() + self.ttl_sec
                self.rotations += 1
                print("[proxy] 动态出口就绪（%s）→ %s" % (reason, entry.name))
                return entry
        self._fetch_cooldown_until = time.time() + FETCH_COOLDOWN_SEC
        print("[proxy] 动态出口连续验活失败 → 直连降级 %ds" % FETCH_COOLDOWN_SEC)
        return None

    def _verify(self, entry: ProxyEntry) -> bool:
        """轻量验活：经代理 GET 雪球轻端点，网络通且非 blacklisted。"""
        try:
            import requests as _rq
            sess = _rq.Session()
            sess.trust_env = False
            r = sess.get(VERIFY_URL, timeout=VERIFY_TIMEOUT,
                         proxies={"http": entry.url, "https": entry.url},
                         headers={"User-Agent": ua_for(entry)})
            body = str(getattr(r, "text", "") or "")[:200].lower()
            if r.status_code == 403 and "blacklisted" in body:
                return False                # 出口 IP 也被雪球拉黑
            return True                     # 200/挑战页/其他状态=网络通
        except Exception:
            return False                    # 代理连不通/超时

    def current(self) -> ProxyEntry | None:
        """当前出口；无/TTL 过期 → 被动拉新；不可得 → None 直连。"""
        if self._entry is not None and time.time() < self._expires:
            return self._entry
        return self._fetch_new("ttl-expired" if self._entry else "cold")

    def rotate(self, reason: str = "") -> ProxyEntry | None:
        """强制拉新出口（notify_blocked 熔断联动）。"""
        return self._fetch_new(reason or "rotate")

    def mark_fail(self, entry: ProxyEntry | None = None,
                  cooldown: int = 0) -> None:
        """记失败计数（动态 IP 寿命短，不做长冷却——rotate 换新即可）。"""
        e = entry or self._entry
        if e is not None:
            e.fails += 1

    def proxies(self) -> dict | None:
        """requests Session.proxies 形态；直连返回 None（不注入）。"""
        e = self.current()
        return {"http": e.url, "https": e.url} if e else None


def get_pool() -> ProxyPool | DynamicPool:
    """进程内单例：静态池优先（有手填出口用静态）→ 动态 API → 空池直连。"""
    global _POOL
    if _POOL is None:
        static = load_pool()
        if static.enabled():
            _POOL = static
            return _POOL
        cfg = load_dynamic_config()
        api = str(cfg.get("api_url") or "")
        if api:
            _POOL = DynamicPool(
                api, ttl_sec=int(cfg.get("ttl_sec") or DYNAMIC_TTL_SEC),
                fetch_timeout=int(cfg.get("fetch_timeout") or 15))
            print("[proxy] 动态出口池已启用（快代理 API，TTL %ds）"
                  % _POOL.ttl_sec)
        else:
            _POOL = static                  # 空静态池=直连（原行为）
    return _POOL


def apply_proxy_env(pool: ProxyPool | None = None) -> str:
    """sync 层接线：把当前出口写进 HTTP(S)_PROXY 环境变量。

    pysnowball 内部 requests 默认 trust_env=True → 自动走代理，
    无需侵入其源码。返回出口名（未启用/直连返回 ""）。直连时主动
    清除四键，避免继承 shell 里与本池冲突的系统代理。
    """
    p = pool or get_pool()
    e = p.current()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)
    if e is None:
        return ""
    for k in ("HTTP_PROXY", "HTTPS_PROXY"):
        os.environ[k] = e.url
    return e.name


def notify_blocked(cooldown: int = EXIT_COOLDOWN_SEC,
                   pool: ProxyPool | None = None) -> str:
    """熔断钩子（xueqiu_common.ip_block_trip 调用）：当前出口进冷却
    并轮换；sync 层随后以新出口刷新环境变量由下轮进程生效。返回
    新出口名（""=直连降级或未启用）。绝不打印 url。"""
    p = pool or get_pool()
    if not p.enabled():
        return ""
    p.mark_fail(cooldown=cooldown)
    new = p.rotate("ip_blocked")
    if new is not None:
        apply_proxy_env(p)         # 同进程后续请求（若有）即刻走新出口
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(k, None)
    return new.name if new else ""
