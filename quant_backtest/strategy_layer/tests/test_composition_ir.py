# -*- coding: utf-8 -*-
"""composition_ir 单测（WO-COMPOSITION-IR-20261031，Phase 1-A）。

覆盖验收口径：
1) 合法 Composition IR：校验通过、类型化回读、拓扑序确定性；
2) 未知策略 / 未知节点类型 / 重复 id / 环 / 错端口 / 缺字段 / 版本错拒绝；
3) 拓扑排序正确且确定性（同层按 id 字典序）；
4) compile_ir 输出 CompiledPipeline：解析 Strategy Card、参数默认值合并 override、
   端口落地 in_port、只读安全开关全 false；
5) 只读：卡片/manifest/registry/aggregator 源文件字节不变；模块源码无写调用；
   编译不落任何文件；
6) CLI：--file/--cards-file/--json/--validate-only；非法 IR rc=1（closed-on-failure）、
   用法/IO 错 rc=2、合法 rc=0；脚本直跑路径可用。

全程离线：只用 strategy_cards 只读 overlay + tmp_path 临时 IR JSON，不连 PG、
不写生产数据、不新增任务、不重启服务。
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from strategy_layer import composition_ir as ci
from strategy_layer import strategy_cards as sc

_MODULE = os.path.abspath(ci.__file__)
_QB_ROOT = os.path.dirname(os.path.dirname(_MODULE))  # quant_backtest/
_STRATEGY_LAYER = os.path.dirname(_MODULE)


# ── 合成 IR 工厂 ──────────────────────────────────────────────────────
def _base_ir():
    """基线合法 IR：universe→2 strategy→filter→rank→allocator→risk→execution
    ＋ agent_team 观察节点；端口全走 signals/ranking/allocations 契约。"""
    return {
        "schema": ci.SCHEMA,
        "pipeline_id": "pc-cn-shadow-001",
        "version": "1.0.0",
        "owner": "quant-classic",
        "tenant": "official",
        "mode": "SHADOW",
        "description": "组合 IR 单测基线（CN 影子）",
        "nodes": [
            {"id": "u_cn", "type": "universe", "params": {"markets": ["CN"]}},
            {
                "id": "s_chan",
                "type": "strategy",
                "strategy_id": "chan_monitor",
                "params": {},
            },
            {
                "id": "s_trend",
                "type": "strategy",
                "strategy_id": "trend_follow",
                "params": {"n": 30},
            },
            {"id": "f_liquid", "type": "filter", "params": {"min_amount": 50000000}},
            {"id": "r_top", "type": "rank", "params": {"top_n": 30}},
            {"id": "a_eq", "type": "allocator", "params": {"scheme": "equal_weight"}},
            {"id": "k_guard", "type": "risk", "params": {}},
            {
                "id": "x_paper",
                "type": "execution",
                "params": {"book": "official", "dry_run": True},
            },
            {
                "id": "t_observer",
                "type": "agent_team",
                "params": {"team": ["策略组"], "role": "observer"},
            },
        ],
        "edges": [
            {"from": "u_cn", "to": "s_chan", "port": "universe"},
            {"from": "u_cn", "to": "s_trend", "port": "universe"},
            {"from": "s_chan", "to": "f_liquid", "port": "signals"},
            {"from": "s_trend", "to": "f_liquid", "port": "signals"},
            {"from": "f_liquid", "to": "r_top", "port": "signals"},
            {"from": "r_top", "to": "a_eq", "port": "ranking"},
            {"from": "a_eq", "to": "k_guard", "port": "allocations"},
            {"from": "k_guard", "to": "x_paper", "port": "allocations"},
            {"from": "r_top", "to": "t_observer", "port": "ranking"},
        ],
        "risk_policy": {
            "policy_id": "rp-default",
            "max_position_pct": 10.0,
            "max_positions": 30,
            "max_gross_exposure_pct": 100.0,
            "stop_loss_pct": 8.0,
            "kill_switch": True,
            "notes": "只读计划默认风控",
        },
        "publish": {
            "enabled": False,
            "channel": "paper",
            "visibility": "private",
            "requires_approval": True,
        },
    }


def _ir(**overrides):
    """基线 IR + 顶层覆盖（深拷贝，测试间互不污染）。"""
    doc = copy.deepcopy(_base_ir())
    doc.update(copy.deepcopy(overrides))
    return doc


def _node(doc, node_id):
    return next(node for node in doc["nodes"] if node["id"] == node_id)


def _find_edge(doc, from_id, to_id):
    return next(
        edge
        for edge in doc["edges"]
        if edge["from"] == from_id and edge["to"] == to_id
    )


def _write_ir(tmp_path, doc, name="ir.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _all_readonly_sources():
    """本单声明不碰的只读源：卡片 overlay / manifest / registry / aggregator / 本模块。"""
    paths = [
        sc.DEFAULT_CARDS_PATH,
        sc.DEFAULT_REGISTRY_PATH,
        os.path.join(_STRATEGY_LAYER, "aggregator.py"),
        _MODULE,
    ]
    for name in sorted(os.listdir(sc.DEFAULT_MANIFESTS_DIR)):
        if name.endswith(".manifest.json"):
            paths.append(os.path.join(sc.DEFAULT_MANIFESTS_DIR, name))
    return paths


@pytest.fixture(scope="module")
def cards():
    """真实 10 张 Strategy Card（只读，模块级缓存一次）。"""
    return sc.load_cards()


@pytest.fixture()
def valid_ir():
    return _base_ir()


# ── 1. 合法 IR ───────────────────────────────────────────────────────
class TestValidIr:
    def test_validate_returns_typed_ir(self, cards, valid_ir):
        cir = ci.validate_ir(valid_ir, cards=cards)
        assert isinstance(cir, ci.CompositionIR)
        assert cir.pipeline_id == "pc-cn-shadow-001"
        assert cir.version == "1.0.0"
        assert cir.owner == "quant-classic"
        assert cir.tenant == "official"
        assert cir.mode == "SHADOW"
        assert cir.schema == ci.SCHEMA
        assert len(cir.nodes) == 9
        assert len(cir.edges) == 9
        assert [node.id for node in cir.nodes] == [n["id"] for n in valid_ir["nodes"]]
        assert sum(1 for node in cir.nodes if node.type == "strategy") == 2

    def test_to_dict_roundtrip(self, cards, valid_ir):
        cir = ci.validate_ir(valid_ir, cards=cards)
        assert cir.to_dict() == valid_ir
        again = ci.validate_ir(cir, cards=cards)
        assert again.to_dict() == cir.to_dict()
        assert again.topological_order == cir.topological_order

    def test_validate_rejects_non_mapping(self, cards):
        with pytest.raises(ci.IRValidationError, match="顶层必须为 dict"):
            ci.validate_ir(["not", "a", "mapping"], cards=cards)

    def test_missing_schema_defaults(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc.pop("schema")
        cir = ci.validate_ir(doc, cards=cards)
        assert cir.schema == ci.SCHEMA

    def test_default_cards_source(self):
        plan = ci.compile_ir(_base_ir())  # 不注入卡片：走 strategy_cards.load_cards()
        assert plan.sources["cards"].endswith("strategy_cards.json")
        assert plan.sources["manifests"].endswith(os.path.join("strategies", "manifests"))
        # chan_monitor→artifacts.chanlun_pool + trend_follow→bars.daily，只读并集
        assert plan.data_deps == ("artifacts.chanlun_pool", "bars.daily")


# ── 2. 未知策略 / 未知类型 / 未知参数 ─────────────────────────────────
class TestRejectUnknown:
    def test_unknown_strategy_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_chan")["strategy_id"] = "ghost_strategy"
        with pytest.raises(ci.IRValidationError, match="未知策略"):
            ci.validate_ir(doc, cards=cards)

    def test_unknown_node_type_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "r_top")["type"] = "selector"
        with pytest.raises(ci.IRValidationError, match="未知节点类型"):
            ci.validate_ir(doc, cards=cards)

    def test_unknown_node_field_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["nodes"][0]["shape"] = "circle"
        with pytest.raises(ci.IRValidationError, match="未知字段"):
            ci.validate_ir(doc, cards=cards)

    def test_unknown_unknown_param_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_trend")["params"] = {"n": 20, "not_a_param": 1}
        with pytest.raises(ci.IRValidationError, match="不在策略卡"):
            ci.validate_ir(doc, cards=cards)

    def test_param_type_mismatch_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_trend")["params"] = {"n": "30"}
        with pytest.raises(ci.IRValidationError, match="类型非法"):
            ci.validate_ir(doc, cards=cards)

    def test_enum_param_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_chan")["strategy_id"] = "chanlun_li122"
        _node(doc, "s_chan")["params"] = {"ruleset_key": "Z_NOT_A_RULESET"}
        with pytest.raises(ci.IRValidationError, match="enum"):
            ci.validate_ir(doc, cards=cards)

    def test_strategy_id_on_non_strategy_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "f_liquid")["strategy_id"] = "trend_follow"
        with pytest.raises(ci.IRValidationError, match="不得带 strategy_id"):
            ci.validate_ir(doc, cards=cards)


# ── 3. id / 边 / 端口 ────────────────────────────────────────────────
class TestRejectGraph:
    def test_duplicate_node_id_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["nodes"].append({"id": "u_cn", "type": "universe", "params": {}})
        with pytest.raises(ci.IRValidationError, match="节点 id 重复"):
            ci.validate_ir(doc, cards=cards)

    def test_duplicate_strategy_node_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["nodes"].append(
            {
                "id": "s_chan2",
                "type": "strategy",
                "strategy_id": "chan_monitor",
                "params": {},
            }
        )
        doc["edges"].append({"from": "u_cn", "to": "s_chan2", "port": "universe"})
        doc["edges"].append({"from": "s_chan2", "to": "f_liquid", "port": "signals"})
        with pytest.raises(ci.IRValidationError, match="重复 strategy_id 节点"):
            ci.validate_ir(doc, cards=cards)

    def test_self_loop_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["edges"].append({"from": "f_liquid", "to": "f_liquid", "port": "signals"})
        with pytest.raises(ci.IRValidationError, match="自环"):
            ci.validate_ir(doc, cards=cards)

    def test_duplicate_edge_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["edges"].append({"from": "s_chan", "to": "f_liquid", "port": "signals"})
        with pytest.raises(ci.IRValidationError, match="重复边"):
            ci.validate_ir(doc, cards=cards)

    def test_illegal_port_name_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _find_edge(doc, "u_cn", "s_chan")["port"] = "signals"
        with pytest.raises(ci.IRValidationError, match="端口非法"):
            ci.validate_ir(doc, cards=cards)

    def test_incompatible_port_type_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["edges"].append({"from": "r_top", "to": "x_paper", "port": "ranking"})
        with pytest.raises(ci.IRValidationError, match="端口类型不兼容"):
            ci.validate_ir(doc, cards=cards)

    def test_missing_edge_endpoint_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _find_edge(doc, "r_top", "a_eq")["to"] = "a_ghost"
        with pytest.raises(ci.IRValidationError, match="to 节点不存在"):
            ci.validate_ir(doc, cards=cards)

    def test_missing_edge_port_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        edge = _find_edge(doc, "r_top", "a_eq")
        edge.pop("port")
        with pytest.raises(ci.IRValidationError, match="缺必填字段: port"):
            ci.validate_ir(doc, cards=cards)

    def test_cycle_rejected(self, cards):
        doc = _ir(
            nodes=[
                {"id": "u", "type": "universe", "params": {}},
                {
                    "id": "s",
                    "type": "strategy",
                    "strategy_id": "chan_monitor",
                    "params": {},
                },
                {"id": "f1", "type": "filter", "params": {}},
                {"id": "f2", "type": "filter", "params": {}},
            ],
            edges=[
                {"from": "u", "to": "s", "port": "universe"},
                {"from": "s", "to": "f1", "port": "signals"},
                {"from": "f1", "to": "f2", "port": "signals"},
                {"from": "f2", "to": "f1", "port": "signals"},
            ],
        )
        with pytest.raises(ci.IRValidationError, match="环"):
            ci.validate_ir(doc, cards=cards)

    def test_structure_requires_universe_and_strategy(self, cards):
        doc = _ir(
            nodes=[{"id": "x", "type": "execution", "params": {}}],
            edges=[{"from": "x", "to": "x", "port": "orders"}],
        )
        with pytest.raises(ci.IRValidationError, match="universe|strategy"):
            ci.validate_ir(doc, cards=cards)


# ── 4. 必填 / 版本 / 取值域 ──────────────────────────────────────────
class TestRejectSchema:
    def test_missing_top_level_field(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc.pop("risk_policy")
        with pytest.raises(ci.IRValidationError, match="缺必填字段"):
            ci.validate_ir(doc, cards=cards)

    def test_missing_strategy_id(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_chan").pop("strategy_id")
        with pytest.raises(ci.IRValidationError, match="缺 strategy_id"):
            ci.validate_ir(doc, cards=cards)

    def test_bad_version_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["version"] = "1.0"
        with pytest.raises(ci.IRValidationError, match="version 必须"):
            ci.validate_ir(doc, cards=cards)

    def test_unsupported_schema_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["schema"] = "composition_ir/v2"
        with pytest.raises(ci.IRValidationError, match="schema 版本"):
            ci.validate_ir(doc, cards=cards)

    def test_bad_mode_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["mode"] = "LIVE"
        with pytest.raises(ci.IRValidationError, match="mode 非法"):
            ci.validate_ir(doc, cards=cards)

    def test_bad_tenant_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["tenant"] = "official/../escape"
        with pytest.raises(ci.IRValidationError, match="tenant"):
            ci.validate_ir(doc, cards=cards)

    def test_risk_policy_missing_field(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["risk_policy"].pop("max_position_pct")
        with pytest.raises(ci.IRValidationError, match="risk_policy 缺必填字段"):
            ci.validate_ir(doc, cards=cards)

    def test_risk_policy_out_of_range(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["risk_policy"]["max_position_pct"] = 0
        with pytest.raises(ci.IRValidationError, match="max_position_pct"):
            ci.validate_ir(doc, cards=cards)

    def test_publish_enabled_requires_channel(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["publish"] = {"enabled": True}
        with pytest.raises(ci.IRValidationError, match="channel"):
            ci.validate_ir(doc, cards=cards)

    def test_execution_dry_run_false_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "x_paper")["params"]["dry_run"] = False
        with pytest.raises(ci.IRValidationError, match="dry_run"):
            ci.validate_ir(doc, cards=cards)

    def test_execution_live_flag_rejected(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "x_paper")["params"]["live_trading"] = True
        with pytest.raises(ci.IRValidationError, match="实盘"):
            ci.validate_ir(doc, cards=cards)


# ── 5. 拓扑排序 ──────────────────────────────────────────────────────
class TestTopologicalOrder:
    @staticmethod
    def _topo_ir():
        return _ir(
            nodes=[
                {"id": "z_universe", "type": "universe", "params": {}},
                {
                    "id": "c_chan_monitor",
                    "type": "strategy",
                    "strategy_id": "chan_monitor",
                    "params": {},
                },
                {
                    "id": "b_nine_dim",
                    "type": "strategy",
                    "strategy_id": "nine_dim",
                    "params": {},
                },
                {"id": "m_rank", "type": "rank", "params": {}},
                {"id": "a_alloc", "type": "allocator", "params": {}},
                {"id": "k_risk", "type": "risk", "params": {}},
                {"id": "y_exec", "type": "execution", "params": {"dry_run": True}},
            ],
            edges=[
                {"from": "z_universe", "to": "c_chan_monitor", "port": "universe"},
                {"from": "z_universe", "to": "b_nine_dim", "port": "universe"},
                {"from": "c_chan_monitor", "to": "m_rank", "port": "signals"},
                {"from": "b_nine_dim", "to": "m_rank", "port": "signals"},
                {"from": "m_rank", "to": "a_alloc", "port": "ranking"},
                {"from": "a_alloc", "to": "k_risk", "port": "allocations"},
                {"from": "k_risk", "to": "y_exec", "port": "allocations"},
            ],
        )

    def test_deterministic_topological_order(self, cards):
        cir = ci.validate_ir(self._topo_ir(), cards=cards)
        assert list(cir.topological_order) == [
            "z_universe",
            "b_nine_dim",
            "c_chan_monitor",
            "m_rank",
            "a_alloc",
            "k_risk",
            "y_exec",
        ]
        assert ci.topological_order(cir) == cir.topological_order

    def test_base_ir_order_respects_edges(self, cards, valid_ir):
        cir = ci.validate_ir(valid_ir, cards=cards)
        position = {node_id: index for index, node_id in enumerate(cir.topological_order)}
        assert len(cir.topological_order) == len(cir.nodes)
        for edge in cir.edges:
            assert position[edge.from_id] < position[edge.to_id], edge


# ── 6. compile 输出 ──────────────────────────────────────────────────
class TestCompile:
    def test_compiled_pipeline_readonly_plan(self, cards, valid_ir):
        plan = ci.compile_ir(valid_ir, cards=cards)
        assert isinstance(plan, ci.CompiledPipeline)
        assert plan.pipeline_id == "pc-cn-shadow-001"
        assert plan.mode == "SHADOW"
        assert plan.readonly is True
        assert plan.side_effects == ()
        assert len(plan.nodes) == 9
        assert len(plan.edges) == 9
        assert list(plan.topological_order) == list(
            ci.validate_ir(valid_ir, cards=cards).topological_order
        )
        payload = plan.to_dict()
        assert payload["schema"] == ci.COMPILED_SCHEMA
        assert payload["readonly"] is True
        assert payload["side_effects"] == []
        assert payload["safety"] == {
            "executes": False,
            "places_orders": False,
            "writes_ledger": False,
            "publishes": False,
        }
        assert payload["execution"]["enabled"] is False
        assert payload["execution"]["dry_run"] is True
        assert payload["execution"]["places_orders"] is False
        assert payload["execution"]["writes_ledger"] is False
        assert payload["execution"]["books"] == ["official"]
        assert payload["publish_plan"]["effective"] is False
        assert payload["publish_plan"]["would_publish"] is False
        assert payload["risk_policy"]["policy_id"] == "rp-default"

    def test_compiled_nodes_follow_topological_order(self, cards, valid_ir):
        plan = ci.compile_ir(valid_ir, cards=cards)
        assert [node["order"] for node in plan.nodes] == list(range(len(plan.nodes)))
        assert [node["id"] for node in plan.nodes] == list(plan.topological_order)
        assert plan.nodes[0]["type"] == "universe"
        assert plan.nodes[-1]["type"] in ci.TERMINAL_NODE_TYPES

    def test_strategy_card_resolution_and_param_merge(self, cards, valid_ir):
        plan = ci.compile_ir(valid_ir, cards=cards)
        assert set(plan.strategies) == {"chan_monitor", "trend_follow"}
        chan = plan.strategies["chan_monitor"]
        assert chan["card"]["strategy_id"] == "chan_monitor"
        assert chan["card"]["version"] == "1.0.0"
        assert chan["card"]["signal_kind"] == "signal_pool_events"
        assert chan["card"]["modes"] == ["BACKTEST", "REPLAY", "SHADOW"]
        assert chan["card"]["mode_supported"] is True
        assert chan["params"]["resonance_bonus_per_field"] == 2  # 卡片默认值
        assert chan["param_sources"]["resonance_bonus_per_field"] == "default"

        trend = plan.strategies["trend_follow"]
        assert trend["params"]["n"] == 30  # 节点 override
        assert trend["param_sources"]["n"] == "override"
        assert trend["params"]["slope_lookback"] == 5  # 卡片默认值
        assert trend["param_sources"]["slope_lookback"] == "default"

    def test_edges_resolve_in_port_and_type(self, cards, valid_ir):
        plan = ci.compile_ir(valid_ir, cards=cards)
        assert all(edge["in_port"] and edge["port_type"] for edge in plan.edges)
        rank_to_alloc = next(
            edge
            for edge in plan.edges
            if edge["from"] == "r_top" and edge["to"] == "a_eq"
        )
        assert rank_to_alloc["port"] == "ranking"
        assert rank_to_alloc["in_port"] == "ranking"
        assert rank_to_alloc["port_type"] == "ranking"
        risk_to_exec = next(
            edge
            for edge in plan.edges
            if edge["from"] == "k_guard" and edge["to"] == "x_paper"
        )
        assert risk_to_exec["in_port"] == "allocations"

    def test_publish_intent_never_effective(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        doc["publish"] = {
            "enabled": True,
            "channel": "paper",
            "visibility": "tenant",
            "requires_approval": True,
        }
        plan = ci.compile_ir(doc, cards=cards)
        assert plan.publish_plan["would_publish"] is True
        assert plan.publish_plan["effective"] is False
        assert plan.publish_plan["channel"] == "paper"
        assert plan.safety["publishes"] is False

    def test_compile_with_injected_cards(self):
        fake_cards = [
            {
                "strategy_id": "fake_alpha",
                "version": "2.0.0",
                "owner": "tester",
                "universe": ["CN"],
                "frequency": "daily",
                "horizon_days": 5,
                "signal_schema": {"kind": "event_frame_signals"},
                "regime_tags": ["trend"],
                "known_failure_modes": ["none"],
                "data_deps": ["bars.daily"],
                "capacity": {
                    "timeout_s": 1.0,
                    "max_concurrency": 1,
                    "max_context_bytes": 1024,
                },
                "params_schema": {"lookback": {"type": "int", "default": 10}},
                "modes": ["SHADOW"],
                "health": {},
            }
        ]
        doc = _ir()
        _node(doc, "s_chan")["strategy_id"] = "fake_alpha"
        _node(doc, "s_chan")["params"] = {"lookback": 20}
        # 注入卡只含 fake_alpha：移除基线里引用真实卡的 s_trend 节点及其连边
        doc["nodes"] = [node for node in doc["nodes"] if node["id"] != "s_trend"]
        doc["edges"] = [
            edge
            for edge in doc["edges"]
            if edge["from"] != "s_trend" and edge["to"] != "s_trend"
        ]
        plan = ci.compile_ir(doc, cards=fake_cards)
        assert plan.sources["cards"] == "injected"
        assert plan.strategies["fake_alpha"]["card"]["version"] == "2.0.0"
        assert plan.strategies["fake_alpha"]["params"]["lookback"] == 20
        assert plan.strategies["fake_alpha"]["param_sources"]["lookback"] == "override"

    def test_compile_closed_on_failure(self, cards, valid_ir):
        doc = copy.deepcopy(valid_ir)
        _node(doc, "s_chan")["strategy_id"] = "ghost_strategy"
        with pytest.raises(ci.IRValidationError, match="未知策略"):
            ci.compile_ir(doc, cards=cards)


# ── 7. 只读 ──────────────────────────────────────────────────────────
class TestReadOnly:
    def test_validate_and_compile_do_not_mutate_sources(self, cards, valid_ir):
        paths = _all_readonly_sources()
        before = {path: _sha256(path) for path in paths}
        cir = ci.validate_ir(valid_ir, cards=cards)
        plan = ci.compile_ir(valid_ir, cards=cards)
        _ = ci.topological_order(cir)
        after = {path: _sha256(path) for path in paths}
        assert before == after, "validate/compile 不得写任何只读源文件"
        assert plan.readonly is True and plan.side_effects == ()

    def test_compile_writes_no_files(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ci.compile_ir(_base_ir())
        assert list(tmp_path.iterdir()) == []

    def test_module_source_has_no_write_or_trading_dependency(self):
        source = open(_MODULE, "r", encoding="utf-8").read()
        tree = ast.parse(source)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        assert not (
            roots
            & {
                "aggregator",
                "combined_posterior",
                "paper_book",
                "risk_gate",
                "engine",
                "ledger",
                "subprocess",
                "requests",
                "psycopg2",
                "sqlite3",
            }
        )
        writes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else "")
            )
            if name in {"write_text", "write_bytes", "dump", "dump_all", "save"}:
                writes.add(name)
            if name == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for keyword in node.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode = keyword.value.value
                if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
                    writes.add("open(%r)" % mode)
        assert writes == set(), "composition_ir 模块不得包含写文件/落账调用"


# ── 8. CLI（closed-on-failure） ──────────────────────────────────────
class TestCli:
    def test_cli_json_output(self, tmp_path, capsys):
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path), "--json"]) == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["ok"] is True
        assert payload["validation"]["pipeline_id"] == "pc-cn-shadow-001"
        assert payload["validation"]["schema"] == ci.SCHEMA
        assert payload["compiled"]["schema"] == ci.COMPILED_SCHEMA
        assert payload["compiled"]["readonly"] is True
        assert payload["compiled"]["side_effects"] == []

    def test_cli_human_output(self, tmp_path, capsys):
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path)]) == 0
        out = capsys.readouterr().out
        assert "[composition_ir] pipeline=pc-cn-shadow-001" in out
        assert "拓扑顺序" in out
        assert "chan_monitor@1.0.0" in out
        assert "只读计划" in out

    def test_cli_validate_only(self, tmp_path, capsys):
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path), "--validate-only", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["compiled"] is None

    def test_cli_invalid_ir_closed_on_failure(self, tmp_path, capsys):
        doc = _base_ir()
        _node(doc, "s_chan")["strategy_id"] = "ghost_strategy"
        path = _write_ir(tmp_path, doc)
        assert ci.main(["--file", str(path), "--json"]) == 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["ok"] is False
        assert any("未知策略" in error for error in payload["errors"])
        assert "非法 IR" in captured.err

    def test_cli_missing_file_rc2(self):
        assert ci.main(["--file", "definitely-missing-ir.json"]) == 2

    def test_cli_usage_errors_rc2(self, tmp_path):
        assert ci.main([]) == 2
        assert ci.main(["--no-such-flag"]) == 2
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path), "--json", "--bad"]) == 2

    def test_cli_help_rc0(self, capsys):
        assert ci.main(["--help"]) == 0
        assert "用法" in capsys.readouterr().out

    def test_cli_cards_file(self, tmp_path, capsys):
        cards_file = tmp_path / "strategy_cards.json"
        cards_file.write_bytes(open(sc.DEFAULT_CARDS_PATH, "rb").read())
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path), "--cards-file", str(cards_file), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["compiled"]["sources"]["cards"] == str(cards_file)
        assert payload["compiled"]["strategies"]["chan_monitor"]["card"]["version"] == "1.0.0"

    def test_cli_bad_cards_file_rc2(self, tmp_path, capsys):
        cards_file = tmp_path / "bad_cards.json"
        cards_file.write_text(json.dumps({"cards": []}), encoding="utf-8")
        path = _write_ir(tmp_path, _base_ir())
        assert ci.main(["--file", str(path), "--cards-file", str(cards_file)]) == 2
        assert "读取策略卡失败" in capsys.readouterr().err

    def test_cli_bad_json_rc2(self, tmp_path, capsys):
        path = tmp_path / "broken.json"
        path.write_text("{not-json", encoding="utf-8")
        assert ci.main(["--file", str(path)]) == 2
        assert "读取 IR 失败" in capsys.readouterr().err

    def test_script_mode_invocation(self, tmp_path):
        path = _write_ir(tmp_path, _base_ir())
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", _MODULE, "--file", str(path), "--json"],
            cwd=_QB_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["compiled"]["pipeline_id"] == "pc-cn-shadow-001"

    def test_load_ir_file_errors(self, tmp_path):
        with pytest.raises(ValueError, match="不存在"):
            ci.load_ir_file(str(tmp_path / "nope.json"))
        bad = tmp_path / "list.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="顶层必须为 dict"):
            ci.load_ir_file(str(bad))
