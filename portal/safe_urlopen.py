# -*- coding: utf-8 -*-
"""安全 urlopen：仅允许 http/https scheme（portal 树副本）。"""
from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


def safe_urlopen(url, *args, **kwargs):
    target = getattr(url, "full_url", None) or str(url)
    scheme = urlsplit(target).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError("safe_urlopen 拒绝非 http(s) scheme: %r（url=%r）"
                         % (scheme or "<none>", target[:120]))
    return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310 - 安全包装器自身
