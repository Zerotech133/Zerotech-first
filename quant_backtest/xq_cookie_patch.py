# -*- coding: utf-8 -*-
"""pysnowball 完整 cookie 补丁（xq_cookie_patch.py，2026-10-17 判例）。

判例：雪球 API 风控升级，kline 等 token 接口纯 xq_a_token header 只返回
空 items（{"items":[]}）——必须携带完整 cookie 集（WAF 伴生 cookie +
xq_a_token）。requests 直连实验（2026-10-17 19:1x）：纯 token → 200 空
items；完整 cookie → 200 全列 K 线。直爬层（ensure_session_cookies+
xq_get）本就带全量 cookie 不受影响；pysnowball 通道（utls.fetch 的
Cookie 只含 token）挂——token 批 5 处 + token_probe 全部中招。

修法：monkey-patch requests.get——对 *.xueqiu.com 请求把 Cookie 头替换
为完整集：xq_browser 缓存/TTL 内匿名预热 cookie + 进程 XUEQIUTOKEN 覆盖
xq_a_token（token 池换号时数据面 token 与 cookie 保持一致）。
幂等；任何异常静默降级原样（绝不 raise——采集不因补丁失败而中断）。
"""

import json
import os
import time
import logging

_HERE = os.path.dirname(os.path.abspath(__file__))
_BROWSER_CACHE = os.path.join(_HERE, "config", "xq_browser_cookies.json")
_CACHE_TTL = 11 * 3600  # 略小于 xq_browser 写侧 12h

_state = {"patched": False, "cookie_str": "", "cookie_ts": 0.0}


def _login_archive_cookie_str() -> str:
    """登录存档全量 cookie 串（config/xq_login_cookies_u.json）。

    2026-11-08 起：收割件把真实登录全量 cookie 写入该存档（含 WAF 伴生
    acw_tc/ssxmod_itna 等），新鲜度远超匿名预热缓存（后者 2026-11-03 起
    过期且匿名链被 WAF 打死——P2-8 判例）。优先用登录存档；缺失/解析
    失败静默降级回原预热缓存链路。绝不打印值。
    """
    try:
        path = os.path.join(_HERE, "config", "xq_login_cookies_u.json")
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        pairs = [
            (c["name"], c["value"])
            for c in data.get("cookies", [])
            if isinstance(c, dict) and c.get("name") and c.get("value")
        ]
        if pairs:
            return "; ".join("%s=%s" % p for p in pairs)
    except Exception:
        pass
    return ""


def _browser_cookie_str() -> str:
    """完整 cookie 基座：登录存档优先 → 匿名预热缓存（TTL 内读缓存；
    缺失/过期 harvest 一次）。"""
    if _state["cookie_str"] and time.time() - _state["cookie_ts"] < _CACHE_TTL:
        return _state["cookie_str"]
    # 2026-11-08：登录存档（真实登录态全量 cookie）优先
    s = _login_archive_cookie_str()
    if s:
        _state["cookie_str"] = s
        _state["cookie_ts"] = time.time()
        return _state["cookie_str"]
    pairs: list = []
    try:
        with open(_BROWSER_CACHE, encoding="utf-8-sig") as f:
            data = json.load(f)
        if time.time() - float(data.get("harvested_at") or 0) <= _CACHE_TTL:
            pairs = [
                (c["name"], c["value"])
                for c in data.get("cookies", [])
                if isinstance(c, dict) and c.get("name") and c.get("value")
            ]
    except Exception:
        pairs = []
    if not pairs:
        try:
            from xq_browser import harvest_cookies

            pairs = [
                (c["name"], c["value"])
                for c in harvest_cookies(force=True)
                if isinstance(c, dict) and c.get("name") and c.get("value")
            ]
        except Exception:
            pairs = []
    if not pairs:
        return ""
    _state["cookie_str"] = "; ".join("%s=%s" % p for p in pairs)
    _state["cookie_ts"] = time.time()
    return _state["cookie_str"]


def _full_cookie(token: str) -> str:
    """完整 cookie 串：浏览器集 + xq_a_token=进程 token（覆盖同名）。"""
    base = _browser_cookie_str()
    if not base:
        return ("xq_a_token=%s" % token) if token else ""
    out = [p for p in base.split("; ") if not p.startswith("xq_a_token=")]
    if token:
        out.append("xq_a_token=%s" % token)
    return "; ".join(out)


def patch_pysnowball_cookies() -> bool:
    """patch requests.get：雪球域名请求 Cookie 头替换为完整集。
    幂等；任何异常静默返回 False（调用方降级，不影响采集）。"""
    if _state["patched"]:
        return True
    try:
        import requests

        _orig = requests.get

        def _get(url, *a, **kw):
            try:
                if isinstance(url, str) and "xueqiu.com" in url:
                    headers = dict(kw.get("headers") or {})
                    full = _full_cookie(os.environ.get("XUEQIUTOKEN", ""))
                    if full:
                        headers["Cookie"] = full
                        kw["headers"] = headers
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xq_cookie_patch.py", exc)
            return _orig(url, *a, **kw)

        requests.get = _get
        _state["patched"] = True
        print("[cookie-patch] pysnowball 完整 cookie 补丁已挂载")
        return True
    except Exception as e:
        print("[cookie-patch] patch 失败（降级原样）：%s" % str(e)[:80])
        return False
