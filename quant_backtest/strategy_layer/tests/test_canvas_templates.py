# -*- coding: utf-8 -*-
"""Phase 4-B Canvas 模板下拉与载入测试（WO-CANVAS-TEMPLATES-20261031）。

覆盖验收口径：
1) HTML/JS 含模板控件（下拉 + 载入按钮 + 来源标记），JS 引用 pipeline_templates.json；
2) 模板列表 = pipeline_templates.json 的 3 个（trend_pullback/trend_reversal/nine_dim_chan），
   fetch 失败时内置 3 模板 fallback（不崩）；
3) 载入模板把 IR 的节点/边/header 映射到画布（Node DOM shim 实跑 boot + 点击载入）；
4) 唯一网络 = GET（graph + templates 收敛单点），无 POST/PUT/DELETE/XMLHttpRequest 等；
5) 保留拖拽/连线/本地校验/复制/导出能力（既有交互不回归）；
6) node --check 通过；Node require 不落盘；不改 pipeline_map.html / 模板文件 / cards / IR。
"""

import functools
import json
import os
import re
import shutil
import subprocess

import pytest

from strategy_layer import composition_ir as ci
from strategy_layer import strategy_cards as sc

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER_DIR = os.path.dirname(HERE)
HTML_PATH = os.path.join(LAYER_DIR, "strategy_canvas.html")
JS_PATH = os.path.join(LAYER_DIR, "strategy_canvas.js")
TEMPLATES_PATH = os.path.join(LAYER_DIR, "pipeline_templates.json")
GRAPH_PATH = os.path.join(LAYER_DIR, "pipeline_graph.json")

EXPECTED_TEMPLATE_IDS = ["trend_pullback", "trend_reversal", "nine_dim_chan"]
NODE_BIN = shutil.which("node")

# 只读边界：模板/画布测试前后这些文件必须零变化
READONLY_BOUNDARY_PATHS = (
    TEMPLATES_PATH,
    GRAPH_PATH,
    os.path.join(LAYER_DIR, "composition_ir.py"),
    sc.DEFAULT_CARDS_PATH,
    sc.DEFAULT_REGISTRY_PATH,
)


# ── 工具 ─────────────────────────────────────────────────────────────
def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _sha256(path):
    import hashlib

    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _require_node():
    if not NODE_BIN:
        pytest.skip("node 不可用（跳过 node --check / JS 模板契约测试）")
    return NODE_BIN


