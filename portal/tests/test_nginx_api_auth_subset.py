# -*- coding: utf-8 -*-
"""H4（K-ORG-20261031-PORTALAUTH，审计 v2）：/api/ 鉴权子集静态回归。

审计实测：未登录 GET 8734 `/api/platform/approvals`=200（25KB）、`/api/org/tasks`=200。
本单对高敏子集启用 auth_request（有效 zb_session → 200，否则 401 JSON），
通用 /api/ 保持公开基线（白名单专项前不整体加锁，避免锁死 8091 全站）。

覆盖：
- 子集 location 均带 `auth_request /_auth_guard` + `error_page 401 = @api_unauthorized` + 反代 8091；
- `@api_unauthorized` 返回 401 JSON（不 302 登录页，避免前端 fetch 拿到 HTML）；
- 通用 `/api/` 未整体加锁（公开基线保留，未覆盖项显式登记在注释里）；
- 公开/认证必需端点不受影响（/api/auth/*、/api/health 回落公开块）；
- 既有页面保护块不回归（/devops、/ide、/approvals 等仍有 auth_request）；
- nginx -t 若可用则真解析（不可用则 skip 并记录）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[1] / "nginx.conf"
PROTECTED_API_PREFIXES = ("/api/platform/", "/api/org/", "/api/analysis/",
                          "/api/os/", "/api/jev/")


def _text() -> str:
    return CONF.read_text(encoding="utf-8")


def _api_subset_block(prefix: str) -> str:
    m = re.search(r"location \^~ " + re.escape(prefix) + r" \{(.*?)\n    \}",
                  _text(), re.S)
    assert m, "缺少鉴权子集 location: %s" % prefix
    return m.group(1)


def test_no_bom():
    raw = CONF.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "nginx.conf 不得带 UTF-8 BOM（nginx -t 会失败）"


def test_api_unauthorized_is_401_json_not_redirect():
    s = _text()
    m = re.search(r"location @api_unauthorized \{(.*?)\n    \}", s, re.S)
    assert m, "缺少 @api_unauthorized named location"
    block = m.group(1)
    assert "return 401" in block
    assert "application/json" in block
    assert "302" not in block and "login" not in block


def test_sensitive_api_subset_protected():
    for prefix in PROTECTED_API_PREFIXES:
        block = _api_subset_block(prefix)
        assert "auth_request /_auth_guard;" in block, prefix
        assert "error_page 401 = @api_unauthorized;" in block, prefix
        assert "proxy_pass http://$h:8091;" in block, prefix


def test_generic_api_not_locked_but_documented():
    s = _text()
    assert "location /api/ {" in s
    block = s.split("location /api/ {", 1)[1].split("}", 1)[0]
    assert "auth_request" not in block, "通用 /api/ 未做完整白名单前不得整体加锁"
    # 未覆盖项必须显式写清楚（不假装全护）
    assert "未覆盖" in s and "H4" in s
    assert "/api/health" in s and "/api/auth/" in s


def test_public_auth_endpoints_not_locked():
    s = _text()
    # 登录必需端点不得出现在鉴权子集前缀里
    for prefix in ("/api/auth/", "/api/bff/"):
        assert ("location ^~ " + prefix) not in s
    # /api/health 无专用 location → 回落公开 /api/
    assert "location = /api/health" not in s


def test_existing_page_protection_not_regressed():
    s = _text()
    for path in ("/devops", "/ide", "/approvals", "/agent_ide_bundle.json"):
        m = re.search(r"location = " + re.escape(path) + r" \{(.*?)\n    \}", s, re.S)
        assert m, path
        assert "auth_request /_auth_guard;" in m.group(1), path


def test_nginx_syntax_if_available():
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("host 无 nginx 二进制（容器 nginx 配置未挂载本 worktree，未改生产）")
    r = subprocess.run([nginx, "-t", "-c", str(CONF)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, (r.stderr or r.stdout)[-2000:]
