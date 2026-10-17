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


def get_pool() -> ProxyPool:
    """进程内单例（首调加载配置，之后缓存——配置热更需重启进程）。"""
    global _POOL
    if _POOL is None:
        _POOL = load_pool()
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
