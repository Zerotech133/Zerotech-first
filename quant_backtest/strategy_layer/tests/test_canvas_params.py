# -*- coding: utf-8 -*-
"""Phase 5-C Canvas 模板参数编辑测试（WO-CANVAS-PARAMS-20261031）。

覆盖验收口径：
1) HTML 含参数区控件（tenant / 每策略权重 / max_single / 重置 / 错误提示），载入模板后显示；
2) JS 暴露纯函数 parseTemplateParameters / validateTemplateParams / applyTemplateParams：
   - 默认值来自 pipeline_templates.json 的 parameters（max_single 百分数 → Canvas 归一化 (0,1]）；
   - 权重和 >0 且 ≤1、max_single (0,1]、tenant 合法，非法拒绝并给出提示；
   - 参数应用到 IR：header.tenant、allocator.params.weights/max_single_pct、
     risk_policy.max_position_pct（×100）；产物通过权威 Python composition_ir.validate_ir；
3) DOM shim 实跑 boot + 载入模板 + 参数编辑：IR 预览实时更新（内存）、非法提示、
   重置回默认、网络仍仅 2 条 GET（graph+templates），无 POST；
4) 保留拖拽/连线/本地校验/复制/导出；node --check 通过；
5) 只读：不改 pipeline_templates.json / pipeline_graph.json / cards / registry / composition_ir。
"""

import functools
import hashlib
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
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _require_node():
    if not NODE_BIN:
        pytest.skip("node 不可用（跳过 node --check / JS 参数契约测试）")
    return NODE_BIN


