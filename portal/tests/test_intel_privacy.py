# -*- coding: utf-8 -*-
"""情报私有化 nginx 静态回归（2026-10-31 拍板 1「全私有」，WO-INTEL-PRIVATE-20261031）。

只做静态配置断言（不依赖生产 nginx/8091）：
- 情报页面（/intel、/intel/stream、xq_intel.html、news_intel.html）未登录 → 302 /login；
- 情报 API（news/intel、xq/intel*、jev/query、jev/review/*、intel/bus、
  intel/evidence-targets）未登录 → 401（不 302，避免前端把登录页 HTML 当 JSON）；
- 认证走现有 /_auth_guard（internal → 8091 /api/auth/guard，透传 Cookie）；
- 公开白名单保留：全局 /api/（含 /api/health）、/login、/s/ 分享短链不加锁；
- 每个受保护路径只声明一次（防复制出未加锁的重复 location 绕过）。
"""
from __future__ import annotations

import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "nginx.conf"

# 情报页面：未登录 302 到 /login
PAGES = (
    ("=", "/intel"),
    ("=", "/intel/stream"),
    ("=", "/xq_intel.html"),
    ("=", "/news_intel.html"),
)
# 情报 API：未登录 401（返回 JSON/错误态，不跳登录页）
APIS = (
    ("=", "/api/news/intel"),
    ("^~", "/api/xq/intel"),
    ("=", "/api/jev/query"),
    ("^~", "/api/jev/review/"),
    ("=", "/api/intel/bus"),
    ("=", "/api/intel/evidence-targets"),
)


def _text() -> str:
    return CONF.read_text(encoding="utf-8")


def _block(kind: str, path: str) -> str:
    """取 location 块体（支持精确 = 与前缀 ^~；块以 4 空格缩进 } 结束）。"""
    pat = (r"location " + ("= " if kind == "=" else r"\^~ ")
           + re.escape(path) + r" \{(.*?)\n    \}")
    m = re.search(pat, _text(), re.S)
    assert m, "nginx 缺少 location 块: %s %s" % (kind, path)
    return m.group(1)


def _decl_count(kind: str, path: str) -> int:
    pat = ("location " + ("= " if kind == "=" else r"\^~ ") + re.escape(path) + " {")
    return len(re.findall(pat, _text()))


def _named_block(name: str) -> str:
    """取 nginx named location 块体（location @name { ... }）。"""
    m = re.search(r"location " + re.escape(name) + r" \{(.*?)\n    \}",
                  _text(), re.S)
    assert m, "nginx 缺少 named location: %s" % name
    return m.group(1)


def test_auth_guard_defined_and_internal():
    s = _text()
    assert "location = /_auth_guard {" in s
    block = _block("=", "/_auth_guard")
    assert "internal;" in block
    assert "proxy_pass http://$h:8091/api/auth/guard" in block
    assert "proxy_set_header Cookie $http_cookie" in block


def test_pages_require_auth_and_redirect():
    for kind, path in PAGES:
        body = _block(kind, path)
        assert "auth_request /_auth_guard;" in body, path
        assert "error_page 401 = @login_required;" in body, path
        assert _decl_count(kind, path) == 1, "重复 location 可能被绕过: %s" % path


def test_intel_static_pages_served_after_auth():
    """静态情报页不得只剩 auth 而丢掉 try_files（避免 302 后 404）。"""
    for path in ("/xq_intel.html", "/news_intel.html"):
        body = _block("=", path)
        assert "try_files $uri =404;" in body, path


def test_apis_require_auth_with_401():
    for kind, path in APIS:
        body = _block(kind, path)
        assert "auth_request /_auth_guard;" in body, path
        assert "error_page 401" not in body, \
            "%s 应直接 401（不 302 登录页，前端需 JSON 错误）" % path
        assert "proxy_pass http://$h:8091;" in body, path
        assert _decl_count(kind, path) == 1, "重复 location 可能被绕过: %s" % path


def test_login_redirect_target_defined():
    body = _named_block("@login_required")
    assert "absolute_redirect off;" in body
    assert "return 302 /login?next=$uri;" in body


def test_public_whitelist_preserved():
    """公开基线不得被整体加锁：全局 /api/（/api/health 走此）、/login、/s/ 分享短链。"""
    s = _text()
    assert "location /api/ {" in s
    api_block = s.split("location /api/ {", 1)[1].split("}", 1)[0]
    assert "auth_request" not in api_block, "全局 /api/ 加锁会连 /api/health 一起锁死"
    # /login 是单行 location（同块内含 }），不能按多行块解析：取声明行+后 3 行
    lines = s.splitlines()
    idx = next(i for i, ln in enumerate(lines)
               if ln.strip().startswith("location = /login "))
    assert "auth_request" not in "\n".join(lines[idx:idx + 3]), "/login 必须公开"
    share = _block("^~", "/s/")
    assert "auth_request" not in share, "/s/ 分享短链属公开白名单"
    # /health 若在配置里显式存在，也不得加锁（本轮未新增，保留防回归断言）
    m = re.search(r"location = /health \{(.*?)\n    \}", s, re.S)
    if m:
        assert "auth_request" not in m.group(1)


def test_conf_braces_balanced():
    """静态括号配平（不替代 nginx -t，但防手改配置结构断裂）。"""
    s = _text()
    assert s.count("{") == s.count("}"), "nginx.conf 花括号不配平"