def _node_eval(script, cwd=None, timeout=90, env=None):
    result = subprocess.run(
        [_require_node(), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        env=env,
    )
    assert result.returncode == 0, (
        "node 执行失败 rc=%s\nstdout:\n%s\nstderr:\n%s"
        % (result.returncode, result.stdout[-1500:], result.stderr[-3000:])
    )
    return result.stdout


@pytest.fixture(scope="module")
def html_text():
    return _read(HTML_PATH)


@pytest.fixture(scope="module")
def js_text():
    return _read(JS_PATH)


@pytest.fixture(scope="module")
def template_payload():
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def cards():
    return sc.load_cards()


# ── Node 契约 harness：模板解析/选择/转换（纯函数）────────────────────
TEMPLATE_CONTRACT_JS = (
    r"""
const core = require(__JS_PATH__);
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(__TEMPLATES_PATH__, "utf8"));
const graph = JSON.parse(fs.readFileSync(__GRAPH_PATH__, "utf8"));
const strategyIds = (graph.nodes || [])
  .filter(function (node) { return node && node.type === "strategy" && node.card !== false; })
  .map(function (node) { return node.strategy_id; });
const file = core.selectTemplates(payload);
const fallback = core.selectTemplates(null);
const bad = core.selectTemplates({ templates: [] });
const garbage = core.selectTemplates("not-a-library");
const parsed = core.parseTemplatesPayload(payload);
const canvas = {};
function convert(list, bucket) {
  list.forEach(function (template) {
    const conv = core.templateToCanvas(template);
    const ir = core.buildIR({ nodes: conv.nodes, edges: conv.edges }, conv.header);
    const res = core.validateIR(ir, { strategyIds: strategyIds });
    canvas[bucket + ":" + template.template_id] = {
      nodes: conv.nodes.length,
      edges: conv.edges.length,
      header: conv.header,
      ok: res.ok,
      errors: res.errors,
      node_types: conv.nodes.map(function (n) { return n.type; }),
      strategy_ids: conv.nodes.filter(function (n) { return n.type === "strategy"; })
        .map(function (n) { return n.strategy_id; }),
      ir: ir
    };
  });
}
convert(file.templates, "file");
convert(fallback.templates, "fallback");
var badRejects = { empty_nodes: false, null_template: false };
try { core.templateToCanvas({ template_id: "x", ir: { nodes: [], edges: [] } }); } catch (e) { badRejects.empty_nodes = true; }
try { core.templateToCanvas(null); } catch (e) { badRejects.null_template = true; }
console.log(JSON.stringify({
  templates_url: core.TEMPLATES_URL,
  template_schema: core.TEMPLATE_SCHEMA,
  template_ids: core.TEMPLATE_IDS,
  fallback_ids: core.FALLBACK_TEMPLATES.map(function (t) { return t.template_id; }),
  file: {
    source: file.source, from_fallback: file.from_fallback,
    count: file.templates.length, ids: file.templates.map(function (t) { return t.template_id; })
  },
  fallback: {
    source: fallback.source, from_fallback: fallback.from_fallback,
    count: fallback.templates.length, ids: fallback.templates.map(function (t) { return t.template_id; })
  },
  bad: { source: bad.source, from_fallback: bad.from_fallback, count: bad.templates.length,
         ids: bad.templates.map(function (t) { return t.template_id; }) },
  garbage: { source: garbage.source, from_fallback: garbage.from_fallback,
             count: garbage.templates.length,
             ids: garbage.templates.map(function (t) { return t.template_id; }) },
  parsed: { count: parsed.length, ids: parsed.map(function (t) { return t.template_id; }) },
  card_strategy_ids: strategyIds,
  canvas: canvas,
  bad_rejects: badRejects
}));
"""
    .replace("__JS_PATH__", json.dumps(JS_PATH))
    .replace("__TEMPLATES_PATH__", json.dumps(TEMPLATES_PATH))
    .replace("__GRAPH_PATH__", json.dumps(GRAPH_PATH))
)


@functools.lru_cache(maxsize=1)
def _contract():
    return json.loads(_node_eval(TEMPLATE_CONTRACT_JS))


@pytest.fixture(scope="module")
def contract():
    return _contract()


# ── Node DOM-shim harness：boot() + 模板下拉 + 点击载入（端到端）──────
BOOT_SHIM_JS = r"""
__SHIM__
const base = __BASE__;
const graphData = JSON.parse(fs.readFileSync(base + "/pipeline_graph.json", "utf8"));
const templatesData = JSON.parse(fs.readFileSync(base + "/pipeline_templates.json", "utf8"));
const failAll = process.env.QA_FAIL_FETCH === "1";
const fetchCalls = [];
global.fetch = function (url, opts) {
  fetchCalls.push({ url: url, method: (opts || {}).method || "GET" });
  if (failAll) return Promise.reject(new Error("offline-sim"));
  const data = url === "pipeline_templates.json" ? templatesData : graphData;
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(data); } });
};
const elements = new Map();
function getEl(id) { if (!elements.has(id)) { const el = new El(); el.id = id; elements.set(id, el); } return elements.get(id); }
global.document = {
  readyState: "complete",
  documentElement: new El("html"),
  body: new El("body"),
  head: new El("head"),
  createElement: function (tag) { return new El(tag); },
  getElementById: getEl,
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  elementFromPoint: function () { return null; },
  execCommand: function () { return true; }
};
global.navigator = {};
require(base + "/strategy_canvas.js");
setTimeout(function () {
  const fail = [];
  const templateSelect = getEl("templateSelect");
  const templateSource = getEl("templateSource");
  const btnLoadTemplate = getEl("btnLoadTemplate");
  const nodeLayer = getEl("nodeLayer");
  const irPreview = getEl("irPreview");
  if (fetchCalls.length !== 2) fail.push("GET 次数应为 2: " + JSON.stringify(fetchCalls));
  if (fetchCalls.some(function (c) { return c.method !== "GET"; })) fail.push("存在非 GET 请求");
  const urls = fetchCalls.map(function (c) { return c.url; }).sort();
  if (JSON.stringify(urls) !== JSON.stringify(["pipeline_graph.json", "pipeline_templates.json"])) {
    fail.push("GET URL 集合错误: " + JSON.stringify(urls));
  }
  const optionsText = templateSelect.innerHTML;
  const bootNodes = (nodeLayer.innerHTML.match(/class="dag-node/g) || []).length;
  const target = failAll ? "trend_reversal" : "nine_dim_chan";
  templateSelect.value = target;
  btnLoadTemplate.fire("click", {});
  const loadedNodes = (nodeLayer.innerHTML.match(/class="dag-node/g) || []).length;
  var ir = null;
  try { ir = JSON.parse(irPreview.textContent); } catch (e) { fail.push("载入后 IR 预览非法 JSON: " + e.message); }
  console.log(JSON.stringify({
    scenario: failAll ? "fallback" : "file",
    fetch_calls: fetchCalls,
    template_options: optionsText,
    template_source: templateSource.textContent,
    boot_nodes: bootNodes,
    loaded_nodes: loadedNodes,
    loaded_template: target,
    ir: ir,
    internal_fail: fail
  }));
  process.exit(0);
}, 120);
"""

SHIM_DEF = r"""
class ClassList {
  constructor() { this._s = new Set(); }
  add(...c) { c.forEach((x) => this._s.add(x)); }
  remove(...c) { c.forEach((x) => this._s.delete(x)); }
  toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else { on ? this._s.add(c) : this._s.delete(c); } return this._s.has(c); }
  contains(c) { return this._s.has(c); }
}
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase(); this.id = ""; this._text = ""; this._html = "";
    this.style = {}; this.value = ""; this.checked = false; this.children = []; this._attrs = {};
    this._listeners = {}; this.classList = new ClassList();
  }
  set textContent(v) { this._text = v == null ? "" : String(v); }
  get textContent() { return this._text; }
  set innerHTML(v) { this._html = v == null ? "" : String(v); this.children = []; }
  get innerHTML() {
    if (this._html) return this._html;
    return String(this._text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener() {}
  setAttribute(k, v) { this._attrs[k] = String(v); if (k === "id") this.id = v; }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); }
  remove() {} querySelector() { return null; } querySelectorAll() { return []; } closest() { return null; }
  focus() {} select() {} click() {} setPointerCapture() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 600 }; }
  get scrollLeft() { return 0; }
  get scrollTop() { return 0; }
  fire(type, evt) { (this._listeners[type] || []).forEach((fn) => fn(evt || {})); }
}
const fs = require("fs");
"""


def _boot_report(fail_fetch):
    script = BOOT_SHIM_JS.replace("__SHIM__", SHIM_DEF).replace("__BASE__", json.dumps(LAYER_DIR))
    env = dict(os.environ)
    if fail_fetch:
        env["QA_FAIL_FETCH"] = "1"
    else:
        env.pop("QA_FAIL_FETCH", None)
    return json.loads(_node_eval(script, env=env))


@pytest.fixture(scope="module")
def boot_file():
    return _boot_report(fail_fetch=False)


@pytest.fixture(scope="module")
def boot_fallback():
    return _boot_report(fail_fetch=True)


# ── 1. 文件 / HTML 控件 / 既有能力保留 ───────────────────────────────
class TestFilesAndControls:
    def test_html_and_js_exist(self):
        assert os.path.isfile(HTML_PATH) and os.path.isfile(JS_PATH)
        assert os.path.getsize(HTML_PATH) > 8000
        assert os.path.getsize(JS_PATH) > 40000

    def test_template_controls_in_html(self, html_text):
        for element_id in ("templateSelect", "btnLoadTemplate", "templateSource"):
            assert ('id="%s"' % element_id) in html_text, element_id
        assert "模板" in html_text
        assert "pipeline_templates.json" in html_text
        assert "载入模板" in html_text

    def test_template_controls_bound_in_js(self, js_text):
        assert 'TEMPLATES_URL = "pipeline_templates.json"' in js_text
        assert 'TEMPLATE_SCHEMA = "pipeline_templates/v1"' in js_text
        for template_id in EXPECTED_TEMPLATE_IDS:
            assert template_id in js_text
        assert "FALLBACK_TEMPLATES" in js_text
        assert "renderTemplateOptions" in js_text
        assert "loadTemplateById" in js_text
        assert "onLoadTemplateClick" in js_text

    def test_existing_canvas_features_preserved(self, html_text, js_text):
        # 拖拽/连线/删除/本地校验/复制/导出不回归
        assert "pointerdown" in js_text
        assert "onNodePointerDown" in js_text
        assert "canConnect" in js_text
        assert "validateIR" in js_text
        assert "navigator.clipboard" in js_text
        assert "Blob(" in js_text
        for element_id in ("canvasWrap", "nodeLayer", "edgeLayer", "irPreview", "btnValidate", "btnCopy", "btnDownload"):
            assert ('id="%s"' % element_id) in html_text, element_id
        # h2 数量保持 3（不触发 shortscreen 门槛）
        assert html_text.count("<h2") == 3

    def test_no_pipeline_map_modification(self, html_text, js_text):
        assert "pipeline_map" not in html_text
        assert not re.search(r'["\']pipeline_map[^"\']*["\']', js_text)

    def test_readonly_boundary_unchanged(self, template_payload):
        before = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        _contract()  # Node require + 模板解析/转换（只读）
        _boot_report(fail_fetch=False)
        after = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        assert before == after, "模板/画布测试不得写 pipeline_templates/pipeline_graph/cards/registry/composition_ir"


# ── 2. 模板列表 / fallback 契约 ──────────────────────────────────────
class TestTemplateLibrary:
    def test_template_file_has_three_templates(self, template_payload):
        assert [t["template_id"] for t in template_payload["templates"]] == EXPECTED_TEMPLATE_IDS
        assert template_payload["_meta"]["schema"] == "pipeline_templates/v1"
        assert template_payload["_meta"]["ir_schema"] == ci.SCHEMA

    def test_js_template_constants_and_fallback_ids(self, contract):
        assert contract["templates_url"] == "pipeline_templates.json"
        assert contract["template_schema"] == "pipeline_templates/v1"
        assert contract["template_ids"] == EXPECTED_TEMPLATE_IDS
        assert contract["fallback_ids"] == EXPECTED_TEMPLATE_IDS

    def test_file_payload_parsed(self, contract):
        file_info = contract["file"]
        assert file_info["from_fallback"] is False
        assert file_info["source"] == "pipeline_templates.json"
        assert file_info["count"] == 3
        assert file_info["ids"] == EXPECTED_TEMPLATE_IDS
        assert contract["parsed"]["count"] == 3
        assert contract["parsed"]["ids"] == EXPECTED_TEMPLATE_IDS

    def test_fallback_when_payload_missing_or_invalid(self, contract):
        for key in ("fallback", "bad", "garbage"):
            info = contract[key]
            assert info["from_fallback"] is True, key
            assert "内置 fallback" in info["source"], key
            assert info["count"] == 3, key
            assert info["ids"] == EXPECTED_TEMPLATE_IDS, key

    def test_bad_template_rejected_by_converter(self, contract):
        assert contract["bad_rejects"] == {"empty_nodes": True, "null_template": True}


# ── 3. 模板 → 画布 IR 转换（列表/加载）──────────────────────────────
class TestTemplateConversion:
    def _entries(self, contract):
        return sorted(contract["canvas"].items())

    def test_each_template_maps_nodes_edges_header(self, contract, template_payload):
        raw = {t["template_id"]: t["ir"] for t in template_payload["templates"]}
        fallback_counts = {
            "trend_pullback": (6, 5),
            "trend_reversal": (6, 6),
            "nine_dim_chan": (7, 8),
        }
        for template_id in EXPECTED_TEMPLATE_IDS:
            file_entry = contract["canvas"]["file:%s" % template_id]
            assert file_entry["ok"] is True, (template_id, file_entry["errors"])
            assert file_entry["nodes"] == len(raw[template_id]["nodes"])
            assert file_entry["edges"] == len(raw[template_id]["edges"])
            for key in ("pipeline_id", "version", "owner", "tenant", "mode"):
                assert file_entry["header"][key] == raw[template_id][key], (template_id, key)
            assert file_entry["header"]["description"] == raw[template_id].get("description", "")

            fb_entry = contract["canvas"]["fallback:%s" % template_id]
            assert fb_entry["ok"] is True, (template_id, fb_entry["errors"])
            assert (fb_entry["nodes"], fb_entry["edges"]) == fallback_counts[template_id]
            assert fb_entry["header"]["pipeline_id"] == "pc-template-" + template_id.replace("_", "-")
            assert fb_entry["header"]["mode"] in ci.ALLOWED_MODES
            assert fb_entry["header"]["version"] == "1.0.0"

    def test_conversion_ir_passes_authoritative_python(self, contract, cards):
        assert len(contract["canvas"]) == 6  # 3 file + 3 fallback
        for key, entry in self._entries(contract):
            validated = ci.validate_ir(entry["ir"], cards=cards)
            assert validated.pipeline_id == entry["header"]["pipeline_id"]
            assert len(validated.nodes) == entry["nodes"]
            assert len(validated.edges) == entry["edges"]
            assert validated.mode in ci.ALLOWED_MODES

    def test_conversion_keeps_readonly_safety(self, contract):
        for key, entry in self._entries(contract):
            ir = entry["ir"]
            assert ir["publish"]["enabled"] is False
            executions = [node for node in ir["nodes"] if node["type"] == "execution"]
            assert executions, key
            for node in executions:
                assert node["params"].get("dry_run") is True, key
            assert ir["risk_policy"]["max_position_pct"] <= 100

    def test_conversion_node_types_and_strategy_ids(self, contract, cards):
        card_ids = set(card["strategy_id"] for card in cards)
        for key, entry in self._entries(contract):
            for node_type in entry["node_types"]:
                assert node_type in ci.NODE_TYPES, (key, node_type)
            for strategy_id in entry["strategy_ids"]:
                assert strategy_id in card_ids, (key, strategy_id)

    def test_raw_template_irs_are_valid(self, template_payload, cards):
        # Phase 4-A 的模板库是「合法 composition_ir/v1」——只读复核，不改文件
        for template in template_payload["templates"]:
            validated = ci.validate_ir(template["ir"], cards=cards)
            assert validated.pipeline_id == template["ir"]["pipeline_id"]


# ── 4. 唯一网络 GET / 无 POST / 本地导出 ─────────────────────────────
class TestFetchContract:
    def test_single_fetch_call_and_get_only(self, js_text):
        assert js_text.count("fetch(") == 1, "唯一网络出口应只有一个 fetch 调用点"
        snippet = re.search(r"fetch\([^;]+\)", js_text, re.S).group(0)
        assert "GRAPH_URL" in snippet
        assert re.search(r'method\s*:\s*"GET"', snippet)
        assert "TEMPLATES_URL" in js_text
        assert re.search(r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']', js_text) is None

    def test_get_whitelist_guard(self, js_text):
        # 两条只读数据收敛到 getJson 单点，URL 白名单拒绝其他目标
        assert "function getJson" in js_text
        assert "url !== GRAPH_URL && url !== TEMPLATES_URL" in js_text
        assert "非白名单 GET URL" in js_text

    def test_no_write_verbs_or_sockets(self, js_text):
        assert not re.search(r'["\'](POST|PUT|PATCH|DELETE)["\']', js_text)
        for forbidden in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "/api/", "$.ajax", "action="):
            assert forbidden not in js_text, forbidden

    def test_no_ports_or_cdn(self, html_text, js_text):
        for text in (html_text, js_text):
            assert "8734" not in text and "8080" not in text and "127.0.0.1:" not in text
            assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', text)
            assert "cdn." not in text.lower()

    def test_export_copy_stay_local(self, js_text):
        assert "Blob(" in js_text
        assert "URL.createObjectURL" in js_text
        assert "link.download" in js_text
        assert "navigator.clipboard" in js_text
        assert "不写服务端" in js_text


