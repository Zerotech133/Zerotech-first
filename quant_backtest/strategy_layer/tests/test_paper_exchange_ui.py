# -*- coding: utf-8 -*-
"""Phase 4-C Paper Exchange 只读页面卡片测试（WO-PAPER-EXCHANGE-UI-20261031）。

覆盖验收口径：
1) HTML/JS 静态存在、HTML 相对引用 paper_exchange.js、前端准入三件套相对路径、
   无外部 CDN/绝对 URL/写死端口；
2) 只读契约：JS 引用且仅引用 exchange_gateway 的 4 个 GET 端点
   /paper_exchange/{health,route,accounts,snapshot}；fetch 仅 1 处且 method=GET；
   无 POST/PUT/PATCH/DELETE/XMLHttpRequest/sendBeacon/WebSocket/本地存储/表单提交；
3) 只读文案：HTML/JS 双处声明“只读 / 不下单 / 不迁移两账 / 不写服务端 / 不造数”；
   空/不可用显式文案齐全（暂无账户、尚无快照、端点未接入、接口不可用、持仓为空、
   tenant 归属未落库（不猜））；
4) 纯函数 node 契约：apiEndpoint/toQuery/parseJsonSafe/statusText/buildSnapshotQuery/
   describeHttpError/attributionNote/summarizeHealth/summarizeAccounts/summarizeSnapshot；
   node --check 通过；node 载入不落盘；
5) DOM 契约：JS 引用的元素 id 均存在于 HTML；h2=4 < 6（无需 shortscreen）；
6) 只读边界：dashboard_api/gateway/facade/tenant_model/paper_book/risk_gate/两账文件
   SHA256 前后不变；前端不启动服务、不写盘、不碰其它服务路径。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(LAYER_DIR))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from agent_platform.paper import exchange_gateway as gw  # noqa: E402

HTML_PATH = os.path.join(LAYER_DIR, "paper_exchange.html")
JS_PATH = os.path.join(LAYER_DIR, "paper_exchange.js")
DASHBOARD_API_PATH = os.path.join(LAYER_DIR, "dashboard_api.py")
GATEWAY_PATH = os.path.join(REPO, "agent_platform", "paper", "exchange_gateway.py")
FACADE_PATH = os.path.join(REPO, "agent_platform", "paper", "exchange_facade.py")
TENANT_MODEL_PATH = os.path.join(REPO, "agent_platform", "paper", "tenant_model.py")
PAPER_BOOK_PATH = os.path.join(REPO, "agent_platform", "paper", "paper_book.py")
RISK_GATE_PATH = os.path.join(REPO, "agent_platform", "paper", "risk_gate.py")
OFFICIAL_LEDGER = os.path.join(REPO, "agent_platform", "data", "paper",
                               "paper_ledger.jsonl")
LEGACY_DB = os.path.join(REPO, "k_stock", "data", "paper.db")

# 读侧契约：端点集合必须与 exchange_gateway 常量逐字一致。
ENDPOINTS = (gw.PATH_HEALTH, gw.PATH_ROUTE, gw.PATH_ACCOUNTS, gw.PATH_SNAPSHOT)
NODE_BIN = shutil.which("node")


# ── 工具 ─────────────────────────────────────────────────────────────
def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _require_node():
    if not NODE_BIN:
        pytest.skip("node 不可用（跳过 node --check / JS 契约测试）")
    return NODE_BIN


def _node_eval(script, cwd=None, timeout=90):
    result = subprocess.run(
        [_require_node(), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
    assert result.returncode == 0, (
        "node 执行失败 rc=%s\nstdout:\n%s\nstderr:\n%s"
        % (result.returncode, result.stdout[-1500:], result.stderr[-3000:])
    )
    return result.stdout


_CONTRACT_TEMPLATE = r"""
const api = require(__JS_PATH__);
const out = {
  api_names: Object.keys(api.API).sort(),
  endpoints: api.apiEndpoints(),
  book_order: api.BOOK_ORDER,
  boundary: api.boundaryChips(),
  write_keys: api.writeBoundaryKeys(),
  boot_type: typeof api.boot,
  text: {
    emptyAccounts: api.TEXT.emptyAccounts,
    emptySnapshot: api.TEXT.emptySnapshot,
    emptyPositions: api.TEXT.emptyPositions,
    endpointMissing: api.TEXT.endpointMissing,
    networkFail: api.TEXT.networkFail,
    nonJson: api.TEXT.nonJson,
    tenantUnverified: api.TEXT.tenantUnverified,
    attrMissing: api.TEXT.attrMissing
  },
  display: [api.display(null, "null"), api.display(undefined, "null"),
            api.display("x"), api.display(0)],
  query: api.toQuery({ b: "2", a: "x y", c: null, d: "" }),
  snapshot_query: api.buildSnapshotQuery(
    { book: "legacy", tenant: "t1", account: "main", combo: "" }),
  parse_bad: api.parseJsonSafe("not-json"),
  parse_good: api.parseJsonSafe('{"ok":true}'),
  status: [api.statusText("ok"), api.statusText("degraded"),
           api.statusText("empty"), api.statusText("unavailable"),
           api.statusText("")],
  http_404: api.describeHttpError(404, null),
  http_0: api.describeHttpError(0, null),
  http_400: api.describeHttpError(
    400, { error: { code: "TenantValidationError", message: "bad" } }),
  attr: api.attributionNote(
    { scope: "account_id", owner_tenant_id: null,
      storage_tenant_dimension: false }),
  health: api.summarizeHealth({
    data: {
      ok: true, status: "ok", readonly: true,
      boundaries: { ledger_write: false, new_port: false, readonly: true },
      books: {
        official: { status: "ok", backend: "official_ledger_jsonl",
                    exists: true, reasons: [] },
        legacy: { status: "unavailable", backend: "legacy_sqlite",
                  exists: false, reasons: ["legacy paper.db 不存在"] }
      }
    }
  }),
  health_bad: api.summarizeHealth({ data: {} }),
  accounts: api.summarizeAccounts({
    data: {
      count: 2,
      books: {
        official: { book: "official", available: true, attribution: "book_level",
                    storage_accounts: null,
                    storage_note: "official ledger 未记录 tenant/account 维度",
                    entries: [] },
        legacy: { book: "legacy", available: true, attribution: "account_id",
                  entries: [
                    { source: "storage", account_id: "main", tenant_id: null,
                      namespace: null, addressable: false, reason: "tenant 未落库",
                      meta: { cash: 500000.0 } }
                  ] }
      }
    }
  }),
  snap_official: api.summarizeSnapshot({
    data: {
      book: "official", status: "ok", backend: "official_ledger_jsonl",
      namespace: "sim/official/t1/a1",
      account: { tenant_id: "t1", account_id: "a1", combo_id: null },
      tenant_id: "t1",
      attribution: { scope: "book_level", owner_tenant_id: null,
                     storage_tenant_dimension: false },
      official: { rows: 2, last_date: "2026-10-30",
                  state: { positions: [
                    { symbol: "600519", weight: 0.1, entry_price: 100.0,
                      last_price: 110.0, industry: "白酒" }
                  ] } },
      legacy: null, unavailable: null, warnings: []
    }
  }),
  snap_legacy: api.summarizeSnapshot({
    data: {
      book: "legacy", status: "unavailable", backend: "legacy_sqlite",
      namespace: null,
      account: { tenant_id: "t1", account_id: "main", combo_id: null },
      tenant_id: "t1",
      attribution: { scope: "account_id", owner_tenant_id: null },
      official: null, legacy: null,
      unavailable: { code: "legacy_db_missing", reason: "paper.db 不存在" },
      warnings: ["没有造数"]
    }
  }),
  snap_bad: api.summarizeSnapshot({ data: {} })
};
console.log(JSON.stringify(out));
"""


@functools.cache
def _contract():
    script = _CONTRACT_TEMPLATE.replace("__JS_PATH__", json.dumps(JS_PATH))
    return json.loads(_node_eval(script))


# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def html_text():
    return _read(HTML_PATH)


@pytest.fixture(scope="module")
def js_text():
    return _read(JS_PATH)


@pytest.fixture(scope="module")
def contract():
    return _contract()


# ── 1. 文件存在 / 引用关系 / 前端准入三件套 ─────────────────────────
class TestFilesAndReferences:
    def test_html_and_js_exist(self):
        assert os.path.isfile(HTML_PATH) and os.path.isfile(JS_PATH)
        assert os.path.getsize(HTML_PATH) > 6000
        assert os.path.getsize(JS_PATH) > 20000

    def test_html_references_js_relative(self, html_text):
        assert 'src="paper_exchange.js"' in html_text
        assert not re.search(r'src\s*=\s*["\']https?://', html_text)
        assert not re.search(r'src\s*=\s*["\']//', html_text)

    def test_tokens_nav_shortscreen_relative_paths(self, html_text):
        assert "../../portal/static/tokens.css" in html_text
        assert "../../portal/static/nav.js" in html_text
        assert "../../portal/static/shortscreen.js" in html_text
        assert not re.search(
            r'["\']/static/(tokens\.css|nav\.js|shortscreen\.js)', html_text)

    def test_no_hardcoded_ports_or_cdn(self, html_text, js_text):
        # 只拦「真正会发请求/加载资源」的写死端口与外部 URL；离线说明性文案可提 file://
        for text in (html_text, js_text):
            for token in ("8734", "8080", "8791", "8795", "127.0.0.1",
                          "cdn."):
                assert token not in text, token
            assert not re.search(
                r'(?:src|href)\s*=\s*["\'](?:file|https?):/{2}', text)
            assert not re.search(
                r'(?:fetch|XMLHttpRequest)\s*\(\s*["\'](?:file|https?)://', text)
            assert not re.search(r'["\']C:\\\\', text)

    def test_title_and_lang(self, html_text):
        assert 'lang="zh-CN"' in html_text
        assert "Paper Exchange" in html_text
        assert "只读" in html_text


# ── 2. 只读契约：4 个 GET 端点 / fetch 仅一处 ────────────────────────
class TestReadonlyContract:
    def test_js_references_four_gateway_endpoints(self, js_text, html_text):
        for endpoint in ENDPOINTS:
            assert endpoint in js_text, endpoint
            assert js_text.count(endpoint) == 1, endpoint
            assert endpoint in html_text, endpoint

    def test_endpoints_match_gateway_constants(self, contract, js_text):
        assert {gw.PATH_HEALTH, gw.PATH_ROUTE, gw.PATH_ACCOUNTS,
                gw.PATH_SNAPSHOT} == set(ENDPOINTS)
        assert tuple(contract["endpoints"]) == ENDPOINTS
        assert contract["api_names"] == ["accounts", "health", "route",
                                         "snapshot"]
        for endpoint in ENDPOINTS:
            assert endpoint in js_text

    def test_js_only_uses_allowed_paper_exchange_paths(self, js_text):
        found = set(re.findall(r'"/paper_exchange/[a-z_]+"', js_text))
        found = {item.strip('"') for item in found}
        assert found == set(ENDPOINTS), found

    def test_single_fetch_and_get_only(self, js_text):
        assert js_text.count("fetch(") == 1
        index = js_text.index("fetch(")
        snippet = js_text[index:index + 400]
        assert "method" in snippet and '"GET"' in snippet
        assert re.search(r'\bmethod\s*:\s*"GET"', js_text)
        assert not re.search(r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']',
                             js_text)
        assert not re.search(r'["\'](POST|PUT|PATCH|DELETE)["\']', js_text)

    def test_no_write_http_verbs_or_sockets(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in ("XMLHttpRequest", "sendBeacon", "WebSocket",
                              "EventSource", "localStorage", "sessionStorage",
                              "document.cookie", "navigator.clipboard",
                              "$.ajax", "/api/", "action=", "new Function",
                              "eval(", "document.write"):
                assert forbidden not in text, forbidden

    def test_no_form_submit_upload_or_download(self, html_text, js_text):
        assert "<form" not in html_text
        assert 'type="submit"' not in html_text
        assert 'type="file"' not in html_text
        assert "onsubmit" not in html_text
        assert "download" not in js_text
        assert "Blob(" not in js_text
        assert "createObjectURL" not in js_text

    def test_no_service_start_or_listen(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in ("serve_forever", "HTTPServer", "http.server",
                              "createServer", ".listen(", "bind(",
                              "register_routes"):
                assert forbidden not in text, forbidden
            # 页面路由由 Lead 后续接入：前端不得 import/call dashboard_api
            assert "dashboard_api." not in text
            assert "from dashboard_api" not in text
            assert "require(\"dashboard" not in text

    def test_all_buttons_are_type_button(self, html_text):
        assert html_text.count('type="button"') >= 3
        assert 'type="submit"' not in html_text


# ── 3. 只读文案 / 空态 / 不可用显式显示 ──────────────────────────────
class TestReadonlyCopy:
    def test_html_declares_readonly_boundaries(self, html_text):
        for marker in ("只读", "不下单", "不迁移两账", "不写服务端",
                       "不调 POST", "不造数", "unavailable", "null",
                       "显式显示"):
            assert marker in html_text, marker

    def test_js_declares_readonly_boundaries(self, js_text):
        for marker in ("只读", "不下单", "不迁移两账", "不写服务端",
                       "不造数", "不猜"):
            assert marker in js_text, marker

    def test_empty_and_unavailable_copies(self, contract, html_text):
        text = contract["text"]
        assert "暂无账户" in text["emptyAccounts"]
        assert "尚无快照" in text["emptySnapshot"]
        assert "持仓为空" in text["emptyPositions"]
        assert "端点未接入" in text["endpointMissing"]
        assert "接口不可用" in text["networkFail"]
        assert "非 JSON" in text["nonJson"]
        assert "未验证" in text["tenantUnverified"]
        assert "不猜" in text["attrMissing"]
        # HTML 静态空态文案同样显式（首屏未加载时也不空白）
        assert "暂无账户" in html_text
        assert "尚无快照" in html_text

    def test_h2_count_below_shortscreen_threshold(self, html_text):
        # AGENTS §2：h2 >= 6 必须过 shortscreen.js；本页 h2=4，暂不需要
        assert html_text.count("<h2") == 4

    def test_footer_readonly_notice(self, html_text):
        assert "只读边界" in html_text
        assert "不写服务端" in html_text
        assert "不迁移两账" in html_text


# ── 4. DOM 结构 / id 契约 ────────────────────────────────────────────
class TestDomStructure:
    def test_required_element_ids(self, html_text):
        for element_id in ("sourceBadge", "btnRefresh", "healthBooks",
                           "boundaryChips", "healthStatus", "healthEmpty",
                           "bookFilter", "btnLoadAccounts", "accountsList",
                           "accountsNote", "accountsEmpty", "selBook",
                           "inpTenant", "inpAccount", "btnLoadSnapshot",
                           "snapshotStatus", "snapshotBody", "snapshotEmpty",
                           "routeStatus", "routeBody"):
            assert ('id="%s"' % element_id) in html_text, element_id

    def test_js_dom_ids_exist_in_html(self, html_text, js_text):
        js_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js_text))
        js_ids |= set(re.findall(
            r'getElementById\("([A-Za-z0-9_-]+)"\)', js_text))
        html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html_text))
        missing = sorted(js_ids - html_ids)
        assert not missing, "JS 引用了 HTML 中不存在的元素 id: %s" % missing

    def test_health_and_snapshot_sections_present(self, html_text):
        assert "健康卡" in html_text
        assert "账户目录" in html_text
        assert "账户快照" in html_text
        assert "路由详情" in html_text
        assert "official" in html_text and "legacy" in html_text

    def test_no_inline_event_handlers(self, html_text):
        assert "onclick=" not in html_text
        assert "onload=" not in html_text


# ── 5. Node 契约（纯函数 / node --check / 不落盘）─────────────────────
class TestNodeContract:
    def test_pure_helpers(self, contract):
        assert contract["boot_type"] == "function"
        assert contract["boundary"] == ["只读", "不下单", "不迁移两账"]
        assert contract["book_order"] == ["official", "legacy"]
        assert "new_port" in contract["write_keys"]
        assert "start_service" in contract["write_keys"]
        assert contract["display"] == ["null", "null", "x", "0"]
        assert contract["query"] == "?a=x%20y&b=2"
        assert contract["snapshot_query"] == "?account=main&book=legacy&tenant=t1"
        assert contract["parse_bad"] is None
        assert contract["parse_good"] == {"ok": True}
        assert contract["status"] == ["正常", "降级（部分数据缺失）",
                                      "空账（合法空态）", "不可用", "未知状态"]

    def test_http_error_descriptions(self, contract):
        assert "端点未接入" in contract["http_404"]
        assert "接口不可用" in contract["http_0"]
        assert "HTTP 400" in contract["http_400"]
        assert "TenantValidationError" in contract["http_400"]

    def test_attribution_note_never_guesses(self, contract):
        note = contract["attr"]
        assert "tenant 归属 null（不猜）" in note
        assert "存储层无 tenant 维度" in note

    def test_summarize_health(self, contract):
        health = contract["health"]
        assert health["status"] == "ok" and health["statusLabel"] == "正常"
        assert [book["book"] for book in health["books"]] == \
            ["official", "legacy"]
        assert health["books"][1]["status"] == "unavailable"
        assert health["books"][1]["reasons"] == ["legacy paper.db 不存在"]
        assert health["boundaries"]["ledger_write"] is False
        assert contract["health_bad"] is None

    def test_summarize_accounts(self, contract):
        accounts = contract["accounts"]
        assert accounts["count"] == 2
        assert [book["book"] for book in accounts["books"]] == \
            ["official", "legacy"]
        assert accounts["books"][0]["storageAccounts"] is None
        legacy = accounts["books"][1]
        assert len(legacy["entries"]) == 1
        entry = legacy["entries"][0]
        assert entry["account_id"] == "main"
        assert entry["tenant_id"] is None  # 归属未落库 → null（不猜）
        assert entry["namespace"] is None
        assert entry["meta"]["cash"] == 500000.0

    def test_summarize_snapshot_official(self, contract):
        snap = contract["snap_official"]
        assert snap["status"] == "ok" and snap["book"] == "official"
        assert snap["namespace"] == "sim/official/t1/a1"
        assert snap["accountId"] == "a1"
        assert snap["rows"] == 2 and snap["lastDate"] == "2026-10-30"
        assert len(snap["positions"]) == 1
        assert snap["positions"][0]["symbol"] == "600519"
        assert "tenant 归属 null（不猜）" in snap["attributionNote"]

    def test_summarize_snapshot_legacy_unavailable(self, contract):
        snap = contract["snap_legacy"]
        assert snap["status"] == "unavailable"
        assert snap["statusLabel"] == "不可用"
        assert snap["unavailable"]["code"] == "legacy_db_missing"
        assert snap["warnings"] == ["没有造数"]
        assert contract["snap_bad"] is None

    def test_node_check_js(self):
        _require_node()
        result = subprocess.run(
            [NODE_BIN, "--check", JS_PATH],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""

    def test_node_require_does_not_write_files(self, tmp_path):
        _require_node()
        script = ("const api = require(%s);"
                  "console.log(typeof api.boot);" % json.dumps(JS_PATH))
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == "function"
        assert os.listdir(str(tmp_path)) == [], "Node 载入只读页面 JS 不得落盘"


# ── 6. 只读边界：不改 dashboard_api / gateway / facade / 两账 ────────
class TestReadonlyBoundary:
    def test_boundary_files_unchanged_by_contract_run(self):
        paths = [path for path in (
            DASHBOARD_API_PATH,
            GATEWAY_PATH,
            FACADE_PATH,
            TENANT_MODEL_PATH,
            PAPER_BOOK_PATH,
            RISK_GATE_PATH,
            OFFICIAL_LEDGER,
            LEGACY_DB,
        ) if os.path.exists(path)]
        before = {path: _sha256(path) for path in paths}
        _contract()  # node require + 全部纯函数
        _read(HTML_PATH)
        _read(JS_PATH)
        after = {path: _sha256(path) for path in paths}
        assert before == after, "只读页面/测试不得写入服务端文件或两本账"

    def test_frontend_does_not_reference_other_services(self, html_text,
                                                        js_text):
        for text in (html_text, js_text):
            assert "dashboard_api." not in text
            assert "portal_api" not in text
            assert "paper_ledger.jsonl" not in text
            assert "paper.db" not in text
            assert "/api/" not in text

    def test_page_is_offline_first(self, js_text):
        # 离线（file:// 或端点未接）时走显式不可用文案，不依赖外部资源
        assert "接口不可用" in js_text
        assert "端点未接入" in js_text
        assert "Promise" in js_text
        assert "catch(" in js_text
