# -*- coding: utf-8 -*-
"""nginx auth_request 页面真保护配置回归（2026-10-30 L2）。

2026-10-31 扩：WO-LEGION-AUTH-20261031 认证拓扑收口——
- /hermes/、/extensions/ 在 auth_request 后注入 Hermes trusted-header 身份
  （X-Portal-User，单用户常量），消除 8734→8787 第二次密码登录；
- 启动器不再注入 HERMES_WEBUI_PASSWORD，改 HERMES_WEBUI_TRUSTED_AUTH_HEADER；
- A0 主聊天直连 8787 只读（不调 8796 执行）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[1] / "nginx.conf"
PROTECTED = ("/os-entry", "/os-board", "/devops", "/model_admin", "/approvals")
REPO = Path(__file__).resolve().parents[2]
HERMES_LAUNCHER = REPO / "Hermes" / "start_hermes_bg.py"
A0_MAINCHAT = REPO / "Hermes" / "extensions" / "legion-ide" / "a0-mainchat.js"


def _text():
    return CONF.read_text(encoding="utf-8")


def test_no_bom():
    raw = CONF.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "nginx.conf 不得带 UTF-8 BOM（nginx -t 会失败）"


def test_auth_guard_internal_block():
    s = _text()
    assert "location = /_auth_guard" in s
    assert "internal;" in s
    assert "proxy_pass http://$h:8091/api/auth/guard" in s
    assert "proxy_set_header Cookie $http_cookie" in s


def test_relative_redirect():
    s = _text()
    assert "location @login_required" in s
    assert "absolute_redirect off;" in s
    assert "return 302 /login?next=$uri;" in s


def test_five_pages_protected():
    s = _text()
    # 2026-10-30 P0：页面 + IDE 数据面（/lde/ /openhands/ /extensions/）统一认证
    # 5 页 + /ide + bundle + /hermes/ + /lde/ + /openhands/ + /extensions/
    assert s.count("auth_request /_auth_guard;") >= len(PROTECTED) + 6
    for p in PROTECTED + ("/ide", "/agent_ide_bundle.json"):
        m = re.search(r"location = " + re.escape(p) + r" \{(.*?)\n    \}", s, re.S)
        assert m, p
        assert "auth_request /_auth_guard;" in m.group(1), p
        assert "error_page 401 = @login_required;" in m.group(1), p
    for prefix in ("/hermes/", "/lde/", "/openhands/", "/extensions/"):
        m = re.search(r"location \^~ " + re.escape(prefix) + r" \{(.*?)\n    \}", s, re.S)
        assert m, prefix
        assert "auth_request /_auth_guard;" in m.group(1), prefix
        assert "error_page 401 = @login_required;" in m.group(1), prefix


def test_login_not_protected():
    s = _text()
    m = re.search(r"location = /login[^\n]*", s)
    assert m
    assert "auth_request" not in m.group(0)


def test_non_intel_public_routes_not_locked():
    """2026-11-01 H4-B：情报全私有后，/quant 与 /api/health 仍公开；全局 /api/ 已从
    公开基线收敛为**显式白名单 + 未登记默认拒绝**（不再有公开 passthrough）。"""
    s = _text()
    q = s.split("location = /quant", 1)
    assert len(q) == 2
    assert "auth_request" not in q[1][:120]
    assert "location /api/ {" in s
    api_block = s.split("location /api/ {", 1)[1].split("}", 1)[0]
    assert "auth_request" not in api_block, "/api/ 不得整体加锁"
    assert "return 401" in api_block and "proxy_pass" not in api_block, \
        "全局 /api/ 必须是白名单未登记默认拒绝，而不是公开 passthrough"
    assert re.search(r"location ~ \^/api/\(.*\bhealth\b", s), \
        "/api/health 必须在 H4-B 公开白名单 regex 内（否则默认 401 锁死探活）"


# ── WO-LEGION-AUTH-20261031：Hermes trusted-header 认证拓扑收口 ──────────────


def _location_block(prefix: str) -> str:
    m = re.search(r"location \^~ " + re.escape(prefix) + r" \{(.*?)\n    \}", _text(), re.S)
    assert m, prefix
    return m.group(1)


def test_hermes_and_extensions_inject_portal_user():
    """8734→/hermes/ 消除第二次密码登录：auth_request 通过后注入 trusted-header 身份。"""
    s = _text()
    # 只允许在这两处注入：直连 8787 无该头必须由 Hermes 302/401 拒绝
    assert s.count('proxy_set_header X-Portal-User "laotian";') == 2
    for prefix in ("/hermes/", "/extensions/"):
        block = _location_block(prefix)
        assert "auth_request /_auth_guard;" in block, prefix
        assert "error_page 401 = @login_required;" in block, prefix
        assert 'proxy_set_header X-Portal-User "laotian";' in block, prefix
        # nginx 语义：本层一旦声明 proxy_set_header，server 级不再继承 → 显式重声明保行为不变
        assert "proxy_set_header X-Real-IP $remote_addr;" in block, prefix
        assert 'proxy_set_header X-Portal-Proxy "1";' in block, prefix


def test_portal_user_header_not_injected_globally():
    """防误注入：server 级不得带 X-Portal-User（否则所有 8091 反代都会被"认证"）。"""
    head = re.split(r"\n    location ", _text(), maxsplit=1)[0]
    assert "X-Portal-User" not in head


def test_hermes_launcher_trusted_header_and_ephemeral_password():
    """启动器：trusted-header 注入 + 一次性随机密码覆盖 settings 旧 hash；不落日志。"""
    src = HERMES_LAUNCHER.read_text(encoding="utf-8")
    assert "HERMES_WEBUI_TRUSTED_AUTH_HEADER" in src
    assert '"X-Portal-User"' in src
    assert "_PW_FILE" not in src, "磁盘密码文件注入应已移除（双登录根因）"
    assert "secrets.token_urlsafe(" in src, "需用一次性随机密码覆盖 settings 旧 hash"
    assert 'env["HERMES_WEBUI_PASSWORD"] =' in src
    for line in src.splitlines():
        assert not (("_log(" in line or "print(" in line) and "HERMES_WEBUI_PASSWORD" in line), \
            "随机密码不得进日志/打印: " + line


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH，跳过 a0-mainchat 可执行断言")
    return node


def test_a0_mainchat_syntax():
    """node --check：语法可执行断言（不依赖浏览器/生产）。"""
    r = subprocess.run([_node(), "--check", str(A0_MAINCHAT)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, (r.stderr or r.stdout)[-2000:]


def test_a0_mainchat_direct_readonly_static():
    """静态断言：8787 直连判定 + 只读守卫必须先于 fetch(8796)。"""
    src = A0_MAINCHAT.read_text(encoding="utf-8")
    assert "function isDirectMode()" in src
    assert 'String(location.port || "") === "8787"' in src
    assert "直连只读" in src and "8734 /hermes/" in src
    body = re.search(r"async function runA0\(text\) \{(.*?)\n  \}\n", src, re.S)
    assert body, "runA0 函数体未找到"
    code = body.group(1)
    direct_pos = code.find("isDirectMode()")
    fetch_pos = code.find("fetch(endpoint()")
    assert direct_pos != -1 and fetch_pos != -1
    assert direct_pos < fetch_pos, "直连只读守卫必须先于 8796 fetch（否则会先执行再报错）"


_A0_NODE_HARNESS = r"""
const assert = require('assert');
const jsPath = process.env.A0_JS;
global.location = { port: '8787', origin: 'http://127.0.0.1:8787' };
global.localStorage = { getItem: () => null, setItem: () => {} };
global.window = { confirm: () => false, open: () => {} };
function el() {
  return { style: {}, dataset: {}, classList: { toggle: () => {} },
           appendChild: () => {}, setAttribute: () => {}, remove: () => {} };
}
global.document = {
  readyState: 'complete',
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  createElement: () => el(),
  body: { appendChild: () => {} },
};
const m = require(jsPath);
assert.strictEqual(typeof m.isDirectMode, 'function', 'isDirectMode 未导出（测试钩子缺失）');
assert.strictEqual(m.isDirectMode(), true, '8787 应判定为直连');
let calls = [];
global.fetch = (url) => {
  calls.push(String(url));
  return Promise.resolve({ ok: false, status: 403,
    json: async () => ({ ok: false, error: 'oh_auth_required' }) });
};
(async () => {
  await m.runA0('hello');
  assert.deepStrictEqual(calls, [], '直连只读不得调用 8796（实得 ' + calls.join(',') + '）');
  global.location.port = '8734';
  assert.strictEqual(m.isDirectMode(), false, '8734 不应判定为直连');
  await m.runA0('hello');
  assert.strictEqual(calls.length, 1, '8734 同源应发起一次 orchestrate，实得 ' + calls.length);
  assert.strictEqual(calls[0], '/openhands/api/ide/orchestrate',
    '同源必须走 /openhands 前缀，实得 ' + calls[0]);
  console.log('A0_MAINCHAT_DIRECT_READONLY_OK');
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def test_a0_mainchat_direct_readonly_executable():
    """可执行断言：直连 8787 不 fetch 8796；8734 同源仍走 /openhands 执行入口。"""
    node = _node()
    env = os.environ.copy()
    env["A0_JS"] = str(A0_MAINCHAT)
    r = subprocess.run([node, "-e", _A0_NODE_HARNESS], cwd=str(REPO), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, (r.stderr or r.stdout)[-3000:]
    assert "A0_MAINCHAT_DIRECT_READONLY_OK" in (r.stdout or "")

def test_acad1_pool_public_page_route():
    """/acad1/pool 公开只读页路由（L2 2026-11-02）；页面文件已匿名可读，不新增鉴权依赖。"""
    s = _text()
    m = re.search(r"location = /acad1/pool \{(.*?)\n    \}", s, re.S)
    assert m, "/acad1/pool"
    block = m.group(1)
    assert "try_files /acad1_pool.html =404;" in block
    assert "auth_request" not in block
    assert 'Cache-Control "no-cache"' in block

