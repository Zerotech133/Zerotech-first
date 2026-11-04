# -*- coding: utf-8 -*-
"""ACAD1 军团接口 SSO 客户端回归（WO-ACAD1-SSO-20261104）。

覆盖：
1) ``portal/static/acad1_client.js``：node --check；node 沙箱 mock fetch 验证
   SSO 换票（POST /quant-api/api/v1/auth/portal-sso）→ Bearer GET 六接口；
   401 显式（清 token + 重换票一次 + 仍 401 抛错）、只读 GET、绝对 URL 拒绝、
   **无 token 落盘**（localStorage/sessionStorage/document.cookie 访问即报错）；
2) ``portal/shuijing.html``：只读“军团接口”面板（六接口/401/只读文案）、
   保留 tokens/nav/shortscreen + shuijing.css/js + 既有 pool 页、无直接 8105 地址/无新增写接口；
3) 只读边界：页面不内联 token/不出现业务 POST；客户端唯一 POST 仅 SSO 换票；
4) 路由基线 297 不变（本单不改 portal_api/路由）；
5) live 只读契约（8734 /quant-api 未登录 401）不可达则 skip。
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
CLIENT = PORTAL / "static" / "acad1_client.js"
PAGE = PORTAL / "shuijing.html"
NODE = shutil.which("node")

sys.path.insert(0, str(PORTAL))
sys.path.insert(0, str(REPO))

SSO_PATH = "/quant-api/api/v1/auth/portal-sso"
BASE_PATH = "/quant-api/api/v1/acad1-research"
INTERFACE_KEYS = ("pool", "stats", "reviews", "decisions", "evidence", "calendar")


def _read(path):
    return path.read_text(encoding="utf-8")


# ── 1. 静态：客户端 / 页面 ──────────────────────────────────────────
def test_client_file_exists_and_contract():
    assert CLIENT.is_file(), "缺 portal/static/acad1_client.js"
    src = _read(CLIENT)
    for token in (SSO_PATH, BASE_PATH, "Authorization", "Bearer",
                  "credentials: 'same-origin'", "unauthorized", "get_only"):
        assert token in src, "客户端缺契约元素: %s" % token
    for key in INTERFACE_KEYS:
        assert "'%s'" % key in src, "客户端缺接口: %s" % key
    # 只读：六接口仅 GET；唯一 POST 是 SSO 换票
    assert "(opts.method || 'GET').toUpperCase()" in src
    assert "method: 'POST'" in src, "SSO 换票需唯一 POST"
    # 无 token 落盘（任何持久化 API 都不允许出现）
    for bad in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                "window.name", "location.search", "location.hash"):
        assert bad not in src, "客户端出现持久化/URL token 面: %s" % bad
    assert "token=" not in src, "token 不得进 URL"


def test_client_syntax():
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    r = subprocess.run([NODE, "--check", str(CLIENT)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "acad1_client.js 语法错误: %s" % (r.stderr or r.stdout)


def test_client_mock_sso_401_and_no_token_persistence():
    """node 沙箱 mock：SSO→Bearer 六接口、401 显式、只读、无落盘。"""
    if not NODE:
        pytest.skip("node 不可用，跳过客户端行为校验")
    script = r"""
const fs = require('fs');
const vm = require('vm');
const CLIENT = process.argv[1];
const code = fs.readFileSync(CLIENT, 'utf8');

function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } }

function makeSandbox(fetchImpl) {
  const calls = [];
  const counter = { storage: 0 };
  const sb = {
    module: { exports: {} },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    fetch: function (url, opts) {
      calls.push({ url: url, method: (opts && opts.method) || 'GET',
                   headers: (opts && opts.headers) || {},
                   credentials: opts && opts.credentials });
      return fetchImpl(url, opts);
    },
  };
  function guard(name) {
    return new Proxy({}, { get: function () { counter.storage += 1; throw new Error('storage accessed: ' + name); } });
  }
  sb.localStorage = guard('localStorage');
  sb.sessionStorage = guard('sessionStorage');
  sb.document = { get cookie() { counter.storage += 1; throw new Error('document.cookie accessed'); } };
  sb.globalThis = sb;
  sb.window = sb;
  vm.createContext(sb);
  vm.runInContext(code, sb, { filename: 'acad1_client.js' });
  return { I: sb.ACAD1Client, calls: calls, counter: counter };
}
function resp(status, body) {
  return { status: status, ok: status >= 200 && status < 300,
           json: function () { return Promise.resolve(body); } };
}
const SSO_OK_1 = { ok: true, token: 'tok-1', username: '司马徽', role: 'VIEWER', read_only: false };
const SSO_OK_2 = { ok: true, token: 'tok-2', username: '司马徽', role: 'VIEWER', read_only: false };
const DATA_OK = { rows: [], pool_date: '2026-11-03' };

