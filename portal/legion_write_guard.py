# -*- coding: utf-8 -*-
"""军团写接口的浏览器上下文守卫（2026-10-30 P0 安全加固）。

背景：`api_legion_ops_v2._authorized_mutation` 此前对"经本门户 nginx 反代 + 本机 IP"
直接放行，导致本机浏览器页面（含跨站 CSRF 表单 POST）可免 token 调用写接口。
本模块提供**纯函数**判定：浏览器发起的写请求必须来自门户白名单 Origin（或同源
Sec-Fetch-Site），否则拒绝；非浏览器客户端（无 Origin/Sec-Fetch-Site/Referer）
仍交由 IP/token 判据处理。

白名单只列本机门户入口（8734 人类入口 + 8080 容器链路）；不接受通配。
"""
from __future__ import annotations

PORTAL_WRITE_ORIGINS = frozenset({
    "http://127.0.0.1:8734",
    "http://localhost:8734",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
})

# 浏览器 Sec-Fetch-Site 中允许写请求的上下文（同源/直接导航）
_ALLOWED_SEC_FETCH_SITE = frozenset({"same-origin", "same-site", "none"})


def write_context_allowed(origin: str = "", sec_fetch_site: str = "", referer: str = "") -> bool:
    """判断浏览器写请求上下文是否可信；非浏览器请求返回 True。

    规则：
    1. 有 Origin：必须在门户白名单内（防跨站 CSRF 表单/fetch）；
    2. 无 Origin 但有 Sec-Fetch-Site：必须是 same-origin/same-site/none；
    3. 无 Origin 有 Referer：Referer 必须落在门户白名单 origin 下；
    4. 三者皆无（curl/服务端脚本/部分同源 GET 场景）：返回 True，交由
       `authz_ip_allowed` / `_token_ok` 继续判定。
    """
    origin = (origin or "").strip().rstrip("/")
    if origin:
        return origin in PORTAL_WRITE_ORIGINS
    sec = (sec_fetch_site or "").strip().lower()
    if sec:
        return sec in _ALLOWED_SEC_FETCH_SITE
    ref = (referer or "").strip()
    if ref:
        return any(ref == o or ref.startswith(o + "/") for o in PORTAL_WRITE_ORIGINS)
    return True