# ── 5. DOM shim 实跑 boot：下拉列表 + 载入到画布（file / fallback）───
class TestBootAndLoad:
    def test_file_scenario_fetches_two_gets_and_lists_templates(self, boot_file):
        assert boot_file["internal_fail"] == []
        assert [call["method"] for call in boot_file["fetch_calls"]] == ["GET", "GET"]
        urls = sorted(call["url"] for call in boot_file["fetch_calls"])
        assert urls == ["pipeline_graph.json", "pipeline_templates.json"]
        for template_id in EXPECTED_TEMPLATE_IDS:
            assert template_id in boot_file["template_options"], template_id
        assert "pipeline_templates.json" in boot_file["template_source"]

    def test_file_scenario_loads_template_into_canvas(self, boot_file, cards):
        assert boot_file["boot_nodes"] == 6  # 启动种子
        assert boot_file["loaded_template"] == "nine_dim_chan"
        assert boot_file["loaded_nodes"] == 9  # 文件模板 nine_dim_chan = 9 节点
        ir = boot_file["ir"]
        assert ir["schema"] == ci.SCHEMA
        assert ir["pipeline_id"] == "pc-template-nine-dim-chan"
        assert ir["mode"] == "SHADOW"
        assert len(ir["nodes"]) == 9
        assert len(ir["edges"]) == 10
        validated = ci.validate_ir(ir, cards=cards)
        assert len(validated.nodes) == 9
        assert len(validated.edges) == 10

    def test_fallback_scenario_lists_and_loads_builtin_templates(self, boot_fallback, cards):
        assert boot_fallback["internal_fail"] == []
        assert [call["method"] for call in boot_fallback["fetch_calls"]] == ["GET", "GET"]
        assert "内置 fallback" in boot_fallback["template_source"]
        for template_id in EXPECTED_TEMPLATE_IDS:
            assert template_id in boot_fallback["template_options"], template_id
        assert boot_fallback["loaded_template"] == "trend_reversal"
        assert boot_fallback["loaded_nodes"] == 6  # 内置 fallback trend_reversal = 6 节点
        ir = boot_fallback["ir"]
        assert ir["pipeline_id"] == "pc-template-trend-reversal"
        assert len(ir["nodes"]) == 6
        assert len(ir["edges"]) == 6
        validated = ci.validate_ir(ir, cards=cards)
        assert len(validated.nodes) == 6


# ── 6. node --check / 零落盘 ─────────────────────────────────────────
class TestNodeChecks:
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
        script = (
            "const core = require(%s); console.log(core.TEMPLATES_URL + '|' + "
            "core.FALLBACK_TEMPLATES.length + '|' + core.templateToCanvas(core.FALLBACK_TEMPLATES[0]).nodes.length);"
            % json.dumps(JS_PATH)
        )
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == "pipeline_templates.json|3|6"
        assert os.listdir(str(tmp_path)) == [], "Node 加载 Canvas 前端不得落盘"
