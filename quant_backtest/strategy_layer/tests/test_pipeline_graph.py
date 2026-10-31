# -*- coding: utf-8 -*-
"""Phase 1-B Pipeline Graph / pipeline_map 升级测试（WO-PIPELINE-GRAPH-20261031）。

覆盖验收口径：
1) Graph JSON schema：nodes/edges/meta/counts 四键、node/edge 必填字段与取值域、
   node id 唯一、edge 端点引用存在；
2) 节点口径：10 张卡 / 2 enabled（registry）/ 8 shadow 候选；live/shadow/overlay
   与 strategy_cards.registry_view 严格一致；kg_boost 为 registry-only（card=false）；
3) 已提交 pipeline_graph.json 与当前 strategy_cards/registry 源同步（忽略 generated_at）；
4) pipeline_map.html：优先 fetch 同目录 pipeline_graph.json 渲染、fetch 失败保留静态
   fallback、保留现有开关/权重交互、不引入外部依赖；
5) 只读：build_graph/validate_graph/write_graph(显式路径) 不改 cards/registry/manifest/
   页面/已提交 JSON。
"""

import copy
import hashlib
import json
import os
import re

import pytest

from strategy_layer import pipeline_graph as pg
from strategy_layer import strategy_cards as sc

EXPECTED_ENABLED_IDS = ["kg_boost", "chan_monitor"]
EXPECTED_ENABLED_CARD_IDS = ["chan_monitor"]
EXPECTED_SHADOW_IDS = [
    "trend_follow",
    "boll_breakout",
    "rsi_reversal",
    "ma_cross",
    "chanlun_li122",
    "nine_dim",
    "qpzm",
    "rps_resonance",
]
EXPECTED_CARD_IDS = set(EXPECTED_SHADOW_IDS) | {"chan_monitor", "vol_target"}


# ── 工具 ─────────────────────────────────────────────────────────────
def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _readonly_sources():
    """导出器允许只读的全部源文件 + 页面 + 已提交 JSON。"""
    paths = [
        sc.DEFAULT_CARDS_PATH,
        sc.DEFAULT_REGISTRY_PATH,
        os.path.join(os.path.dirname(os.path.abspath(sc.__file__)), "strategy_cards.py"),
        pg.DEFAULT_GRAPH_PATH,
        pg.DEFAULT_PAGE_PATH,
    ]
    for name in sorted(os.listdir(sc.DEFAULT_MANIFESTS_DIR)):
        if name.endswith(".manifest.json"):
            paths.append(os.path.join(sc.DEFAULT_MANIFESTS_DIR, name))
    return paths


def _node_map(graph):
    return {node["id"]: node for node in graph["nodes"]}


def _strategy_nodes(graph):
    return [node for node in graph["nodes"] if node["type"] == "strategy"]


def _card_nodes(graph):
    return [node for node in _strategy_nodes(graph) if node.get("card")]


@pytest.fixture(scope="module")
def graph():
    return pg.build_graph()


@pytest.fixture(scope="module")
def committed():
    return pg.load_graph()


