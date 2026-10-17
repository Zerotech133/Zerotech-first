# -*- coding: utf-8 -*-
"""雪球 WAF 穿越 cookie 工厂（Playwright headless Edge，四级链 L3）。

架构（EVAL_PLAYWRIGHT_XUEQIU_20261017.md §4，spike 实测全通）：
- Playwright 只当「WAF 预热器/cookie 工厂」——headless Edge（系统
  Edge，channel="msedge" 免下载浏览器）开首页过阿里云 WAF JS 挑战，
  导出匿名 xq_a_token（40 位 hex）+ 伴生 cookie；数据面继续走现有
  requests 通路（API JSON 端点未被 WAF 拦），HTML 全程爬取不必要；
- 缓存：成功结果写 config/xq_browser_cookies.json，TTL 内直接复用
  免开浏览器（每轮采集至多一次浏览器启动，约 12s）；
- 降级：任何失败（playwright 未装/浏览器起不来/挑战未过/无 token）
  返回 []——调用方降级直连，行为与未引入本模块完全一致，零风险；
- 出口联动：xq_proxy 当前出口注入 launch(proxy=…)（出口+cookie 同
  源，指纹一致性）；xq_proxy 空池直连。
- 自包含铁律：路径 HERE 相对；仅单向 import xq_proxy（无环）；
  WAF 特征判断局部实现（与 xueqiu_common.looks_waf_challenged
  同语义，避免反向依赖）。

依赖：playwright pip 包（未装 → harvest 返回 []，不 raise）。
判例（2026-10-17 spike）：WAF 挑战页自动 reload，goto 必须
wait_until="commit"（domcontentloaded 会被 reload 循环卡死超时）。

本模块绝不打印 cookie 值（凭证）；日志只打计数与 token 掩码。
"""
import asyncio
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "config", "xq_browser_cookies.json")

CACHE_TTL_SEC = 12 * 3600        # cookie 缓存 TTL（12h）：匿名 token
                                 # 有效期 20-30 天，12h 足够日频轮次
HOME_URL = "https://xueqiu.com/"
CHALLENGE_WAIT_MS = 10_000       # WAF 挑战 JS 执行窗口（spike 实测 10s 过）
GOTO_TIMEOUT_MS = 30_000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")  # 与 common.UA 同串


def _looks_waf(text: str) -> bool:
    """响应体是否 WAF 挑战页（特征判前 3KB，语义同
    xueqiu_common.looks_waf_challenged；局部实现防反向依赖）。"""
    if not text:
        return False
    head = text[:3000]
    return "aliyun_waf" in head or ("renderData" in head and "_waf_" in head)


# ── cookie 缓存（TTL）────────────────────────────────────────
def _load_cache(path: str | None = None) -> list[dict]:
    """读缓存：TTL 内且含 xq_a_token → cookie 列表；过期/坏/无 → []。"""
    p = path or CACHE_PATH
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        ts = float(data.get("harvested_at") or 0)
        cookies = data.get("cookies")
        if time.time() - ts > CACHE_TTL_SEC or not isinstance(cookies, list):
            return []
        if not any(c.get("name") == "xq_a_token" and c.get("value")
                   for c in cookies if isinstance(c, dict)):
            return []
        return [c for c in cookies if isinstance(c, dict)]
    except Exception:
        return []


def _save_cache(cookies: list[dict], path: str | None = None) -> bool:
    """缓存原子写（tmp+replace）。失败返回 False（不影响主流程）。"""
    p = path or CACHE_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"harvested_at": time.time(), "cookies": cookies},
                      f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


# ── 实采（Playwright）────────────────────────────────────────
def _harvest_live(timeout_s: float = GOTO_TIMEOUT_MS / 1000,
                  proxy_url: str = "") -> list[dict]:
    """真实采集：headless Edge 开首页过 WAF → 导出 cookie。

    同步接口（采集代码全同步），内部 asyncio.run。playwright 未装
    → ModuleNotFoundError 上抛（harvest_cookies 统一捕获）。
    """
    async def _run() -> list[dict]:
        from playwright.async_api import async_playwright
        kw: dict = {"channel": "msedge", "headless": True}
        if proxy_url:
            kw["proxy"] = {"server": proxy_url}
        async with async_playwright() as p:
            browser = await p.chromium.launch(**kw)
            try:
                ctx = await browser.new_context(
                    user_agent=UA, locale="zh-CN",
                    viewport={"width": 1366, "height": 768})
                page = await ctx.new_page()
                # 判例：挑战页自动 reload → commit 级等待，不卡 reload 循环
                try:
                    await page.goto(HOME_URL, wait_until="commit",
                                    timeout=int(timeout_s * 1000))
                except Exception:
                    pass                  # commit 已提交；reload 由窗口兜
                await page.wait_for_timeout(CHALLENGE_WAIT_MS)
                return await ctx.cookies()
            finally:
                await browser.close()

    return asyncio.run(_run())


def harvest_cookies(force: bool = False, path: str | None = None) -> list[dict]:
    """四级链 L3 入口：缓存优先 → 实采 → xq_a_token 校验 → 落缓存。

    返回 playwright cookie 列表（[{name,value,domain,…},…]）；
    任何失败返回 []（调用方降级直连，绝不 raise）。force=True
    跳过缓存强刷（运维手动用）。
    """
    if not force:
        cached = _load_cache(path)
        if cached:
            return cached
    # 出口联动（xq_proxy 单向引用；任何异常直连）
    proxy_url = ""
    try:
        from xq_proxy import get_pool
        e = get_pool().current()
        proxy_url = e.url if e else ""
    except Exception:
        proxy_url = ""
    try:
        cookies = _harvest_live(proxy_url=proxy_url)
    except Exception as e:
        print("[xq_browser] harvest 失败（降级直连）：%s" % str(e)[:80])
        return []
    if not any(isinstance(c, dict) and c.get("name") == "xq_a_token"
               and c.get("value") for c in cookies):
        print("[xq_browser] WAF 挑战未过（无 xq_a_token）→ 降级直连")
        return []
    tok = next(c["value"] for c in cookies if c["name"] == "xq_a_token")
    print("[xq_browser] WAF 预热成功：%d cookie（xq_a_token=%s…）"
          % (len(cookies), str(tok)[:6]))
    _save_cache(cookies, path)
    return cookies