(async function () {
  // A. happy path：1 次 SSO + 6 次 Bearer GET；token 仅内存
  var a = makeSandbox(function (url) {
    if (url.indexOf('/auth/portal-sso') >= 0) return Promise.resolve(resp(200, SSO_OK_1));
    return Promise.resolve(resp(200, DATA_OK));
  });
  var results = await a.I.loadAll();
  assert(results.length === 6, 'A: 六接口');
  assert(results.every(function (r) { return r.ok; }), 'A: 全部 ok');
  assert(a.calls.length === 7, 'A: SSO+6 请求，实际=' + a.calls.length);
  assert(a.calls[0].url === a.I.SSO_PATH, 'A: 首请求 SSO 路径');
  assert(a.calls[0].method === 'POST', 'A: SSO POST');
  assert(a.calls[0].credentials === 'same-origin', 'A: SSO same-origin cookie');
  for (var i = 1; i < a.calls.length; i += 1) {
    assert(a.calls[i].method === 'GET', 'A: 数据 GET');
    assert(a.calls[i].url.indexOf(a.I.BASE_PATH) === 0, 'A: 数据同源相对路径');
    assert(a.calls[i].headers['Authorization'] === 'Bearer tok-1', 'A: Bearer 头');
  }
  assert(a.I.getToken() === 'tok-1', 'A: token 仅内存可读');
  assert(a.counter.storage === 0, 'A: 未访问任何持久化面');

  // B. 401 → 清 token → 重换票 → 重试成功
  var b = makeSandbox((function () {
    var sso = 0; var data = 0;
    return function (url) {
      if (url.indexOf('/auth/portal-sso') >= 0) {
        sso += 1; return Promise.resolve(resp(200, sso === 1 ? SSO_OK_1 : SSO_OK_2));
      }
      data += 1;
      return Promise.resolve(data === 1 ? resp(401, { detail: { code: 'unauthorized' } }) : resp(200, DATA_OK));
    };
  })());
  var retry = await b.I.fetchAcad1('/stats');
  assert(retry && retry.pool_date === '2026-11-03', 'B: 重试成功');
  assert(b.I.getToken() === 'tok-2', 'B: 401 后换新 token');
  assert(b.calls.length === 4, 'B: sso/data(401)/sso/data = 4，实际=' + b.calls.length);
  assert(b.counter.storage === 0, 'B: 未访问持久化面');

  // C. 401 → 重换票仍 401 → 显式 unauthorized（不静默）
  var c = makeSandbox((function () {
    var sso = 0; var data = 0;
    return function (url) {
      if (url.indexOf('/auth/portal-sso') >= 0) {
        sso += 1;
        return Promise.resolve(sso === 1 ? resp(200, SSO_OK_1)
          : resp(401, { detail: { code: 'portal_sso.no_session' } }));
      }
      data += 1;
      return Promise.resolve(resp(401, { detail: { code: 'unauthorized' } }));
    };
  })());
  var caught = null;
  try { await c.I.fetchAcad1('/pool'); } catch (e) { caught = e; }
  assert(caught && caught.kind === 'unauthorized', 'C: kind=unauthorized');
  assert(caught.status === 401, 'C: status=401');
  assert(caught.code === 'portal_sso.no_session', 'C: 8105 error code 透传');
  assert(c.I.getToken() === '', 'C: 失败后内存 token 已清');
  assert(c.counter.storage === 0, 'C: 未访问持久化面');

  // D. 未登录（SSO 直接 401 no_session）
  var d = makeSandbox(function () {
    return Promise.resolve(resp(401, { detail: { code: 'portal_sso.no_session' } }));
  });
  var dErr = null;
  try { await d.I.ensureToken(); } catch (e) { dErr = e; }
  assert(dErr && dErr.kind === 'unauthorized' && dErr.code === 'portal_sso.no_session', 'D: 未登录显式 401');
  assert(d.calls.length === 1 && d.calls[0].method === 'POST', 'D: 仅 SSO 请求');

  // E. 只读：非 GET 直接拒绝，不发请求
  var e = makeSandbox(function () { return Promise.resolve(resp(200, DATA_OK)); });
  var eErr = null;
  try { await e.I.fetchAcad1('/pool', { method: 'POST' }); } catch (err) { eErr = err; }
  assert(eErr && eErr.code === 'get_only', 'E: 非 GET 拒绝');
  assert(e.calls.length === 0, 'E: 无请求发出');

  // F. 绝对/跨源 URL 拒绝
  var f = makeSandbox(function () { return Promise.resolve(resp(200, DATA_OK)); });
  var fErr = null;
  try { await f.I.fetchAcad1('http://127.0.0.1:8105/api/v1/x'); } catch (err) { fErr = err; }
  assert(fErr && fErr.code === 'absolute_url_forbidden', 'F: 绝对 URL 拒绝');
  assert(f.calls.length === 0, 'F: 无请求发出');

  // G. 接口清单/摘要
  var g = makeSandbox(function () { return Promise.resolve(resp(200, DATA_OK)); });
  var list = g.I.interfaceList();
  assert(list.length === 6, 'G: 六接口清单');
  var keys = list.map(function (x) { return x.key; }).join(',');
  assert(keys === 'pool,stats,reviews,decisions,evidence,calendar', 'G: 接口键: ' + keys);
  assert(list.every(function (x) { return x.path.indexOf(g.I.BASE_PATH) === 0; }), 'G: 同源相对路径');
  var summary = g.I.summarize('stats', { pool_total: 12, obs_done: 3, obs_total: 5,
    bt_total: 7, review_total: 4, pool_date: '2026-11-03' });
  assert(summary.indexOf('池=12') >= 0 && summary.indexOf('2026-11-03') >= 0, 'G: stats 摘要');
  assert(g.counter.storage === 0, 'G: 未访问持久化面');

  console.log(JSON.stringify({ ok: true, version: g.I.VERSION }));
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
"""
    r = subprocess.run([NODE, "-e", script, str(CLIENT)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "客户端行为校验失败:\n%s\n%s" % (r.stderr or "", r.stdout or "")
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True


def test_page_panel_and_readonly_boundary():
    html = _read(PAGE)
    # 面板 + 客户端接入
    for token in ("/static/acad1_client.js", "acad1-api-panel", "军团接口",
                  "acad1-api-load", "acad1-api-rows", "acad1-api-state",
                  "401", "只读", "token 仅内存", "/quant-api"):
        assert token in html, "页面缺: %s" % token
    for key in INTERFACE_KEYS:
        assert key in html or key in _read(CLIENT), "缺接口: %s" % key
    # 既有页面/三件套保留
    for token in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js",
                  "/static/shuijing.css", "/static/shuijing.js", "ACAD1 推荐池",
                  "/api/acad1/pool"):
        assert token in html, "既有页面元素丢失: %s" % token
    # 不直接连 8105 / 不出现内联 token / 无业务写接口
    assert "127.0.0.1:8105" not in html and "localhost:8105" not in html
    assert "method:'POST'" not in html and 'method:"POST"' not in html
    assert "Bearer " not in html, "页面不内联 Bearer（由客户端内存 token 提供）"
    assert "token=" not in html


# ── 2. 沙箱/静态 JS 语法 ────────────────────────────────────────────
def test_page_inline_js_syntax(tmp_path):
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    html = _read(PAGE)
    blocks = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S):
        body = m.group(1).strip()
        if body:
            blocks.append(body)
    assert blocks, "页面无内联脚本？"
    target = tmp_path / "shuijing_inline.js"
    target.write_text("\n;\n".join(blocks), encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(target)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "shuijing 内联脚本语法错误: %s" % (r.stderr or r.stdout)


def test_route_baseline_unchanged():
    spec = importlib.util.spec_from_file_location(
        "route_inventory_acad1_sso_t", REPO / "tools" / "portal_route_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    source = _read(PORTAL / "portal_api.py")
    inv = mod.extract_routes(source)
    table = PORTAL / "route_table.py"
    if table.is_file():
        have = {x["route"] for x in inv["GET"]["exact"]}
        for route in mod.table_exact_routes(table):
            if route not in have:
                inv["GET"]["exact"].append({"route": route, "kind": "exact", "line": 0})
                have.add(route)
        inv["GET"]["exact"].sort(key=lambda x: x["route"])
        inv["total"] = sum(len(inv[m][k]) for m in ("GET", "POST")
                           for k in ("exact", "prefix"))
    sigs = mod.signatures(inv)
    baseline = json.loads((REPO / "tools" / "portal_route_baseline.json")
                          .read_text(encoding="utf-8"))
    baseline_sigs = set(tuple(x) for x in baseline.get("signatures") or [])
    # 本单不改 portal_api/路由：当前签名集必须与既有基线一致（数量随 master 演进，不写死）
    assert baseline.get("total") == len(baseline_sigs)
    assert sigs == baseline_sigs


# ── 3. live 只读契约（不可达 skip，不依赖服务） ─────────────────────
def _http_status(req):
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()[:500]
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read() or b"")[:500]
    except Exception as exc:  # noqa: BLE001 — 服务不可达 → skip
        pytest.skip("8734 不可达，跳过 live 契约校验：%s" % str(exc)[:120])


def test_live_quant_api_unauth_401_contract():
    """未登录时 8734 /quant-api 反代：数据 401、SSO GET 405（POST-only）。"""
    status, _ = _http_status(urllib.request.Request(
        "http://127.0.0.1:8734%s/pool" % BASE_PATH, method="GET"))
    assert status == 401, "未登录数据接口应 401，实际=%s" % status
    status2, _ = _http_status(urllib.request.Request(
        "http://127.0.0.1:8734%s" % SSO_PATH, method="GET"))
    assert status2 == 405, "SSO 仅 POST，GET 应 405，实际=%s" % status2
