# -*- coding: utf-8 -*-
"""H4-B（K-ORG-20261101-H4HARDEN，审计 v2 H4）：/api/ 公开白名单 + 默认拒绝静态回归。

首轮 H4（K-ORG-20261031-PORTALAUTH）只保护了高敏子集（platform/org/analysis/os/jev），
通用 `/api/` 仍是"公开基线"（任何未登记前缀匿名可达）。本单把通用公开基线收敛为
**显式白名单**：

- 公开（匿名）前缀：health/auth/bff/m + 匿名页数据族（144→141 个一级名 + intel 3 子路径，
  明细见 nginx.conf H4-B 段注释；本测试对静态页/JS 的 `/api/` 引用做覆盖校验）；
- 受保护：上方 `^~` 高敏子集 + 既有专项 location（/api/news/intel、/api/xq/intel、
  /api/jev/query、/api/jev/review/、/api/intel/bus、/api/intel/evidence-targets）；
- 未登记前缀：`location /api/` **默认拒绝** 401 JSON（不再有公开 passthrough）；
- `/api/health`、`/api/auth/*`、`/api/bff/*`、`/api/m/*` 等 8734 公开页/登录必需端点
  必须在白名单内，不得被锁死；
- nginx -t 若可用（优先 docker nginx:alpine，其次宿主 nginx）则真解析。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[1] / "nginx.conf"
PORTAL_ROOT = CONF.parent

PROTECTED_API_PREFIXES = ("/api/platform/", "/api/org/", "/api/analysis/",
                          "/api/os/", "/api/jev/", "/api/dw/", "/api/hq/",
                          "/api/a2a/", "/api/monitor/")
# 既有专项保护（精确/前缀），H4-B 不得把它们放进公开白名单
SPECIAL_PROTECTED = (
    ("=", "/api/news/intel"),
    ("^~", "/api/xq/intel"),
    ("=", "/api/jev/query"),
    ("^~", "/api/jev/review/"),
    ("=", "/api/intel/bus"),
    ("=", "/api/intel/evidence-targets"),
)
# 公开静态页/JS 里对受保护前缀的引用（这些页面本身要求登录，不参与白名单覆盖）
PROTECTED_EXACT = {"/api/news/intel", "/api/intel/bus", "/api/intel/evidence-targets"}
PROTECTED_PREFIXES_EXTRA = ("/api/xq/intel", "/api/case/")

H4B_START = "    # ===== H4-B（K-ORG-20261101-H4HARDEN"
H4B_END = "    # 未登记前缀默认拒绝（H4-B）"

# 扫描静态页/JS 里的 /api 引用；负向后行断言排除 "/quant-api/api/..."：
# /quant-api 是独立同源反代（→ 8105，自带 JWT 鉴权），不经门户 /api/ 默认拒绝白名单，
# 不能把其路径误判为未登记 /api 前缀（WO-ACAD1-SSO-20261104 实锤误报）。
STATIC_API_RE = re.compile(r"(?<![A-Za-z0-9_-])/api/[A-Za-z0-9_\-/.%{}:?=&$]*")


def _text() -> str:
    return CONF.read_text(encoding="utf-8")


def _h4b_block() -> str:
    s = _text()
    assert H4B_START in s and H4B_END in s, "缺少 H4-B 白名单段"
    return s.split(H4B_START, 1)[1].split(H4B_END, 1)[0]


def _api_deny_block() -> str:
    s = _text()
    m = re.search(r"location /api/ \{(.*?)\n    \}", s, re.S)
    assert m, "缺少默认拒绝 location /api/"
    return m.group(1)


def _public_whitelist_alts() -> list:
    alts = []
    for line in _h4b_block().splitlines():
        line = line.strip()
        if not line.startswith("location ~ ^/api/(?:"):
            continue
        body = line.split("^/api/(?:", 1)[1].split(")(?:/|$)", 1)[0]
        alts.extend(part for part in body.split("|") if part)
    assert alts, "H4-B 段未解析到公开白名单 regex"
    return alts


def _public_whitelist_re() -> re.Pattern:
    return re.compile("^/api/(?:" + "|".join(_public_whitelist_alts()) + ")(?:/|$)")


def _is_protected(path: str) -> bool:
    if path in PROTECTED_EXACT:
        return True
    return any(path == p.rstrip("/") or path.startswith(p)
               for p in PROTECTED_API_PREFIXES + PROTECTED_PREFIXES_EXTRA)


def _location_block(kind: str, path: str) -> str:
    pat = r"location " + re.escape(kind) + " " + re.escape(path) + r" \{(.*?)\n    \}"
    m = re.search(pat, _text(), re.S)
    assert m, "缺少 location %s %s" % (kind, path)
    return m.group(1)


def _static_api_paths() -> list:
    paths = []
    files = (sorted((PORTAL_ROOT / "static").rglob("*.js"))
             + sorted((PORTAL_ROOT / "static").rglob("*.html"))
             + sorted(PORTAL_ROOT.glob("*.html")))
    # static/proto 为设计原型/沙盘，非生产公开页；其未实现 API 不应阻塞 H4-B 门。
    files = [p for p in files if "proto" not in p.parts]
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in STATIC_API_RE.finditer(text):
            raw = m.group(0).split("?")[0].split("#")[0]
            raw = raw.rstrip(".,;:)]}\\'\"")
            if not raw.startswith("/api/") or raw in ("/api/",):
                continue
            if any(tok in raw for tok in ("$", "{", "}")):
                continue  # 动态拼接 URL，静态无法判定
            paths.append(raw)
    assert paths, "未从静态页/JS 提取到任何 /api/ 引用"
    return paths


# ── 基础结构 ──────────────────────────────────────────────────────────────
def test_no_bom():
    raw = CONF.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "nginx.conf 不得带 UTF-8 BOM（nginx -t 会失败）"


def test_h4b_default_deny_is_401_json_not_passthrough():
    block = _api_deny_block()
    assert "return 401" in block
    assert "application/json" in block
    assert "api_not_in_whitelist" in block
    assert "proxy_pass" not in block, "通用 /api/ 不得保留公开 passthrough"
    # 旧公开基线注释必须消失（防回退）
    assert "本块保持**公开基线**" not in _text()
    assert "禁止恢复通用公开基线" in _text()


def test_api_unauthorized_is_401_json_not_redirect():
    m = re.search(r"location @api_unauthorized \{(.*?)\n    \}", _text(), re.S)
    assert m, "缺少 @api_unauthorized named location"
    block = m.group(1)
    assert "return 401" in block
    assert "application/json" in block
    assert "302" not in block and "login" not in block


def test_sensitive_api_subset_protected():
    for prefix in PROTECTED_API_PREFIXES:
        block = _location_block("^~", prefix)
        assert "auth_request /_auth_guard;" in block, prefix
        assert "error_page 401 = @api_unauthorized;" in block, prefix
        assert "proxy_pass http://$h:8091;" in block, prefix


def test_existing_special_protected_locations_kept():
    for kind, path in SPECIAL_PROTECTED:
        block = _location_block(kind, path)
        assert "auth_request /_auth_guard;" in block, path
        assert "proxy_pass http://$h:8091;" in block, path
        assert _text().count("location %s %s {" % (kind, path)) == 1, \
            "重复 location 可能被绕过: %s" % path


# ── 公开白名单 ────────────────────────────────────────────────────────────
def test_public_whitelist_covers_login_and_health_endpoints():
    rx = _public_whitelist_re()
    for path in ("/api/health",
                 "/api/auth/login", "/api/auth/logout", "/api/auth/me",
                 "/api/auth/oidc/start", "/api/auth/oidc/status",
                 "/api/bff/identity/me",
                 "/api/m/position",
                 "/api/overview", "/api/home", "/api/brief"):
        assert rx.match(path), "公开必需端点未进白名单: %s" % path
        assert not _is_protected(path), "公开端点被受保护规则误伤: %s" % path
    # /api/health 不得有独立受保护 location（保持匿名 200）
    assert "location = /api/health" not in _text()


def test_intel_public_subpaths_only():
    rx = _public_whitelist_re()
    for path in ("/api/intel/brief", "/api/intel/policy", "/api/intel/sources"):
        assert rx.match(path), "intel 公开子路径未进白名单: %s" % path
    for path in ("/api/intel", "/api/intel/bus", "/api/intel/evidence-targets",
                 "/api/intel/other"):
        assert not rx.match(path), "intel 受保护/未登记路径不得进公开白名单: %s" % path


def test_protected_prefixes_not_in_public_whitelist():
    rx = _public_whitelist_re()
    for path in ("/api/platform/approvals", "/api/org/tasks",
                 "/api/analysis/submit", "/api/os/status",
                 "/api/jev/review/queue", "/api/jev/query",
                 "/api/news/intel", "/api/xq/intel/dates"):
        assert not rx.match(path), "受保护路径误入公开白名单: %s" % path
        assert _is_protected(path), "受保护路径未被识别: %s" % path


def test_unknown_prefix_default_denied():
    rx = _public_whitelist_re()
    for path in ("/api/not-registered-xyz", "/api/new_feature/x",
                 "/api/platformx", "/api/jevx"):
        assert not rx.match(path), "未登记前缀不应命中公开白名单: %s" % path


def test_public_whitelist_covers_static_page_references():
    rx = _public_whitelist_re()
    uncovered = []
    for path in _static_api_paths():
        if _is_protected(path) or rx.match(path):
            continue
        uncovered.append(path)
    assert not uncovered, (
        "公开页/JS 引用了未登记（会被默认拒绝 401）的 /api 路径（%d 个，示例 %r）；"
        "请把它们显式加入 nginx.conf H4-B 白名单或确认为受保护前缀"
        % (len(uncovered), sorted(set(uncovered))[:10])
    )


def test_public_whitelist_documented():
    s = _text()
    assert "公开白名单明细（一级路由名 141 个 + intel 3 子路径" in s
    # 明细注释至少逐项列出 141 个一级名（按行内空格分隔计）
    detail = s.split("公开白名单明细", 1)[1].split("公开族 A", 1)[0]
    names = [w for w in re.findall(r"\b[a-z][a-z0-9_.\-]*\b", detail)
             if w not in ("api", "intel")]
    assert len(names) >= 141, "白名单明细注释不完整: %d" % len(names)


def test_public_whitelist_regex_and_docs_are_bidirectional():
    """regex 里每个字面一级名都必须在明细注释出现（防加 regex 忘登记/注释漂移）。"""
    s = _text()
    detail = s.split("公开白名单明细", 1)[1].split("公开族 A", 1)[0]
    missing = []
    for alt in _public_whitelist_alts():
        if any(ch in alt for ch in "[]()?\\"):  # 通配模式（不逐名核对）
            continue
        if not re.search(r"(?<![a-z0-9_.-])" + re.escape(alt) + r"(?![a-z0-9_.-])", detail):
            missing.append(alt)
    assert not missing, "regex 有、明细注释无的一级名: %r" % missing


def test_existing_page_protection_not_regressed():
    for path in ("/devops", "/ide", "/approvals", "/agent_ide_bundle.json"):
        block = _location_block("=", path)
        assert "auth_request /_auth_guard;" in block, path


# ── 真语法校验 ────────────────────────────────────────────────────────────
def _nginx_test_container(conf: Path) -> tuple:
    """把配置挂进 nginx:alpine 跑 nginx -t；返回 (ok, 输出)。"""
    td = tempfile.mkdtemp(prefix="h4-nginx-")
    try:
        target = Path(td) / "h4-portal.conf"
        target.write_bytes(conf.read_bytes())
        r = subprocess.run(
            ["docker", "run", "--rm",
             "-v", "%s:/etc/nginx/conf.d:ro" % td,
             "nginx:alpine", "nginx", "-t"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        return r.returncode == 0, (r.stderr or "") + (r.stdout or "")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_nginx_syntax_if_available():
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "image", "inspect", "nginx:alpine"],
                               capture_output=True, timeout=60)
        if probe.returncode == 0:
            ok, out = _nginx_test_container(CONF)
            assert ok, "docker nginx -t 失败:\n%s" % out[-2000:]
            return
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("host 无 nginx 二进制且 docker nginx:alpine 不可用（未改生产 nginx）")
    r = subprocess.run([nginx, "-t", "-c", str(CONF)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, (r.stderr or r.stdout)[-2000:]

