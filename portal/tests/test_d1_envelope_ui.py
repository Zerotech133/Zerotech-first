# -*- coding: utf-8 -*-
"""D1 页面 IFS envelope 迁移回归（IFS v1 F1c / WO-IFS-ENVELOPE-UI-20261104）。

覆盖：
1) ``portal/static/ifs_client.js``：语法（node --check）+ 解包语义（新 envelope / 旧格式 /
   warnings / request_id / error_detail 兼容旧 error / training source 碰撞）；
2) D1 页面迁移：d1_research / d1_training 引入 ifs_client.js、读 ``data`` 优先 + 旧字段兜底、
   只读展示 envelope 元信息与 warnings；
3) 只读边界：页面 API 端点白名单不变（training 无 POST；research 仅既有 decision 写入口重试）；
4) 未接线范围：d1_war_room 消费聚合接口（未 envelope 接线）不在本单迁移；
5) route baseline 297 条零漂移（本单无 API/路由变更）。
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
CLIENT = PORTAL / "static" / "ifs_client.js"
TRAINING = PORTAL / "d1_training.html"
RESEARCH = PORTAL / "d1_research.html"
WAR_ROOM = PORTAL / "d1_war_room.html"
NODE = shutil.which("node")

sys.path.insert(0, str(PORTAL))
sys.path.insert(0, str(REPO))


def _read(path):
    return path.read_text(encoding="utf-8")


def _apis(html):
    return set(re.findall(r"/api/[A-Za-z0-9_./-]+", html))


# ── 1. 客户端文件 ────────────────────────────────────────────────────
def test_ifs_client_file_exists():
    assert CLIENT.is_file(), "缺 portal/static/ifs_client.js"
    src = _read(CLIENT)
    for token in ("unwrapEnvelope", "warnings", "request_id", "error_detail",
                  "source_detail", "renderMeta", "renderWarnings", "IFSClient"):
        assert token in src, "客户端缺能力: %s" % token
    assert "fetch(" in src  # 只读 fetch 包装（无写方法）


def test_ifs_client_syntax():
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    r = subprocess.run([NODE, "--check", str(CLIENT)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "ifs_client.js 语法错误: %s" % (r.stderr or r.stdout)


def test_ifs_client_unwrap_legacy_and_envelope():
    """node 内真实执行客户端：旧格式 fallback / envelope 优先 / warnings / request_id / 错误归一。"""
    if not NODE:
        pytest.skip("node 不可用，跳过客户端行为校验")
    script = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = { module: { exports: {} }, console: console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'ifs_client.js' });
const I = sandbox.IFSClient;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } }

// 旧格式（无 envelope）：data=原 payload，warnings/request_id 空
const legacy = { ok: true, items: [{ code: '600519' }], date: '2026-11-04' };
let e = I.unwrapEnvelope(legacy);
assert(e.isEnvelope === false, 'legacy.isEnvelope');
assert(e.ok === true, 'legacy.ok');
assert(e.data === legacy, 'legacy.data fallback');
assert(e.warnings.length === 0, 'legacy.warnings');
assert(e.requestId === '', 'legacy.requestId');
assert(e.error === null, 'legacy.error');

// 旧错误字符串兼容
const legacyErr = { ok: false, error: 'bad_limit', message: 'limit 非法' };
let le = I.errorInfo(legacyErr);
assert(le && le.code === 'bad_limit', 'legacy error code');
assert(le.retryable === false, 'legacy error retryable');
assert(I.errorText(legacyErr).indexOf('bad_limit') === 0, 'legacy errorText');

// 新 envelope：data 优先 + warnings + request_id + source
const rid = 'req-0123456789abcdef0123456789abcdef';
const envelope = {
  ok: true, schema_version: 'd1_research/v1', request_id: rid,
  source: { interface_id: 'd1.research.get', source_of_truth: 'x',
            source_mode: 'aggregate', stale: false },
  data: { ok: true, date: '2026-11-04', items: [{ code: '600519' }] },
  warnings: [{ code: 'data_unavailable', message: 'no episodes yet' }],
  date: '2026-11-04'
};
let ev = I.unwrapEnvelope(envelope);
assert(ev.isEnvelope === true, 'env.isEnvelope');
assert(ev.data.items.length === 1, 'env.data');
assert(ev.warnings.length === 1, 'env.warnings');
assert(ev.requestId === rid, 'env.requestId');
assert(ev.interfaceId === 'd1.research.get', 'env.interfaceId');
assert(ev.sourceMode === 'aggregate', 'env.sourceMode');
assert(ev.error === null, 'env.error');

// error/v1 + error_detail 优先，兼容旧 error 字符串
const envErr = { ok: false, schema_version: 'error/v1', request_id: rid,
  error: 'bad_target_action',
  error_detail: { code: 'invalid_parameter', message: 'm', retryable: false,
                  details: { legacy_code: 'bad_target_action', http_status: 400 } },
  warnings: [] };
let ee = I.errorInfo(envErr);
assert(ee.code === 'invalid_parameter', 'error_detail code');
assert(ee.legacyCode === 'bad_target_action', 'error_detail legacy_code');
assert(ee.httpStatus === 400, 'error_detail http_status');
assert(I.errorText(envErr).indexOf('invalid_parameter') === 0, 'errorText');

// training.episode.get 字段碰撞：source 字符串 + source_detail 对象
const train = { ok: true, schema_version: 'training_episode/v1', request_id: rid,
  source: 'agent_platform/data/training/episodes.jsonl',
  source_detail: { interface_id: 'training.episode.get',
                   source_of_truth: 'y', source_mode: 'ledger' },
  data: { ok: true, data_unavailable: true, count: 0 }, warnings: [] };
let te = I.unwrapEnvelope(train);
assert(te.sourceMode === 'ledger', 'training source_detail source_mode');
assert(te.data.data_unavailable === true, 'training data');

// DOM 渲染（安全转义 + 只读元信息）
function el() { return { textContent: '', innerHTML: '', style: {},
  attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; } }; }
const meta = el();
I.renderMeta(meta, envelope, {});
assert(meta.textContent.indexOf('IFS envelope') === 0, 'renderMeta text');
assert(meta.textContent.indexOf(rid) >= 0, 'renderMeta request_id');
assert(meta.attrs['data-envelope'] === 'envelope', 'renderMeta attr');
const warn = el();
assert(I.renderWarnings(warn, envelope, { title: 'IFS warnings' }) === 1, 'renderWarnings count');
assert(warn.innerHTML.indexOf('data_unavailable') >= 0, 'renderWarnings html');
assert(warn.style.display === 'block', 'renderWarnings display');
const legacyMeta = el();
I.renderMeta(legacyMeta, legacy, {});
assert(legacyMeta.attrs['data-envelope'] === 'legacy', 'legacy meta attr');
assert(legacyMeta.textContent === I.LEGACY_FALLBACK_TEXT, 'legacy meta text');
console.log(JSON.stringify({ ok: true, version: I.VERSION }));
"""
    r = subprocess.run([NODE, "-e", script, str(CLIENT)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "客户端行为校验失败: %s\n%s" % (
        r.stderr or "", r.stdout or "")
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True


# ── 2. D1 页面迁移 ───────────────────────────────────────────────────
def test_training_page_uses_envelope_client():
    html = _read(TRAINING)
    assert '/static/ifs_client.js' in html
    assert "IFSClient" in html and "unwrapPayload(" in html
    assert "renderEnvelope(" in html and "IFS.renderMeta" in html
    assert 'id="env"' in html and 'id="warns"' in html
    assert "IFS warnings" in html or "renderWarnings" in html
    # 旧格式/客户端缺失时仍能按旧顶层字段兜底
    assert "旧格式响应" in html
    # 只读：唯一 GET 端点，无 POST
    assert _apis(html) == {"/api/d1/training/episodes"}
    assert "method:'POST'" not in html and 'method:"POST"' not in html
    for token in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js"):
        assert token in html


def test_research_page_uses_envelope_client():
    html = _read(RESEARCH)
    assert '/static/ifs_client.js' in html
    assert "IFSClient" in html and "unwrapPayload(" in html
    assert "renderEnvelope(" in html and "IFS.renderMeta" in html
    assert 'id="env"' in html and 'id="warns"' in html
    assert "旧格式响应" in html
    # GET envelope 接口 + 既有 decision 写入口（403 token 重试共 2 个 POST，未新增写接口）
    assert _apis(html) == {"/api/d1/research", "/api/d1/decisions", "/api/d1/decision"}
    assert html.count("method:'POST'") == 2
    for token in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js"):
        assert token in html


def test_war_room_not_in_f1c_scope():
    """war_room 消费聚合接口（F1b 未接线），本单不迁移：保持零改动语义。"""
    if WAR_ROOM.is_file():
        assert '/static/ifs_client.js' not in _read(WAR_ROOM)
    spec = importlib.util.spec_from_file_location(
        "ifs_envelope_f1c_t", PORTAL / "ifs_envelope.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    paths = {meta["path"] for meta in mod.INTERFACES.values()}
    assert "/api/war-room/snapshot" not in paths
    assert "/api/d1/research" in paths
    assert "/api/d1/training/episodes" in paths


def test_pages_preserve_readonly_and_no_new_endpoints():
    """迁移只读展示，不新增接口/写操作：两页 API 路径与迁移前一致。"""
    training = _read(TRAINING)
    research = _read(RESEARCH)
    assert "method:'POST'" not in training
    assert research.count("method:'POST'") == 2  # 既有 403 token 重试，非新增
    assert "IFSClient.fetchJson(" not in training  # 页面直连既有端点，不引入新请求
    assert "IFSClient.fetchJson(" not in research


def test_route_baseline_unchanged():
    spec = importlib.util.spec_from_file_location(
        "route_inventory_f1c_t", REPO / "tools" / "portal_route_inventory.py")
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
    assert baseline.get("total") == inv["total"], (
        "route baseline total 应与当前 portal_api 路由集合一致：baseline=%r inv=%r"
        % (baseline.get("total"), inv["total"]))
    assert set(tuple(x) for x in baseline.get("signatures") or []) == sigs