@pytest.fixture(scope="module")
def html_text():
    with open(pg.DEFAULT_PAGE_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


# ── 1. Graph JSON schema / 契约 ───────────────────────────────────────
class TestCommittedGraph:
    def test_file_exists_and_parses(self):
        assert os.path.isfile(pg.DEFAULT_GRAPH_PATH)
        with open(pg.DEFAULT_GRAPH_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        assert raw["counts"]["cards"] == pg.BASELINE_COUNTS["cards"]
        # load_graph 内部走 validate_graph → 不合法会抛 ValueError
        assert pg.load_graph()["counts"]["cards"] == 10

    def test_top_level_contract(self, committed):
        assert set(committed) == {"nodes", "edges", "meta", "counts"}
        assert isinstance(committed["nodes"], list) and committed["nodes"]
        assert isinstance(committed["edges"], list)
        assert isinstance(committed["meta"], dict)
        assert isinstance(committed["counts"], dict)

    def test_meta_contract(self, committed):
        meta = committed["meta"]
        assert meta["schema_version"] == pg.GRAPH_SCHEMA_VERSION
        assert meta["version"] == pg.GRAPH_VERSION
        assert meta["wo"] == "WO-PIPELINE-GRAPH-20261031"
        assert meta["phase"] == "Phase 1-B"
        assert isinstance(meta["generated_at"], str) and meta["generated_at"].strip()
        assert meta["readonly"] is True
        assert meta["generator"].endswith("strategy_layer/pipeline_graph.py")
        for key in ("cards", "registry", "manifests", "cards_module", "page"):
            assert isinstance(meta["source"][key], str) and meta["source"][key].strip()
        assert meta["source"]["cards"].endswith("strategy_layer/strategy_cards.json")
        assert meta["source"]["registry"].endswith("strategy_layer/strategy_registry.json")
        assert [layer["id"] for layer in meta["layers"]] == list(pg.LAYER_IDS)
        assert meta["counts"] == committed["counts"]

    def test_node_schema(self, committed):
        seen = set()
        for node in committed["nodes"]:
            for field in ("id", "type", "label", "status", "layer"):
                assert isinstance(node.get(field), str) and node[field].strip(), (
                    "node 缺字段/空值: %s %r" % (field, node.get("id"))
                )
            assert node["id"] not in seen, "node id 重复: %s" % node["id"]
            seen.add(node["id"])
            assert node["type"] in pg.NODE_TYPES
            assert node["status"] in pg.STATUS_VALUES
            assert node["layer"] in pg.LAYER_IDS
            if node["type"] == "strategy":
                assert isinstance(node.get("strategy_id"), str)
                assert node["id"] == "strategy:" + node["strategy_id"]
                assert node["layer"] == "L3"
                assert isinstance(node.get("card"), bool)
            if "metrics" in node:
                assert isinstance(node["metrics"], dict)
            if node["type"] == "layer":
                assert node["id"] == "layer:" + node["layer"]
        for layer_id in pg.LAYER_IDS:
            assert ("layer:" + layer_id) in seen

    def test_edge_schema_and_references(self, committed):
        node_ids = set(node["id"] for node in committed["nodes"])
        for edge in committed["edges"]:
            for field in ("from", "to", "port", "label"):
                assert isinstance(edge.get(field), str) and edge[field].strip(), (
                    "edge 缺字段: %s %r" % (field, edge)
                )
            assert edge["from"] in node_ids, "edge.from 悬空: %r" % (edge["from"],)
            assert edge["to"] in node_ids, "edge.to 悬空: %r" % (edge["to"],)
            assert edge["port"] in pg.EDGE_PORTS
            if "kind" in edge:
                assert edge["kind"] in pg.EDGE_KINDS

    def test_committed_in_sync_with_sources(self, committed):
        fresh = pg.build_graph()
        diffs = pg.compare_graph(committed, fresh)
        assert diffs == [], (
            "pipeline_graph.json 与 strategy_cards/registry 源漂移（差异键 %s）；"
            "重跑 python -X utf8 strategy_layer/pipeline_graph.py "
            "--out strategy_layer/pipeline_graph.json" % diffs
        )

    def test_build_is_deterministic_with_fixed_now(self):
        left = pg.build_graph(now="2026-10-31T12:00:00+08:00")
        right = pg.build_graph(now="2026-10-31T12:00:00+08:00")
        assert left == right
        assert left["meta"]["generated_at"] == "2026-10-31T12:00:00+08:00"


# ── 2. 节点/计数口径：10 卡 / 2 enabled / 8 shadow ───────────────────
class TestCountsAndStatus:
    def test_baseline_counts_10_2_8(self, committed):
        counts = committed["counts"]
        assert pg.BASELINE_COUNTS == {"cards": 10, "enabled": 2, "shadow": 8}
        assert counts["cards"] == 10
        assert counts["enabled"] == 2
        assert counts["shadow"] == 8
        assert counts["shadow_candidates"] == counts["shadow"] == 8
        assert counts["overlay"] == 1
        assert counts["registry_only_enabled"] == 1

    def test_counts_self_consistent(self, committed):
        nodes, edges, counts = committed["nodes"], committed["edges"], committed["counts"]
        strategy_nodes = _strategy_nodes(committed)
        card_nodes = _card_nodes(committed)
        assert counts["nodes"] == len(nodes)
        assert counts["edges"] == len(edges)
        assert counts["layers"] == 6
        assert counts["layer_nodes"] == 6
        assert counts["cards"] == len(card_nodes)
        assert counts["strategy_nodes"] == len(strategy_nodes) == 11
        assert counts["enabled"] == len(
            [node for node in strategy_nodes if node["status"] == "live"]
        )
        assert counts["shadow"] == len(
            [node for node in card_nodes if node["status"] == "shadow"]
        )
        assert counts["overlay"] == len(
            [node for node in strategy_nodes if node["status"] == "overlay"]
        )
        assert counts["pipeline_nodes"] == len(
            [node for node in nodes if node["type"] == "pipeline"]
        )
        assert counts["slot_nodes"] == len(
            [node for node in nodes if node["type"] == "slot"]
        )
        assert counts["planned"] == len(
            [node for node in nodes if node["status"] == "planned"]
        )

    def test_all_ten_cards_represented(self, committed):
        cards = sc.load_cards()
        card_ids = set(card["strategy_id"] for card in cards)
        graph_card_ids = set(node["strategy_id"] for node in _card_nodes(committed))
        assert graph_card_ids == card_ids == EXPECTED_CARD_IDS
        assert len(graph_card_ids) == 10
        # 每张卡的 label/detail 都来自卡片，非空
        for node in _card_nodes(committed):
            assert node["label"].strip()
            assert node["detail"].strip()
            assert node["metrics"]["version"] == "1.0.0"
            assert isinstance(node["metrics"]["horizon_days"], int)
            assert node["metrics"]["modes"]
            assert node["status"] in ("live", "shadow", "overlay")

    def test_enabled_live_nodes_match_registry(self, committed):
        view = sc.registry_view()
        live_ids = [node["strategy_id"] for node in _strategy_nodes(committed)
                    if node["status"] == "live"]
        assert live_ids == EXPECTED_ENABLED_IDS == view["enabled_ids"]
        assert view["enabled"] == 2
        # enabled 卡只有 chan_monitor；kg_boost 为 registry-only（无 manifest 卡）
        assert [node["strategy_id"] for node in _card_nodes(committed)
                if node["status"] == "live"] == EXPECTED_ENABLED_CARD_IDS
        kg_boost = _node_map(committed)["strategy:kg_boost"]
        assert kg_boost["card"] is False
        assert kg_boost["status"] == "live"
        assert kg_boost["metrics"]["registry_only"] is True
        assert kg_boost["metrics"]["weight"] == 0.5
        assert kg_boost["metrics"]["top_n"] == 50

    def test_shadow_candidates_match_cards_view(self, committed):
        view = sc.registry_view()
        shadow_ids = [node["strategy_id"] for node in _card_nodes(committed)
                      if node["status"] == "shadow"]
        assert shadow_ids == EXPECTED_SHADOW_IDS == view["shadow_candidate_ids"]
        assert view["shadow_candidates"] == 8
        for sid in shadow_ids:
            node = _node_map(committed)["strategy:" + sid]
            assert node["metrics"]["shadow_candidate"] is True
            assert node["metrics"]["enabled"] is False

    def test_non_signal_overlay_vol_target(self, committed):
        view = sc.registry_view()
        overlay_ids = [node["strategy_id"] for node in _strategy_nodes(committed)
                       if node["status"] == "overlay"]
        assert overlay_ids == ["vol_target"] == view["non_signal_overlay_ids"]
        node = _node_map(committed)["strategy:vol_target"]
        assert node["metrics"]["overlay"] is True
        assert node["metrics"]["shadow_candidate"] is False

    def test_layers_and_planned_nodes(self, committed):
        nodes = _node_map(committed)
        for layer_id in pg.LAYER_IDS:
            assert nodes["layer:" + layer_id]["type"] == "layer"
        # 规划/预留：RiskGuard 共享位 + L3 两个预留插槽 = 3
        assert nodes["L4:risk_guard"]["status"] == "planned"
        assert nodes["L3:dual_blade"]["status"] == "planned"
        assert nodes["L3:llm_agent"]["status"] == "planned"
        assert committed["counts"]["planned"] == 3
        # 影子期节点：WeightUpdater
        assert nodes["L5:weight_updater"]["status"] == "shadow"


# ── 3. edges 引用与流向 ──────────────────────────────────────────────
class TestEdges:
    def _edge_set(self, graph):
        return {(edge["from"], edge["to"], edge["port"]) for edge in graph["edges"]}

    def test_layer_forward_chain(self, committed):
        edges = self._edge_set(committed)
        for pair, port in (
            (("layer:L0", "layer:L1"), "rows"),
            (("layer:L1", "layer:L2"), "scores_input"),
            (("layer:L2", "layer:L3"), "signals"),
            (("layer:L3", "layer:L4"), "strategy_scores"),
            (("layer:L4", "layer:L5"), "verdicts"),
        ):
            assert (pair[0], pair[1], port) in edges

    def test_feedback_edge_l5_to_l3(self, committed):
        feedback = [
            edge for edge in committed["edges"]
            if edge["from"] == "layer:L5" and edge["to"] == "layer:L3"
        ]
        assert len(feedback) == 1
        assert feedback[0]["kind"] == "feedback"
        assert feedback[0]["port"] == "feedback"
        assert "EWMA" in feedback[0]["label"]

    def test_live_strategy_has_adapter_and_score_edges(self, committed):
        by_pair = {(edge["from"], edge["to"], edge["port"]) for edge in committed["edges"]}
        for sid in EXPECTED_ENABLED_IDS:
            node_id = "strategy:" + sid
            assert ("layer:L3", node_id, "adapter") in by_pair
            assert (node_id, "layer:L4", "score") in by_pair

    def test_shadow_and_overlay_are_readonly_edges(self, committed):
        outgoing = set(edge["from"] for edge in committed["edges"])
        for sid in EXPECTED_SHADOW_IDS:
            node_id = "strategy:" + sid
            assert node_id not in outgoing, "影子候选不得有下游 score 边: %s" % sid
        assert ("layer:L3", "strategy:vol_target", "overlay") in {
            (edge["from"], edge["to"], edge["port"]) for edge in committed["edges"]
        }
        assert "strategy:vol_target" not in outgoing

    def test_edges_unique(self, committed):
        keys = [
            (edge["from"], edge["to"], edge["port"], edge["label"])
            for edge in committed["edges"]
        ]
        assert len(keys) == len(set(keys)), "edges 存在重复"


# ── 4. validate_graph 校验与拒绝 ─────────────────────────────────────
class TestValidation:
    def test_valid_graph_passes(self, graph):
        assert pg.validate_graph(copy.deepcopy(graph)) == graph

    def test_missing_top_level_key_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad.pop("counts")
        with pytest.raises(ValueError, match="缺顶层键"):
            pg.validate_graph(bad)

    def test_missing_required_node_field_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["nodes"][0].pop("label")
        with pytest.raises(ValueError, match="缺字段"):
            pg.validate_graph(bad)

    def test_illegal_status_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["nodes"][0]["status"] = "running"
        with pytest.raises(ValueError, match="status 非法"):
            pg.validate_graph(bad)

    def test_illegal_layer_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["nodes"][0]["layer"] = "L9"
        with pytest.raises(ValueError, match="layer 非法"):
            pg.validate_graph(bad)

    def test_duplicate_node_id_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["nodes"].append(copy.deepcopy(bad["nodes"][0]))
        with pytest.raises(ValueError, match="node id 重复"):
            pg.validate_graph(bad)

    def test_dangling_edge_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["edges"].append(
            {"from": "layer:L4", "to": "node:ghost", "port": "verdicts", "label": "x"}
        )
        with pytest.raises(ValueError, match="edge.to 引用不存在"):
            pg.validate_graph(bad)

    def test_illegal_edge_port_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["edges"][0]["port"] = "nope"
        with pytest.raises(ValueError, match="edge.port 非法"):
            pg.validate_graph(bad)

    def test_counts_drift_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["counts"]["cards"] = 9
        bad["meta"]["counts"] = dict(bad["counts"])  # 同步镜像后再触发 nodes 自洽校验
        with pytest.raises(ValueError, match="counts.cards"):
            pg.validate_graph(bad)

    def test_meta_counts_mirror_mismatch_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["meta"]["counts"] = dict(bad["meta"]["counts"])
        bad["meta"]["counts"]["nodes"] = 999
        with pytest.raises(ValueError, match="meta.counts"):
            pg.validate_graph(bad)

    def test_missing_layer_node_rejected(self, graph):
        bad = copy.deepcopy(graph)
        bad["nodes"] = [node for node in bad["nodes"] if node["id"] != "layer:L2"]
        with pytest.raises(ValueError, match="缺 layer 节点"):
            pg.validate_graph(bad)

    def test_strategy_id_consistency_rejected(self, graph):
        bad = copy.deepcopy(graph)
        target = next(node for node in bad["nodes"] if node["id"] == "strategy:chan_monitor")
        target["strategy_id"] = "ghost"
        with pytest.raises(ValueError, match="strategy 节点 id"):
            pg.validate_graph(bad)

    def test_readonly_flag_required(self, graph):
        bad = copy.deepcopy(graph)
        bad["meta"]["readonly"] = False
        with pytest.raises(ValueError, match="readonly"):
            pg.validate_graph(bad)


# ── 5. 只读：不改任何源文件/页面/已提交 JSON ─────────────────────────
class TestReadOnly:
    def test_sources_unchanged_by_build_validate_write(self, tmp_path):
        paths = _readonly_sources()
        before = {path: _sha256(path) for path in paths}
        graph = pg.build_graph()
        pg.validate_graph(graph)
        target = tmp_path / "graph_copy.json"
        written = pg.write_graph(graph, str(target))
        assert os.path.abspath(written) == os.path.abspath(str(target))
        assert pg.load_graph(str(target))["counts"]["cards"] == 10
        after = {path: _sha256(path) for path in paths}
        assert before == after, "导出器不得写 cards/registry/manifest/页面/已提交 JSON"

    def test_build_does_not_mutate_injected_cards(self):
        cards = sc.load_cards()
        snapshot = json.dumps(cards, ensure_ascii=False, sort_keys=True)
        graph = pg.build_graph(cards=cards)
        assert graph["counts"]["cards"] == 10
        assert json.dumps(cards, ensure_ascii=False, sort_keys=True) == snapshot

    def test_json_output_is_utf8_json_text(self, graph):
        text = pg.dumps_graph(graph)
        assert text.endswith("\n")
        reparsed = json.loads(text)
        assert reparsed["counts"] == graph["counts"]
        assert "链路" in text or "策略" in text  # ensure_ascii=False 保留中文


# ── 6. pipeline_map.html：Graph 加载 / fallback / 交互保留 ────────────
class TestPipelineMapHtml:
    def test_fetches_same_dir_graph_json(self, html_text):
        assert re.search(r'fetch\(\s*"pipeline_graph\.json"', html_text), (
            "页面必须优先 fetch 同目录 pipeline_graph.json"
        )

    def test_keeps_static_fallback(self, html_text):
        # fetch 失败分支明确回退静态结构与徽标
        assert "静态 fallback" in html_text
        assert "不可达 → 静态 fallback" in html_text
        assert "catch" in html_text
        # 静态 fallback 的 6 个层带与关键节点仍在（fetch 失败可直接展示）
        for layer_id in pg.LAYER_IDS:
            assert 'data-layer="%s"' % layer_id in html_text
        assert html_text.count('class="layer"') == 6
        for marker in (
            "TushareSync",
            "SignalAggregator",
            "dual_blade",
            "llm_agent",
            "反馈回流环",
        ):
            assert marker in html_text

    def test_keeps_existing_toggle_and_weight_interactions(self, html_text):
        assert "markDirty" in html_text
        assert "saveStrat" in html_text
        assert 'id="en-kg_boost"' in html_text
        assert 'id="w-kg_boost"' in html_text
        assert 'id="btn-chan_monitor"' in html_text
        assert "onclick=\"saveStrat('kg_boost')\"" in html_text
        assert "/api/strategies/adjust" in html_text
        assert "loadStrategies" in html_text
        assert "loadKpis" in html_text
        assert 'id="kStrat"' in html_text

    def test_graph_render_contract(self, html_text):
        # 渲染函数 + 结构校验 + counts 口径展示
        for marker in (
            "function renderGraph",
            "function renderStrategyNode",
            "function renderPlainNode",
            "function validateGraphPayload",
            "counts",
            "data-sid",
            "shadow 候选",
            "只读",
        ):
            assert marker in html_text, marker
        # 层间通道用 graph edges 更新标签（含反馈边）
        assert 'data-from="L0" data-to="L1"' in html_text
        assert 'data-from="L5" data-to="L3"' in html_text
        assert 'class="flow rev"' in html_text

    def test_no_new_dependencies(self, html_text):
        assert "<script src=" not in html_text
        assert "@import" not in html_text
        assert not re.search(r'(src|href)\s*=\s*["\']https?://', html_text), (
            "不引入外部静态依赖（页面保持零依赖）"
        )
        # 页面本身仍是单文件内联样式/脚本
        assert "<style>" in html_text and "</script>" in html_text

    def test_graph_badge_and_kpi_present(self, html_text):
        assert 'id="graphMeta"' in html_text
        assert 'id="kCards"' in html_text


# ── 7. CLI：只读自检 / 显式导出 / 同步校验 ───────────────────────────
class TestCli:
    def test_summary_does_not_write(self, capsys):
        before = _sha256(pg.DEFAULT_GRAPH_PATH)
        assert pg.main([]) == 0
        out = capsys.readouterr().out
        assert "cards=10 enabled=2 shadow=8" in out
        assert "readonly=true" in out
        assert _sha256(pg.DEFAULT_GRAPH_PATH) == before

    def test_json_print_does_not_write(self, capsys):
        before = _sha256(pg.DEFAULT_GRAPH_PATH)
        assert pg.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["counts"]["cards"] == 10
        assert payload["counts"]["enabled"] == 2
        assert payload["counts"]["shadow"] == 8
        assert _sha256(pg.DEFAULT_GRAPH_PATH) == before

    def test_out_writes_only_explicit_path(self, tmp_path, capsys):
        target = tmp_path / "pipeline_graph_out.json"
        before = _sha256(pg.DEFAULT_GRAPH_PATH)
        assert pg.main(["--out", str(target)]) == 0
        assert target.is_file()
        assert pg.load_graph(str(target))["counts"]["cards"] == 10
        assert _sha256(pg.DEFAULT_GRAPH_PATH) == before
        assert "wrote" in capsys.readouterr().out

    def test_check_committed_in_sync(self, capsys):
        assert pg.main(["--check"]) == 0
        assert "同步" in capsys.readouterr().out

    def test_check_detects_drift(self, tmp_path, capsys):
        drifted = json.loads(json.dumps(pg.load_graph(), ensure_ascii=False))
        drifted["nodes"][0]["label"] = "被篡改的层节点"
        path = tmp_path / "drifted.json"
        path.write_text(json.dumps(drifted, ensure_ascii=False), encoding="utf-8")
        assert pg.main(["--check", "--graph", str(path)]) == 1
        captured = capsys.readouterr()
        assert "漂移" in captured.err
