# -*- coding: utf-8 -*-
"""安全 urlopen：仅允许 http/https scheme（portal 树副本；含回环禁代理豁免）。

回环豁免（2026-11-06 补）：对 127.0.0.0/8、::1、localhost、0.0.0.0 的请求
**不走系统/环境代理**。原因是 urllib 根本不豁免回环——实测
`urllib.request.proxy_bypass('127.0.0.1')` 返回 False，只要 HTTP_PROXY 存在，
打 127.0.0.1 的请求照样被送去代理。代理一转手，探活的结论就从
「服务活不活」变成了「代理通不通」：
  - 代理挡死  -> 全套本机服务被误判宕机（假警报）；
  - 代理拒转  -> 代理的 502 被当成服务返回的 502（门禁 47609c4f5 就是这么红的）。

用法：
    from safe_urlopen import safe_urlopen
    with safe_urlopen(req, timeout=5) as r: ...

支持 urllib.request.Request 或 URL 字符串；其余参数原样透传。
"""
from __future__ import annotations

import ipaddress
import urllib.request
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

# 回环别名：这些主机名解析结果只可能是本机
_LOOPBACK_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_loopback(host: str | None) -> bool:
    """主机是否指向本机（127.0.0.0/8 / ::1 / localhost / 0.0.0.0）。"""
    if not host:
        return False
    h = host.strip().strip("[]").lower()
    if h in _LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    # 0.0.0.0 作为客户端目标等价于本机（Linux 直连 127.0.0.1，Windows 拒绝），
    # 两种情形都不该交给代理。
    return ip.is_loopback or str(ip) == "0.0.0.0"


def _open_no_proxy(url, *args, **kwargs):
    """回环目标专用：清空 ProxyHandler 后打开。"""
    ctx = kwargs.pop("context", None)
    opener = _NO_PROXY_OPENER
    if ctx is not None:
        # OpenerDirector.open 不收 context，需换 HTTPSHandler 承接
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
    return opener.open(url, *args, **kwargs)


def safe_urlopen(url, *args, **kwargs):
    target = getattr(url, "full_url", None) or str(url)
    parts = urlsplit(target)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError("safe_urlopen 拒绝非 http(s) scheme: %r（url=%r）"
                         % (scheme or "<none>", target[:120]))
    if is_loopback(parts.hostname):
        return _open_no_proxy(url, *args, **kwargs)
    return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310 - 安全包装器自身（已先校验 scheme）