def _node_eval(script, cwd=None, timeout=120, env=None):
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
def graph_json():
    with open(GRAPH_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def cards():
    return sc.load_cards()


# ── Node 契约 harness：参数默认值/校验/应用（纯函数）──────────────────
PARAMS_CONTRACT_JS = (
    r"""
const core = require(__JS_PATH__);
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(__TEMPLATES_PATH__, "utf8"));
const graph = JSON.parse(fs.readFileSync(__GRAPH_PATH__, "utf8"));
const cardStrategyIds = (graph.nodes || [])
  .filter(function (node) { return node && node.type === "strategy" && node.card !== false; })
  .map(function (node) { return node.strategy_id; });

const defaults = {};
const applied = {};
const noMutation = {};
payload.templates.forEach(function (template) {
  const conv = core.templateToCanvas(template);
  const params = conv.params;
  const validation = core.validateTemplateParams(params);
  defaults[template.template_id] = {
    tenant: params.tenant,
    weights: params.weights,
    max_single: params.max_single,
    max_single_pct: params.max_single_pct,
    ok: validation.ok,
    errors: validation.errors,
    weights_sum: validation.weights_sum,
    strategy_ids: core.templateStrategyIds(template)
  };
  /* 输入不被 apply/buildIR 改动 */
  const convSnapshot = JSON.stringify(conv);
  const templateSnapshot = JSON.stringify(template);
  /* 自定义参数：权重减半（和 ∈ (0,1]）、max_single=0.05、tenant=tenant-b */
  const customWeights = {};
  Object.keys(params.weights).forEach(function (sid) {
    customWeights[sid] = Math.round(params.weights[sid] * 50) / 100;
  });
  const custom = { tenant: "tenant-b", weights: customWeights, max_single: 0.05 };
  const customValidation = core.validateTemplateParams(custom);
  const result = core.applyTemplateParams({ nodes: conv.nodes, edges: conv.edges }, conv.header, custom);
  const ir = core.buildIR({ nodes: result.nodes, edges: result.edges }, result.header, result.options);
  const res = core.validateIR(ir, { strategyIds: cardStrategyIds });
  const allocator = ir.nodes.filter(function (node) { return node.type === "allocator"; })[0];
  applied[template.template_id] = {
    custom_ok: customValidation.ok,
    custom_errors: customValidation.errors,
    ok: res.ok,
    errors: res.errors,
    ir: ir,
    tenant: ir.tenant,
    risk_max_position_pct: ir.risk_policy.max_position_pct,
    allocator_weights: allocator ? allocator.params.weights : null,
    allocator_max_single_pct: allocator ? allocator.params.max_single_pct : null,
    publish_enabled: ir.publish.enabled,
    execution_dry_run: (ir.nodes.filter(function (n) { return n.type === "execution"; })[0] || {}).params
  };
  noMutation[template.template_id] = {
    conv_unchanged: JSON.stringify(conv) === convSnapshot,
    template_unchanged: JSON.stringify(template) === templateSnapshot
  };
});

/* 缺 parameters 的模板 → 从 IR 推导默认参数 */
const derived = core.parseTemplateParameters({
  template_id: "derived",
  ir: payload.templates[0].ir
});

/* 校验正/负例 */
const cases = {
  valid_normalized: { tenant: "official", weights: { trend_follow: 0.6, rsi_reversal: 0.4 }, max_single: 0.1 },
  valid_partial: { tenant: "t1", weights: { trend_follow: 0.3 }, max_single: 1 },
  valid_with_zero: { tenant: "t1", weights: { trend_follow: 0.5, rsi_reversal: 0 }, max_single: 0.5 },
  sum_zero: { tenant: "official", weights: { trend_follow: 0 }, max_single: 0.1 },
  sum_too_big: { tenant: "official", weights: { trend_follow: 0.8, rsi_reversal: 0.8 }, max_single: 0.1 },
  negative_weight: { tenant: "official", weights: { trend_follow: -0.1, rsi_reversal: 0.5 }, max_single: 0.1 },
  bad_weight_type: { tenant: "official", weights: { trend_follow: "0.5" }, max_single: 0.1 },
  empty_weights: { tenant: "official", weights: {}, max_single: 0.1 },
  max_zero: { tenant: "official", weights: { trend_follow: 1 }, max_single: 0 },
  max_too_big: { tenant: "official", weights: { trend_follow: 1 }, max_single: 1.5 },
  tenant_empty: { tenant: "", weights: { trend_follow: 1 }, max_single: 0.1 },
  tenant_bad_chars: { tenant: "有空 格", weights: { trend_follow: 1 }, max_single: 0.1 },
  not_dict: null
};
const validation = {};
Object.keys(cases).forEach(function (key) {
  const res = core.validateTemplateParams(cases[key]);
  validation[key] = { ok: res.ok, errors: res.errors, weights_sum: res.weights_sum, max_single: res.max_single };
});

/* 向后兼容：不传 options 的 buildIR 仍用默认 risk_policy（10.0） */
const baseGraph = { nodes: [{ id: "u1", type: "universe", params: { markets: ["CN"] } }, { id: "s1", type: "strategy", strategy_id: "trend_follow", params: {} }], edges: [{ from: "u1", to: "s1", port: "universe" }] };
const plainIR = core.buildIR(baseGraph, { pipeline_id: "pc-bc-001", version: "1.0.0", owner: "quant-classic", tenant: "official", mode: "SHADOW", description: "" });

console.log(JSON.stringify({
  templates_url: core.TEMPLATES_URL,
  defaults: defaults,
  applied: applied,
  no_mutation: noMutation,
  derived: derived,
  validation: validation,
  plain_ir_risk_max: plainIR.risk_policy.max_position_pct,
  plain_ir_tenant: plainIR.tenant
}));
"""
    .replace("__JS_PATH__", json.dumps(JS_PATH))
    .replace("__TEMPLATES_PATH__", json.dumps(TEMPLATES_PATH))
    .replace("__GRAPH_PATH__", json.dumps(GRAPH_PATH))
)


@functools.lru_cache(maxsize=1)
def _contract():
    return json.loads(_node_eval(PARAMS_CONTRACT_JS))


@pytest.fixture(scope="module")
def contract():
    return _contract()


# ── Node DOM-shim harness：boot + 载入模板 + 参数编辑（端到端）────────
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

PARAMS_BOOT_JS = r"""
__SHIM__
const base = __BASE__;
const graphData = JSON.parse(fs.readFileSync(base + "/pipeline_graph.json", "utf8"));
const templatesData = JSON.parse(fs.readFileSync(base + "/pipeline_templates.json", "utf8"));
const fetchCalls = [];
global.fetch = function (url, opts) {
  fetchCalls.push({ url: url, method: (opts || {}).method || "GET" });
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
function previewIR() { return JSON.parse(getEl("irPreview").textContent); }
function allocatorWeights(ir) {
  const alloc = ir.nodes.filter(function (n) { return n.type === "allocator"; })[0];
  return alloc ? alloc.params.weights : null;
}
setTimeout(function () {
  const report = { fetch_calls: fetchCalls, fail: [] };
  const panel = getEl("templateParams");
  const pTenant = getEl("pTenant");
  const pMaxSingle = getEl("pMaxSingle");
  const pErrors = getEl("pErrors");
  const irStatus = getEl("irStatus");
  const btnLoadTemplate = getEl("btnLoadTemplate");
  const templateSelect = getEl("templateSelect");
  const btnResetParams = getEl("btnResetParams");

  report.panel_hidden_before_load = panel.classList.contains("hidden");
  templateSelect.value = "trend_reversal";
  btnLoadTemplate.fire("click", {});
  report.panel_hidden_after_load = panel.classList.contains("hidden");
  report.title = getEl("templateParamsTitle").textContent;
  report.defaults = {
    tenant: pTenant.value,
    max_single: pMaxSingle.value,
    weights: {
      trend_follow: getEl("pw-trend_follow").value,
      rsi_reversal: getEl("pw-rsi_reversal").value
    },
    risk_max: previewIR().risk_policy.max_position_pct,
    allocator_weights: allocatorWeights(previewIR())
  };

  /* 合法参数变更 → IR 实时更新 */
  pTenant.value = "tenant-a"; pTenant.fire("input", {});
  getEl("pw-trend_follow").value = "0.4"; getEl("pw-trend_follow").fire("input", {});
  getEl("pw-rsi_reversal").value = "0.3"; getEl("pw-rsi_reversal").fire("input", {});
  pMaxSingle.value = "0.05"; pMaxSingle.fire("input", {});
  const validIR = previewIR();
  const allocator = validIR.nodes.filter(function (n) { return n.type === "allocator"; })[0];
  report.valid = {
    ir: validIR,
    tenant: validIR.tenant,
    weights: allocatorWeights(validIR),
    risk_max: validIR.risk_policy.max_position_pct,
    allocator_max_single_pct: allocator ? allocator.params.max_single_pct : null,
    panel_note: pErrors.textContent,
    status_err: irStatus.classList.contains("err")
  };

  /* 非法：max_single 越界 */
  pMaxSingle.value = "1.5"; pMaxSingle.fire("input", {});
  report.invalid_max = { note: pErrors.textContent, status_err: irStatus.classList.contains("err") };
  /* 恢复 max_single，再非法：权重和 > 1 */
  pMaxSingle.value = "0.05"; pMaxSingle.fire("input", {});
  getEl("pw-trend_follow").value = "0.8"; getEl("pw-trend_follow").fire("input", {});
  getEl("pw-rsi_reversal").value = "0.8"; getEl("pw-rsi_reversal").fire("input", {});
  report.invalid_sum = { note: pErrors.textContent, status_err: irStatus.classList.contains("err") };

  /* 重置回模板默认 */
  btnResetParams.fire("click", {});
  report.reset = {
    tenant: pTenant.value,
    max_single: pMaxSingle.value,
    weights: {
      trend_follow: getEl("pw-trend_follow").value,
      rsi_reversal: getEl("pw-rsi_reversal").value
    },
    risk_max: previewIR().risk_policy.max_position_pct,
    panel_note: pErrors.textContent
  };
  report.fetch_methods = fetchCalls.map(function (c) { return c.method; });
  report.fetch_urls = fetchCalls.map(function (c) { return c.url; });
  console.log(JSON.stringify(report));
  process.exit(0);
}, 150);
"""


def _boot_report():
    script = PARAMS_BOOT_JS.replace("__SHIM__", SHIM_DEF).replace("__BASE__", json.dumps(LAYER_DIR))
    return json.loads(_node_eval(script))


@pytest.fixture(scope="module")
def boot_report():
    return _boot_report()


# ── 1. HTML 控件 / JS 函数 / 既有能力保留 ───────────────────────────
class TestFilesAndControls:
    def test_html_and_js_exist(self):
        assert os.path.isfile(HTML_PATH) and os.path.isfile(JS_PATH)
        assert os.path.getsize(JS_PATH) > 60000

    def test_param_controls_in_html(self, html_text):
        for element_id in (
            "templateParams",
            "templateParamsTitle",
            "pTenant",
            "pWeights",
            "pWeightsHint",
            "pMaxSingle",
            "pErrors",
            "btnResetParams",
        ):
            assert ('id="%s"' % element_id) in html_text, element_id
        for marker in ("模板参数", "tenant", "权重", "max_single", "重置"):
            assert marker in html_text, marker
        # 参数区初始隐藏（载入模板后显示）
        assert re.search(r'class="tpl-params hidden" id="templateParams"', html_text)

    def test_param_functions_in_js(self, js_text):
        for marker in (
            "function parseTemplateParameters",
            "function validateTemplateParams",
            "function applyTemplateParams",
            "function renderTemplateParams",
            "function onTemplateParamInput",
            "function resetTemplateParams",
            "templateStrategyIds",
        ):
            assert marker in js_text, marker
        # buildIR 支持 options 覆写 risk_policy/publish（参数联动 IR）
        assert "options.risk_policy" in js_text
        assert "max_position_pct" in js_text
        assert "max_single_pct" in js_text

    def test_existing_canvas_features_preserved(self, html_text, js_text):
        assert "pointerdown" in js_text and "onNodePointerDown" in js_text
        assert "canConnect" in js_text and "validateIR" in js_text
        assert "navigator.clipboard" in js_text and "Blob(" in js_text
        for element_id in ("canvasWrap", "nodeLayer", "btnValidate", "btnCopy", "btnDownload", "templateSelect"):
            assert ('id="%s"' % element_id) in html_text, element_id
        assert html_text.count("<h2") == 3

    def test_no_pipeline_map_modification(self, html_text, js_text):
        assert "pipeline_map" not in html_text
        assert not re.search(r'["\']pipeline_map[^"\']*["\']', js_text)

    def test_readonly_boundary_unchanged(self):
        before = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        _contract()
        _boot_report()
        after = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        assert before == after, "参数测试不得写 templates/graph/cards/registry/composition_ir"


# ── 2. 模板参数默认值（来自 pipeline_templates.json）─────────────────
class TestParamDefaults:
    def test_defaults_from_template_parameters(self, contract, template_payload):
        for template in template_payload["templates"]:
            tid = template["template_id"]
            raw = template["parameters"]
            entry = contract["defaults"][tid]
            assert entry["ok"] is True, (tid, entry["errors"])
            assert entry["tenant"] == raw["tenant"]["default"], tid
            assert entry["weights"] == raw["weights"]["default"], tid
            assert entry["max_single"] == pytest.approx(raw["max_single"]["default"] / 100.0), tid
            assert 0 < entry["max_single"] <= 1, tid
            assert entry["weights_sum"] == pytest.approx(sum(raw["weights"]["default"].values())), tid
            assert 0 < entry["weights_sum"] <= 1, tid
            assert entry["strategy_ids"] == list(raw["weights"]["default"].keys()) or set(
                entry["strategy_ids"]
            ) >= set(raw["weights"]["default"].keys())

    def test_defaults_match_template_ir_allocator(self, contract, template_payload):
        raw = {t["template_id"]: t["ir"] for t in template_payload["templates"]}
        for tid, entry in contract["defaults"].items():
            allocator = [n for n in raw[tid]["nodes"] if n["type"] == "allocator"][0]
            assert entry["weights"] == allocator["params"]["weights"], tid
            assert entry["max_single_pct"] == allocator["params"]["max_single_pct"], tid

    def test_derived_when_template_lacks_parameters(self, contract):
        derived = contract["derived"]
        assert derived["tenant"] == "official"
        assert derived["weights"] == {"trend_follow": 1.0}
        assert derived["max_single"] == pytest.approx(0.08)
        assert derived["max_single_pct"] == 8.0

    def test_three_templates_covered(self, contract):
        assert sorted(contract["defaults"]) == sorted(EXPECTED_TEMPLATE_IDS)


# ── 3. 参数校验：合法通过 / 非法拒绝 ─────────────────────────────────
INVALID_EXPECTATIONS = {
    "sum_zero": "权重和必须 > 0",
    "sum_too_big": "权重和必须 ≤ 1",
    "negative_weight": "不得为负数",
    "bad_weight_type": "必须为数值",
    "empty_weights": "非空 dict",
    "max_zero": "max_single 必须 ∈ (0,1]",
    "max_too_big": "max_single 必须 ∈ (0,1]",
    "tenant_empty": "tenant 必须为非空字符串",
    "tenant_bad_chars": "合法标识符",
    "not_dict": "模板参数必须为 dict",
}


class TestParamValidation:
    @pytest.mark.parametrize("case", sorted(INVALID_EXPECTATIONS))
    def test_invalid_params_rejected(self, contract, case):
        entry = contract["validation"][case]
        assert entry["ok"] is False, case
        assert any(INVALID_EXPECTATIONS[case] in error for error in entry["errors"]), (
            case,
            entry["errors"],
        )

    @pytest.mark.parametrize("case", ["valid_normalized", "valid_partial", "valid_with_zero"])
    def test_valid_params_pass(self, contract, case):
        entry = contract["validation"][case]
        assert entry["ok"] is True, (case, entry["errors"])
        assert 0 < entry["weights_sum"] <= 1
        assert 0 < entry["max_single"] <= 1

    def test_weight_sum_exact_boundary(self, contract):
        assert contract["validation"]["valid_normalized"]["weights_sum"] == pytest.approx(1.0)
        assert contract["validation"]["sum_too_big"]["weights_sum"] == pytest.approx(1.6)
        assert contract["validation"]["not_dict"]["errors"] == ["模板参数必须为 dict"]


# ── 4. 参数 → IR 映射（权重/tenant/max_single 实时联动）──────────────
class TestParamToIR:
    def test_custom_params_map_into_ir(self, contract, template_payload):
        raw = {t["template_id"]: t for t in template_payload["templates"]}
        for tid, entry in contract["applied"].items():
            assert entry["custom_ok"] is True, (tid, entry["custom_errors"])
            assert entry["ok"] is True, (tid, entry["errors"])
            assert entry["tenant"] == "tenant-b"
            assert entry["ir"]["tenant"] == "tenant-b"
            assert entry["risk_max_position_pct"] == pytest.approx(5.0)
            assert entry["allocator_max_single_pct"] == pytest.approx(5.0)
            expected_weights = {
                sid: round(value * 50) / 100
                for sid, value in raw[tid]["parameters"]["weights"]["default"].items()
            }
            assert entry["allocator_weights"] == expected_weights, tid
            assert entry["publish_enabled"] is False
            if entry["execution_dry_run"] is not None:
                assert entry["execution_dry_run"].get("dry_run") is True

    def test_param_ir_passes_authoritative_python(self, contract, cards):
        for tid, entry in contract["applied"].items():
            validated = ci.validate_ir(entry["ir"], cards=cards)
            assert validated.pipeline_id == "pc-template-" + tid.replace("_", "-")
            assert validated.tenant == "tenant-b"
            assert validated.risk_policy["max_position_pct"] == pytest.approx(5.0)
            # 权重落位：allocator 节点参数保留
            allocator = [node for node in validated.to_dict()["nodes"] if node["type"] == "allocator"][0]
            assert allocator["params"]["max_single_pct"] == pytest.approx(5.0)

    def test_apply_does_not_mutate_inputs(self, contract):
        for tid, entry in contract["no_mutation"].items():
            assert entry["conv_unchanged"] is True, tid
            assert entry["template_unchanged"] is True, tid

    def test_build_ir_backward_compatible_without_options(self, contract):
        assert contract["plain_ir_risk_max"] == 10.0
        assert contract["plain_ir_tenant"] == "official"


# ── 5. DOM shim 实跑：载入模板 → 参数区 → 编辑 → IR 实时更新 ────────
class TestBootParamEditing:
    def test_param_panel_hidden_until_template_loaded(self, boot_report):
        assert boot_report["panel_hidden_before_load"] is True
        assert boot_report["panel_hidden_after_load"] is False
        assert "trend_reversal" in boot_report["title"]

    def test_defaults_rendered_in_panel(self, boot_report, template_payload):
        raw = [t for t in template_payload["templates"] if t["template_id"] == "trend_reversal"][0]
        defaults = boot_report["defaults"]
        assert defaults["tenant"] == "official"
        assert defaults["max_single"] == str(raw["parameters"]["max_single"]["default"] / 100.0)
        assert defaults["weights"] == {
            "trend_follow": str(raw["parameters"]["weights"]["default"]["trend_follow"]),
            "rsi_reversal": str(raw["parameters"]["weights"]["default"]["rsi_reversal"]),
        }
        assert defaults["risk_max"] == pytest.approx(raw["ir"]["risk_policy"]["max_position_pct"])

    def test_valid_edit_updates_ir_preview(self, boot_report, cards):
        valid = boot_report["valid"]
        assert valid["tenant"] == "tenant-a"
        assert valid["weights"] == {"trend_follow": 0.4, "rsi_reversal": 0.3}
        assert valid["risk_max"] == pytest.approx(5.0)
        assert valid["allocator_max_single_pct"] == pytest.approx(5.0)
        assert valid["status_err"] is False
        # 实时 IR 可被权威 Python 校验（内存态，未导出/未写服务端）
        validated = ci.validate_ir(valid["ir"], cards=cards)
        assert validated.tenant == "tenant-a"
        assert validated.risk_policy["max_position_pct"] == pytest.approx(5.0)

    def test_invalid_max_single_prompted(self, boot_report):
        assert "max_single" in boot_report["invalid_max"]["note"]
        assert boot_report["invalid_max"]["status_err"] is True

    def test_invalid_weight_sum_prompted(self, boot_report):
        assert "权重和" in boot_report["invalid_sum"]["note"]
        assert boot_report["invalid_sum"]["status_err"] is True

    def test_reset_restores_template_defaults(self, boot_report):
        reset = boot_report["reset"]
        assert reset["tenant"] == "official"
        assert reset["max_single"] == "0.1"
        assert reset["weights"] == {"trend_follow": "0.6", "rsi_reversal": "0.4"}
        assert reset["risk_max"] == pytest.approx(10.0)

    def test_param_edits_do_not_add_network_calls(self, boot_report):
        assert boot_report["fetch_methods"] == ["GET", "GET"]
        assert sorted(boot_report["fetch_urls"]) == ["pipeline_graph.json", "pipeline_templates.json"]


# ── 6. 唯一 GET / 无 POST / 本地导出保留 ────────────────────────────
class TestFetchContract:
    def test_single_fetch_call_and_get_only(self, js_text):
        assert js_text.count("fetch(") == 1, "唯一网络出口应只有一个 fetch 调用点"
        snippet = re.search(r"fetch\([^;]+\)", js_text, re.S).group(0)
        assert "GRAPH_URL" in snippet
        assert re.search(r'method\s*:\s*"GET"', snippet)
        assert "TEMPLATES_URL" in js_text
        assert re.search(r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']', js_text) is None

    def test_get_whitelist_guard(self, js_text):
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


# ── 7. node --check / 零落盘 ────────────────────────────────────────
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
            "const core = require(%s);"
            " const p = core.parseTemplateParameters(core.FALLBACK_TEMPLATES[0]);"
            " const r = core.validateTemplateParams(p);"
            " console.log(core.TEMPLATES_URL + '|' + r.ok + '|' + p.max_single);"
            % json.dumps(JS_PATH)
        )
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == "pipeline_templates.json|true|0.08"
        assert os.listdir(str(tmp_path)) == [], "Node 加载 Canvas 前端不得落盘"
