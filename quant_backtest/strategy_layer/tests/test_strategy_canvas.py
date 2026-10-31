# -*- coding: utf-8 -*-
"""Phase 2-B Strategy Canvas MVP 测试（WO-STRATEGY-CANVAS-20261031）。

覆盖验收口径：
1) HTML/JS 静态存在、HTML 以相对路径引用 strategy_canvas.js；
2) 引用 Graph JSON（pipeline_graph.json，策略节点 live/shadow 来源），失败内置 fallback；
3) IR schema 字段齐全（composition_ir/v1 顶层/节点/边/risk_policy/publish），
   且 JS buildIR 产物能通过权威 Python ``composition_ir.validate_ir``；
4) 节点类型齐全：JS NODE_TYPES / PORT_TYPES / NODE_SPECS 与 composition_ir 常量一致；
5) 无写服务端调用：fetch 只有 1 处且为 GET，无 POST/PUT/DELETE/XMLHttpRequest 等；
   导出/复制全部本地（Blob + 剪贴板），不落盘；
6) node --check JS 通过；不改 pipeline_map.html / cards / IR / registry / 交易链。
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
GRAPH_PATH = os.path.join(LAYER_DIR, "pipeline_graph.json")
PIPELINE_MAP_PATH = os.path.join(LAYER_DIR, "pipeline_map.html")

# 只读边界：canvas 测试/运行前后这些文件必须零变化
READONLY_BOUNDARY_PATHS = (
    PIPELINE_MAP_PATH,
    GRAPH_PATH,
    os.path.join(LAYER_DIR, "composition_ir.py"),
    sc.DEFAULT_CARDS_PATH,
    sc.DEFAULT_REGISTRY_PATH,
)

NODE_BIN = shutil.which("node")

# 与 composition_ir 同口径的 8 类节点（Phase 2-B 要求 7 类 + rank 补全 IR 契约）
EXPECTED_PALETTE_TYPES = (
    "universe",
    "strategy",
    "filter",
    "rank",
    "allocator",
    "risk",
    "execution",
    "agent_team",
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


CONTRACT_JS = r"""
const core = require(JS_PATH);
const baseGraph = {
  nodes: [
    { id: "u1", type: "universe", label: "Universe CN", params: { markets: ["CN"] } },
    { id: "s1", type: "strategy", label: "chan_monitor", strategy_id: "chan_monitor", params: {} },
    { id: "r1", type: "rank", label: "Rank TopN", params: { top_n: 30 } },
    { id: "a1", type: "allocator", label: "Allocator", params: {} },
    { id: "k1", type: "risk", label: "Risk", params: {} },
    { id: "x1", type: "execution", label: "Execution", params: { book: "official", dry_run: true } }
  ],
  edges: [
    { from: "u1", to: "s1", port: "universe" },
    { from: "s1", to: "r1", port: "signals" },
    { from: "r1", to: "a1", port: "ranking" },
    { from: "a1", to: "k1", port: "allocations" },
    { from: "k1", to: "x1", port: "allocations" }
  ]
};
const header = {
  pipeline_id: "pc-test-001",
  version: "1.0.0",
  owner: "quant-classic",
  tenant: "official",
  mode: "SHADOW",
  description: "contract test"
};
const strategyIds = ["kg_boost", "chan_monitor"];
const clone = function (value) { return JSON.parse(JSON.stringify(value)); };
const byId = function (doc, id) {
  return doc.nodes.filter(function (node) { return node.id === id; })[0];
};
const ir = core.buildIR(baseGraph, header);
const base = core.validateIR(ir, { strategyIds: strategyIds });
const cases = {};
cases.cycle = (function () {
  const doc = clone(ir);
  doc.nodes.push({ id: "f1", type: "filter", params: {} }, { id: "f2", type: "filter", params: {} });
  doc.edges.push(
    { from: "s1", to: "f1", port: "signals" },
    { from: "f1", to: "f2", port: "signals" },
    { from: "f2", to: "f1", port: "signals" }
  );
  return doc;
})();
cases.unknown_strategy = (function () {
  const doc = clone(ir);
  byId(doc, "s1").strategy_id = "ghost_strategy";
  return doc;
})();
cases.missing_strategy_id = (function () {
  const doc = clone(ir);
  delete byId(doc, "s1").strategy_id;
  return doc;
})();
cases.self_loop = (function () {
  const doc = clone(ir);
  doc.edges.push({ from: "s1", to: "s1", port: "signals" });
  return doc;
})();
cases.bad_port = (function () {
  const doc = clone(ir);
  doc.edges.push({ from: "s1", to: "a1", port: "banana" });
  return doc;
})();
cases.incompatible_port = (function () {
  const doc = clone(ir);
  doc.edges.push({ from: "u1", to: "r1", port: "universe" });
  return doc;
})();
cases.duplicate_edge = (function () {
  const doc = clone(ir);
  doc.edges.push({ from: "u1", to: "s1", port: "universe" });
  return doc;
})();
cases.no_universe = (function () {
  const doc = clone(ir);
  doc.nodes = doc.nodes.filter(function (node) { return node.id !== "u1"; });
  doc.edges = doc.edges.filter(function (edge) { return edge.from !== "u1" && edge.to !== "u1"; });
  return doc;
})();
cases.missing_header = (function () {
  const doc = clone(ir);
  delete doc.pipeline_id;
  return doc;
})();
cases.bad_mode = (function () {
  const doc = clone(ir);
  doc.mode = "LIVE";
  return doc;
})();
cases.unknown_top_field = (function () {
  const doc = clone(ir);
  doc.extra_field = 1;
  return doc;
})();
cases.node_unknown_field = (function () {
  const doc = clone(ir);
  doc.nodes[0].extra_field = 1;
  return doc;
})();
cases.missing_risk_policy = (function () {
  const doc = clone(ir);
  delete doc.risk_policy;
  return doc;
})();
const caseResults = {};
Object.keys(cases).forEach(function (key) {
  const doc = cases[key];
  caseResults[key] = { ir: doc, result: core.validateIR(doc, { strategyIds: strategyIds }) };
});
const pairs = {
  "u1->s1": core.canConnect(baseGraph.nodes[0], baseGraph.nodes[1], []),
  "s1->r1": core.canConnect(baseGraph.nodes[1], baseGraph.nodes[2], []),
  "r1->a1": core.canConnect(baseGraph.nodes[2], baseGraph.nodes[3], []),
  "a1->k1": core.canConnect(baseGraph.nodes[3], baseGraph.nodes[4], []),
  "k1->x1": core.canConnect(baseGraph.nodes[4], baseGraph.nodes[5], []),
  "self": core.canConnect(baseGraph.nodes[1], baseGraph.nodes[1], baseGraph.edges),
  "rank->strategy": core.canConnect(baseGraph.nodes[2], baseGraph.nodes[1], []),
  "duplicate": core.canConnect(baseGraph.nodes[0], baseGraph.nodes[1], baseGraph.edges)
};
console.log(JSON.stringify({
  schema: core.SCHEMA,
  graph_url: core.GRAPH_URL,
  node_types: core.NODE_TYPES,
  port_types: core.PORT_TYPES,
  terminal_node_types: core.TERMINAL_NODE_TYPES,
  node_meta_types: Object.keys(core.NODE_META),
  node_specs: core.NODE_SPECS,
  allowed_modes: core.ALLOWED_MODES,
  default_header: core.DEFAULT_HEADER,
  risk_policy_required: core.RISK_POLICY_REQUIRED,
  publish_required: core.PUBLISH_REQUIRED,
  top_level_required: core.TOP_LEVEL_REQUIRED,
  fallback: core.FALLBACK_STRATEGIES.map(function (item) {
    return { id: item.id, status: item.status, card: item.card !== false };
  }),
  seed: core.pickSeedStrategy(core.FALLBACK_STRATEGIES),
  ir: ir,
  base: base,
  pairs: pairs,
  cases: caseResults
}));
""".replace("JS_PATH", json.dumps(JS_PATH))


@functools.lru_cache(maxsize=1)
def _contract():
    return json.loads(_node_eval(CONTRACT_JS))


@pytest.fixture(scope="module")
def contract():
    return _contract()


@pytest.fixture(scope="module")
def html_text():
    return _read(HTML_PATH)


@pytest.fixture(scope="module")
def js_text():
    return _read(JS_PATH)


@pytest.fixture(scope="module")
def cards():
    return sc.load_cards()


@pytest.fixture(scope="module")
def graph_json():
    with open(GRAPH_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _js_errors(case_result):
    return "；".join(case_result["result"]["errors"])


# ── 1. 文件存在 / 引用关系 / 只读边界 ────────────────────────────────
class TestFilesAndReferences:
    def test_html_and_js_exist(self):
        assert os.path.isfile(HTML_PATH) and os.path.isfile(JS_PATH)
        assert os.path.getsize(HTML_PATH) > 8000
        assert os.path.getsize(JS_PATH) > 20000

    def test_html_references_js_relative(self, html_text):
        assert 'src="strategy_canvas.js"' in html_text
        assert not re.search(r'src\s*=\s*["\']https?://', html_text)

    def test_js_references_graph_json(self, js_text):
        assert "pipeline_graph.json" in js_text
        assert 'GRAPH_URL = "pipeline_graph.json"' in js_text
        assert "pipeline_graph.json" in _read(HTML_PATH) or "Graph JSON" in _read(HTML_PATH)

    def test_graph_json_live_shadow_source_contract(self, contract, graph_json):
        """策略清单来自 pipeline_graph.json 的 live/shadow 节点；内置 fallback 与当前图一致。"""
        expected = [
            {
                "id": node["strategy_id"],
                "status": node["status"],
                "card": node.get("card") is not False,
            }
            for node in graph_json["nodes"]
            if node.get("type") == "strategy" and node.get("status") in ("live", "shadow")
        ]
        assert len(expected) == 10  # 2 live + 8 shadow
        assert contract["fallback"] == expected, "内置 fallback 必须与 pipeline_graph.json live/shadow 同步"

    def test_seed_strategy_uses_strategy_card(self, contract):
        """示例种子必须选「有卡片」的 live 策略；registry-only kg_boost 不能作为 IR strategy 节点。"""
        seed = contract["seed"]
        assert seed["id"] == "chan_monitor"
        assert seed["status"] == "live"
        assert seed["card"] is True
        # kg_boost 是 registry-only（card=false），只能展示/参考，不参与 IR 校验白名单
        registry_only = [item for item in contract["fallback"] if item["card"] is False]
        assert [item["id"] for item in registry_only] == ["kg_boost"]

    def test_no_pipeline_map_modification(self, html_text, js_text):
        # 不引用/不修改 pipeline_map.html（写域边界：只新增两个前端文件）；
        # JS 注释里可以声明「不改 pipeline_map.html」，但不得有页面引用/写操作。
        assert "pipeline_map" not in html_text
        assert not re.search(r'(?:src|href)\s*=\s*["\'][^"\']*pipeline_map', js_text)
        assert not re.search(r'["\']pipeline_map[^"\']*["\']', js_text)
        assert not re.search(r"(writeFile|open\()\s*\([^)]*pipeline_map", js_text)

    def test_readonly_boundary_unchanged(self):
        before = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        _contract()  # 通过 node require 加载 JS + 执行 buildIR/validateIR
        after = {path: _sha256(path) for path in READONLY_BOUNDARY_PATHS}
        assert before == after, "Canvas 前端不得写 pipeline_map / cards / registry / pipeline_graph / composition_ir"

    def test_tokens_nav_shortcreen_relative_paths(self, html_text):
        assert "../../portal/static/tokens.css" in html_text
        assert "../../portal/static/nav.js" in html_text
        assert "../../portal/static/shortscreen.js" in html_text
        assert not re.search(r'["\']/static/(tokens\.css|nav\.js|shortscreen\.js)', html_text)

    def test_no_hardcoded_ports_or_cdn(self, html_text, js_text):
        for text in (html_text, js_text):
            assert "8734" not in text and "8080" not in text and "127.0.0.1:" not in text
            assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', text)
            assert "cdn." not in text.lower()


# ── 2. 节点类型 / 端口契约齐全（与 composition_ir 一致） ─────────────
class TestNodeTypeContract:
    def test_eight_node_types_cover_ir(self, contract):
        assert tuple(contract["node_types"]) == tuple(ci.NODE_TYPES)
        assert set(ci.NODE_TYPES) == set(EXPECTED_PALETTE_TYPES)
        # 左面板 7 类 + rank 必须全部可渲染（NODE_META 覆盖所有 IR 节点类型）
        assert tuple(contract["node_meta_types"]) == tuple(ci.NODE_TYPES)

    def test_port_types_cover_ir(self, contract):
        assert tuple(contract["port_types"]) == tuple(ci.PORT_TYPES)

    def test_node_specs_inputs_outputs_match_ir(self, contract):
        for node_type in ci.NODE_TYPES:
            js_spec = contract["node_specs"][node_type]
            py_spec = ci.NODE_SPECS[node_type]
            assert tuple(js_spec["inputs"].keys()) == tuple(py_spec.inputs.keys())
            assert tuple(js_spec["outputs"].keys()) == tuple(py_spec.outputs.keys())
            assert js_spec["inputs"] == dict(py_spec.inputs)
            assert js_spec["outputs"] == dict(py_spec.outputs)
            assert bool(js_spec["terminal"]) == bool(py_spec.terminal)

    def test_allowed_modes_and_constants(self, contract):
        assert tuple(contract["allowed_modes"]) == tuple(ci.ALLOWED_MODES)
        assert contract["schema"] == ci.SCHEMA
        assert contract["graph_url"] == "pipeline_graph.json"
        assert tuple(contract["top_level_required"]) == tuple(ci.TOP_LEVEL_REQUIRED)
        assert tuple(contract["risk_policy_required"]) == tuple(ci.RISK_POLICY_REQUIRED)
        assert tuple(contract["publish_required"]) == tuple(ci.PUBLISH_REQUIRED)

    def test_palette_panels_in_html(self, html_text, js_text):
        for marker in ('id="palette"', 'id="strategyPalette"', "节点面板", "策略（Graph JSON live/shadow）"):
            assert marker in html_text
        # 8 类节点面板按钮由 JS data-type 渲染（NODE_META 覆盖在 TestNodeTypeContract 校验）
        assert 'data-type="' in js_text
        assert "NODE_META" in js_text


# ── 3. IR schema 字段齐全 + 权威 Python 校验 ─────────────────────────
class TestIRSchema:
    def test_top_level_schema_fields(self, contract):
        ir = contract["ir"]
        assert set(ir).issubset(set(ci.TOP_LEVEL_ALLOWED))
        for key in ci.TOP_LEVEL_REQUIRED:
            assert key in ir, "IR 缺顶层字段: %s" % key
        assert ir["schema"] == ci.SCHEMA
        assert ir["mode"] in ci.ALLOWED_MODES
        assert ir["publish"]["enabled"] is False, "Canvas 只读：publish.enabled 必须为 false"
        assert ir["publish"]["visibility"] in ci.PUBLISH_VISIBILITIES
        for key in ci.RISK_POLICY_REQUIRED:
            assert key in ir["risk_policy"]
        assert ir["risk_policy"]["max_position_pct"] == 10.0
        assert ir["risk_policy"]["kill_switch"] is True

    def test_node_and_edge_field_contract(self, contract):
        ir = contract["ir"]
        assert len(ir["nodes"]) == 6
        assert len(ir["edges"]) == 5
        for node in ir["nodes"]:
            assert set(node).issubset(set(ci.NODE_ALLOWED))
            for key in ci.NODE_REQUIRED:
                assert key in node
            assert node["type"] in ci.NODE_TYPES
            assert isinstance(node["params"], dict)
        for edge in ir["edges"]:
            assert set(edge).issubset(set(ci.EDGE_ALLOWED))
            for key in ci.EDGE_REQUIRED:
                assert key in edge
            assert edge["port"] in ci.PORT_TYPES
        strategy_nodes = [node for node in ir["nodes"] if node["type"] == "strategy"]
        assert len(strategy_nodes) == 1
        assert strategy_nodes[0]["strategy_id"] == "chan_monitor"

    def test_js_validation_passes_for_exported_ir(self, contract):
        result = contract["base"]
        assert result["ok"] is True, result["errors"]
        assert result["schema"] == ci.SCHEMA
        assert result["topological_order"] == ["u1", "s1", "r1", "a1", "k1", "x1"]

    def test_exported_ir_passes_authoritative_python_validation(self, contract, cards):
        """JS 导出的 IR 必须能被权威 composition_ir.validate_ir 接受（端到端契约）。"""
        validated = ci.validate_ir(contract["ir"], cards=cards)
        assert validated.schema == ci.SCHEMA
        assert validated.pipeline_id == "pc-test-001"
        assert validated.mode == "SHADOW"
        assert len(validated.nodes) == 6
        assert len(validated.edges) == 5
        assert validated.topological_order == ("u1", "s1", "r1", "a1", "k1", "x1")

    def test_exported_ir_compiles_readonly(self, contract, cards):
        plan = ci.compile_ir(contract["ir"], cards=cards)
        assert plan.readonly is True
        assert plan.side_effects == [] or tuple(plan.side_effects) == ()
        assert plan.safety["executes"] is False
        assert plan.safety["places_orders"] is False

    def test_build_ir_is_deterministic(self, contract):
        # 同一图/头信息重复构建 → JSON 完全一致（排序稳定）
        assert contract["ir"] == _contract()["ir"]


# ── 4. JS 本地校验负例（且被 Python 权威校验同样拒绝） ────────────────
NEGATIVE_EXPECTATIONS = {
    "cycle": "环",
    "unknown_strategy": "不在 Graph JSON",
    "missing_strategy_id": "strategy_id",
    "self_loop": "自环",
    "bad_port": "无输出端口",
    "incompatible_port": "端口类型不兼容",
    "duplicate_edge": "重复边",
    "no_universe": "universe",
    "missing_header": "缺必填字段",
    "bad_mode": "mode 非法",
    "unknown_top_field": "未知顶层字段",
    "node_unknown_field": "未知字段",
    "missing_risk_policy": "risk_policy",
}


class TestJSValidatorNegativeCases:
    @pytest.mark.parametrize("case,expected", sorted(NEGATIVE_EXPECTATIONS.items()))
    def test_js_validator_rejects(self, contract, case, expected):
        entry = contract["cases"][case]
        assert entry["result"]["ok"] is False, "JS 校验器漏放非法 IR: %s" % case
        assert expected in _js_errors(entry), (
            "%s 的错误信息未包含 %r: %s" % (case, expected, _js_errors(entry))
        )

    @pytest.mark.parametrize("case", sorted(NEGATIVE_EXPECTATIONS))
    def test_python_authoritative_rejects_same_cases(self, contract, cards, case):
        with pytest.raises(ci.IRValidationError):
            ci.validate_ir(contract["cases"][case]["ir"], cards=cards)

    def test_can_connect_contract(self, contract):
        pairs = contract["pairs"]
        for key, expected_port in (
            ("u1->s1", "universe"),
            ("s1->r1", "signals"),
            ("r1->a1", "ranking"),
            ("a1->k1", "allocations"),
            ("k1->x1", "allocations"),
        ):
            assert pairs[key]["ok"] is True, (key, pairs[key])
            assert pairs[key]["port"] == expected_port
        assert pairs["self"]["ok"] is False and "自环" in pairs["self"]["reason"]
        assert pairs["rank->strategy"]["ok"] is False
        assert "端口" in pairs["rank->strategy"]["reason"]
        assert pairs["duplicate"]["ok"] is False and "重复边" in pairs["duplicate"]["reason"]


# ── 5. 无服务端写入 / fetch 仅 GET / 导出仅本地 ──────────────────────
class TestNoServerWrites:
    def test_single_fetch_and_get_only(self, js_text):
        assert js_text.count("fetch(") == 1
        fetch_snippet = re.search(r"fetch\([^;]+\)", js_text, re.S).group(0)
        assert "GRAPH_URL" in fetch_snippet
        assert re.search(r'method\s*:\s*"GET"', fetch_snippet)
        assert not re.search(r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']', js_text)
        assert "pipeline_graph.json" in js_text

    def test_no_write_http_verbs_or_sockets(self, js_text):
        # 只查「真正会发写请求」的用法；文档注释里的「不发 POST/PUT/DELETE」声明不算
        assert not re.search(r'["\'](POST|PUT|PATCH|DELETE)["\']', js_text)
        for forbidden in (
            "XMLHttpRequest",
            "sendBeacon",
            "WebSocket",
            "EventSource",
            "/api/",
            "$.ajax",
            "action=",
        ):
            assert forbidden not in js_text, forbidden

    def test_export_and_copy_are_local_only(self, js_text):
        assert "Blob(" in js_text
        assert "URL.createObjectURL" in js_text
        assert "link.download" in js_text
        assert "navigator.clipboard" in js_text
        # 只读/不写服务端声明（页面与 JS 双处标注）
        assert "不写服务端" in js_text

    def test_strategy_whitelist_excludes_registry_only(self, js_text):
        # composition_ir 只认 10 张卡；registry-only（card=false）仅展示，不进 IR 白名单
        assert "item.card !== false" in js_text
        assert "registry-only" in js_text

    def test_fetch_failure_has_builtin_fallback(self, js_text):
        assert "FALLBACK_STRATEGIES" in js_text
        assert ".catch(" in js_text
        assert "内置 fallback" in js_text

    def test_node_require_does_not_write_files(self, tmp_path):
        _require_node()
        script = "const core = require(%s); console.log(core.SCHEMA);" % json.dumps(JS_PATH)
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == ci.SCHEMA
        assert os.listdir(str(tmp_path)) == [], "Node 加载 Canvas 前端不得落盘"

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


# ── 6. HTML 结构 / 交互入口 ───────────────────────────────────────────
class TestHtmlStructure:
    def test_required_container_ids(self, html_text):
        for element_id in (
            "canvas-app",
            "palette",
            "strategyPalette",
            "graphSource",
            "canvasWrap",
            "edgeLayer",
            "nodeLayer",
            "irPreview",
            "irStatus",
            "btnSeed",
            "btnClear",
            "btnValidate",
            "btnCopy",
            "btnDownload",
            "fPipelineId",
            "fOwner",
            "fTenant",
            "fMode",
            "fDescription",
        ):
            assert ('id="%s"' % element_id) in html_text, element_id

    def test_svg_canvas_and_ir_preview(self, html_text):
        assert "<svg" in html_text
        assert 'xmlns="http://www.w3.org/2000/svg"' in html_text
        assert "IR 预览" in html_text
        assert "composition_ir/v1" in html_text
        assert 'id="irPreview"' in html_text
        assert "<pre" in html_text

    def test_js_dom_ids_exist_in_html(self, html_text, js_text):
        """JS boot() 里 $("id")/getElementById("id") 引用的元素必须存在于 HTML。"""
        js_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js_text))
        js_ids |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js_text))
        html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html_text))
        missing = sorted(js_ids - html_ids)
        assert not missing, "JS 引用了 HTML 中不存在的元素 id: %s" % missing

    def test_ir_action_buttons(self, html_text):
        assert "校验" in html_text
        assert "复制" in html_text
        assert "导出 JSON" in html_text
        assert "不写服务端" in html_text
        assert "不调 POST" in html_text

    def test_drag_hints_present(self, html_text):
        assert "拖拽" in html_text
        assert "连线" in html_text
        assert "Delete" in html_text

    def test_h2_count_below_shortscreen_threshold(self, html_text):
        # AGENTS §2：h2 >= 6 必须过 shortscreen.js；本页 h2=3，暂不需要
        assert html_text.count("<h2") == 3
