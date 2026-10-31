# -*- coding: utf-8 -*-
"""pipeline_templates 单测（WO-PIPELINE-TEMPLATES-20261031，Phase 2-E）。

覆盖验收口径：
1) 3 个模板存在（trend_pullback / trend_reversal / nine_dim_chan），JSON 合法；
2) 每个模板是合法 Composition IR：composition_ir.validate_ir / compile_ir 全过；
3) 节点引用存在：strategy 节点 strategy_id 属于 Strategy Card 索引、边端点存在；
4) 只读：execution dry_run、publish effective=false、compile 安全开关全 false；
   模板 JSON / cards / manifest / registry / composition_ir 源文件字节不变；
5) 参数化（tenant / weights / max_single）只改内存副本，非法覆写拒绝；
6) CLI --list / --template / --json 行为与退出码。

全程离线：只读 strategy_cards overlay + 内置 pipeline_templates.json + tmp_path 临时库。
不写生产数据、不新增任务、不重启服务。
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os

import pytest

from strategy_layer import composition_ir as ci
from strategy_layer import pipeline_templates as pt
from strategy_layer import strategy_cards as sc

EXPECTED_TEMPLATES = {"trend_pullback", "trend_reversal", "nine_dim_chan"}
EXPECTED_STRATEGIES = {
    "trend_pullback": ["trend_follow"],
    "trend_reversal": ["trend_follow", "rsi_reversal"],
    "nine_dim_chan": ["nine_dim", "chan_monitor", "chanlun_li122"],
}
_MODULE = os.path.abspath(pt.__file__)


# ── 工具 ──────────────────────────────────────────────────────────────
def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _load_default_library():
    with open(pt.DEFAULT_TEMPLATES_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_library(tmp_path, doc, name="pipeline_templates.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _template_ir(doc, template_id):
    return next(
        template for template in doc["templates"] if template["template_id"] == template_id
    )["ir"]


def _node_ids(ir):
    return {node["id"] for node in ir["nodes"]}


def _strategy_nodes(ir):
    return [node for node in ir["nodes"] if node["type"] == "strategy"]


def _allocators(ir):
    return [node for node in ir["nodes"] if node["type"] == "allocator"]


def _mutated_library(mutator):
    doc = _load_default_library()
    mutator(doc)
    return doc


@pytest.fixture(scope="module")
def cards():
    return sc.load_cards()


@pytest.fixture(scope="module")
def library():
    return _load_default_library()


# ── 1. 模板库 JSON 与 IR 合法性 ──────────────────────────────────────
class TestLibraryJson:
    def test_schema_and_meta(self, library):
        meta = library["_meta"]
        assert meta["schema"] == pt.TEMPLATE_SCHEMA
        assert meta["ir_schema"] == ci.SCHEMA
        assert meta["safety"]["readonly"] is True
        assert meta["safety"]["writes_registry"] is False

    def test_three_templates_present(self, library):
        ids = [template["template_id"] for template in library["templates"]]
        assert len(ids) == 3
        assert set(ids) == EXPECTED_TEMPLATES

    def test_each_template_ir_is_valid(self, library):
        for template in library["templates"]:
            validated = ci.validate_ir(template["ir"])
            assert validated.schema == ci.SCHEMA
            plan = ci.compile_ir(template["ir"])
            assert plan.readonly is True
            assert tuple(plan.side_effects) == ()
            assert all(value is False for value in plan.safety.values())
            assert plan.execution["dry_run"] is True
            assert plan.execution["enabled"] is False
            assert plan.execution["places_orders"] is False
            assert plan.execution["writes_ledger"] is False
            assert plan.publish_plan["effective"] is False
            assert plan.publish_plan["would_publish"] is False

    def test_parameter_defaults_match_ir(self, library):
        for template in library["templates"]:
            ir = template["ir"]
            params = template["parameters"]
            assert set(pt.PARAMETER_NAMES) <= set(params)
            assert params["tenant"]["default"] == ir["tenant"]
            assert params["max_single"]["default"] == ir["risk_policy"]["max_position_pct"]
            allocator_weights = [
                node["params"]["weights"]
                for node in _allocators(ir)
                if "weights" in node.get("params", {})
            ]
            assert allocator_weights
            assert allocator_weights[0] == params["weights"]["default"]

    def test_list_templates_metadata(self):
        infos = pt.list_templates()
        assert len(infos) == 3
        by_id = {info["template_id"]: info for info in infos}
        assert set(by_id) == EXPECTED_TEMPLATES
        for template_id, info in by_id.items():
            assert info["strategy_ids"] == EXPECTED_STRATEGIES[template_id]
            assert info["node_count"] > 0
            assert info["edge_count"] > 0
            assert info["execution_dry_run"] is True
            assert info["publish_effective"] is False
            assert info["readonly"] is True
            assert info["mode"] in ci.ALLOWED_MODES


# ── 2. load_template + 节点引用 ───────────────────────────────────────
class TestLoadTemplate:
    def test_load_all_templates(self):
        for template_id in EXPECTED_TEMPLATES:
            ir = pt.load_template(template_id)
            validated = ci.validate_ir(ir)
            assert validated.pipeline_id
            plan = ci.compile_ir(ir)
            assert plan.readonly is True
            assert plan.execution["dry_run"] is True
            assert plan.publish_plan["effective"] is False

    def test_strategy_nodes_reference_existing_cards(self, cards):
        card_ids = {card["strategy_id"] for card in cards}
        for template_id, expected in EXPECTED_STRATEGIES.items():
            ir = pt.load_template(template_id)
            referenced = [node["strategy_id"] for node in _strategy_nodes(ir)]
            assert referenced == expected
            assert set(referenced) <= card_ids

    def test_edges_reference_existing_nodes(self):
        for template_id in EXPECTED_TEMPLATES:
            ir = pt.load_template(template_id)
            node_ids = _node_ids(ir)
            assert len(node_ids) == len(ir["nodes"])
            for edge in ir["edges"]:
                assert edge["from"] in node_ids
                assert edge["to"] in node_ids
                assert edge["from"] != edge["to"]

    def test_compile_resolves_cards_and_merge_params(self, cards):
        card_index = {card["strategy_id"]: card for card in cards}
        ir = pt.load_template("trend_reversal")
        plan = ci.compile_ir(ir)
        for strategy_id in EXPECTED_STRATEGIES["trend_reversal"]:
            assert strategy_id in plan.strategies
            item = plan.strategies[strategy_id]
            assert item["card"]["strategy_id"] == strategy_id
            for key, value in item["params"].items():
                if item["param_sources"].get(key) == "default":
                    default = card_index[strategy_id]["params_schema"][key]["default"]
                    assert value == default
        assert plan.execution["dry_run"] is True
        assert plan.safety["executes"] is False
        assert plan.sources["ir"] == "in-memory"

    def test_default_is_readonly_overlay(self):
        for template_id in EXPECTED_TEMPLATES:
            plan = ci.compile_ir(pt.load_template(template_id))
            assert plan.readonly is True
            assert plan.sources["cards"].endswith("strategy_cards.json")
            assert plan.publish_plan["effective"] is False


# ── 3. 参数化（只改内存副本 + 非法拒绝） ─────────────────────────────
class TestParameterization:
    def test_tenant_override(self):
        ir = pt.load_template("trend_reversal", tenant="tenant-a")
        assert ir["tenant"] == "tenant-a"
        ci.validate_ir(ir)

    def test_weights_override(self):
        weights = {"trend_follow": 0.7, "rsi_reversal": 0.3}
        ir = pt.load_template("trend_reversal", weights=weights)
        for allocator in _allocators(ir):
            assert allocator["params"]["scheme"] == "weighted"
            assert allocator["params"]["weights"] == weights
        ci.validate_ir(ir)

    def test_max_single_override(self):
        ir = pt.load_template("nine_dim_chan", max_single=12.5)
        assert ir["risk_policy"]["max_position_pct"] == 12.5
        for allocator in _allocators(ir):
            assert allocator["params"]["max_single_pct"] == 12.5
        ci.validate_ir(ir)

    def test_all_overrides_together(self):
        ir = pt.load_template(
            "trend_pullback",
            tenant="tenant-b",
            weights={"trend_follow": 1.0},
            max_single=7.0,
        )
        assert ir["tenant"] == "tenant-b"
        assert ir["risk_policy"]["max_position_pct"] == 7.0
        assert _allocators(ir)[0]["params"]["weights"] == {"trend_follow": 1.0}
        plan = ci.compile_ir(ir)
        assert plan.tenant == "tenant-b"
        assert plan.readonly is True

    def test_overrides_do_not_mutate_library(self):
        before = _sha256(pt.DEFAULT_TEMPLATES_PATH)
        original = pt.load_template("trend_reversal")
        pt.load_template(
            "trend_reversal",
            tenant="tenant-c",
            weights={"trend_follow": 0.1, "rsi_reversal": 0.9},
            max_single=5.0,
        )
        after = pt.load_template("trend_reversal")
        assert _sha256(pt.DEFAULT_TEMPLATES_PATH) == before
        assert after == original
        assert after["tenant"] == "official"
        assert _allocators(after)[0]["params"]["weights"] == {
            "trend_follow": 0.6,
            "rsi_reversal": 0.4,
        }

    def test_load_returns_deep_copy(self):
        first = pt.load_template("trend_pullback")
        first["nodes"][0]["params"]["markets"].append("US")
        second = pt.load_template("trend_pullback")
        assert second["nodes"][0]["params"]["markets"] == ["CN"]

    def test_unknown_template_rejected(self):
        with pytest.raises(pt.TemplateNotFoundError):
            pt.load_template("ghost_template")
        with pytest.raises(pt.PipelineTemplateError):
            pt.get_template("ghost_template")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"tenant": "bad tenant!"},
            {"tenant": ""},
            {"weights": {"ghost_strategy": 1.0}},
            {"weights": {"trend_follow": -0.5}},
            {"weights": {}},
            {"max_single": 0},
            {"max_single": 100.1},
            {"max_single": "high"},
        ],
    )
    def test_invalid_overrides_rejected(self, kwargs):
        with pytest.raises(pt.PipelineTemplateError):
            pt.load_template("trend_pullback", **kwargs)


# ── 4. validate_templates 全量报告 ───────────────────────────────────
class TestValidateTemplates:
    def test_all_templates_ok(self):
        report = pt.validate_templates()
        assert report["schema"] == pt.TEMPLATE_SCHEMA
        assert report["ir_schema"] == ci.SCHEMA
        assert report["readonly"] is True
        assert report["total"] == 3
        assert report["all_ok"] is True
        ids = {item["template_id"] for item in report["templates"]}
        assert ids == EXPECTED_TEMPLATES
        for item in report["templates"]:
            assert item["ok"] is True
            assert item["errors"] == []
            assert item["execution_dry_run"] is True
            assert item["publish_effective"] is False
            assert item["readonly"] is True
            assert item["strategy_ids"] == EXPECTED_STRATEGIES[item["template_id"]]
            assert all(value is False for value in item["safety"].values())

    def test_invalid_ir_reported_and_raised(self, tmp_path):
        doc = _mutated_library(
            lambda lib: _template_ir(lib, "trend_pullback")["nodes"].append(
                {"id": "s_ghost", "type": "strategy", "strategy_id": "ghost_strategy"}
            )
        )
        path = _write_library(tmp_path, doc)
        report = pt.validate_templates(path=str(path), raise_on_error=False)
        assert report["all_ok"] is False
        failed = [item for item in report["templates"] if not item["ok"]]
        assert failed and failed[0]["template_id"] == "trend_pullback"
        assert any("未知策略" in error for error in failed[0]["errors"])
        with pytest.raises(pt.PipelineTemplateError):
            pt.validate_templates(path=str(path))

    def test_execution_dry_run_false_reported(self, tmp_path):
        def mutate(lib):
            ir = _template_ir(lib, "trend_pullback")
            for node in ir["nodes"]:
                if node["type"] == "execution":
                    node["params"]["dry_run"] = False

        path = _write_library(tmp_path, _mutated_library(mutate))
        report = pt.validate_templates(path=str(path), raise_on_error=False)
        assert report["all_ok"] is False
        errors = "；".join(report["templates"][0]["errors"])
        assert "dry_run" in errors

    def test_publish_enabled_reported(self, tmp_path):
        def mutate(lib):
            _template_ir(lib, "trend_reversal")["publish"]["enabled"] = True

        path = _write_library(tmp_path, _mutated_library(mutate))
        report = pt.validate_templates(path=str(path), raise_on_error=False)
        assert report["all_ok"] is False

    def test_bad_parameter_default_reported(self, tmp_path):
        def mutate(lib):
            for template in lib["templates"]:
                if template["template_id"] == "nine_dim_chan":
                    template["parameters"]["max_single"]["default"] = 150.0

        path = _write_library(tmp_path, _mutated_library(mutate))
        report = pt.validate_templates(path=str(path), raise_on_error=False)
        assert report["all_ok"] is False
        errors = "；".join(
            error
            for item in report["templates"]
            if item["template_id"] == "nine_dim_chan"
            for error in item["errors"]
        )
        assert "max_single" in errors

    def test_duplicate_template_id_rejected(self, tmp_path):
        doc = _load_default_library()
        cloned = copy.deepcopy(doc["templates"][0])
        doc["templates"].append(cloned)
        path = _write_library(tmp_path, doc)
        with pytest.raises(pt.PipelineTemplateError, match="重复"):
            pt.validate_templates(path=str(path))


# ── 5. 只读（源文件字节不变 + 模块无写调用） ─────────────────────────
class TestReadOnly:
    def test_readonly_sources_unchanged(self, capsys):
        sources = pt.readonly_sources()
        assert pt.DEFAULT_TEMPLATES_PATH in sources
        assert any(path.endswith("strategy_cards.json") for path in sources)
        assert any(path.endswith("strategy_registry.json") for path in sources)
        assert any(path.endswith("composition_ir.py") for path in sources)
        before = {path: _sha256(path) for path in sources}

        pt.list_templates()
        pt.load_template(
            "trend_reversal",
            tenant="tenant-ro",
            weights={"trend_follow": 0.5, "rsi_reversal": 0.5},
            max_single=9.0,
        )
        pt.validate_templates()
        capsys.readouterr()
        assert pt.main(["--list"]) == 0
        assert pt.main(["--template", "nine_dim_chan", "--json"]) == 0

        after = {path: _sha256(path) for path in sources}
        assert before == after, "模板库只读：不得写任何只读源文件"

    def test_module_has_no_write_calls(self):
        with open(_MODULE, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        write_names = {
            "write_text",
            "write_bytes",
            "makedirs",
            "mkdir",
            "remove",
            "unlink",
            "rename",
            "dump",
            "replace",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in write_names, "只读模块不得调用写盘函数: %s" % name
            if name == "open":
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                assert mode == "r", "只读模块 open 只允许 'r'，收到 %r" % (mode,)

    def test_no_registry_write_reference(self):
        with open(_MODULE, "r", encoding="utf-8") as handle:
            source = handle.read()
        assert "set_enabled(" not in source
        assert "set_weight(" not in source
        assert "register(" not in source


# ── 6. CLI ───────────────────────────────────────────────────────────
class TestCli:
    def test_list_human(self, capsys):
        assert pt.main(["--list"]) == 0
        out = capsys.readouterr().out
        for template_id in EXPECTED_TEMPLATES:
            assert template_id in out
        assert "templates=3" in out

    def test_list_json(self, capsys):
        assert pt.main(["--list", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["schema"] == pt.TEMPLATE_SCHEMA
        assert {item["template_id"] for item in payload["templates"]} == EXPECTED_TEMPLATES

    def test_template_json(self, capsys):
        assert pt.main(["--template", "trend_reversal", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["template_id"] == "trend_reversal"
        assert payload["readonly"] is True
        ci.validate_ir(payload["ir"])
        assert {node["strategy_id"] for node in _strategy_nodes(payload["ir"])} == set(
            EXPECTED_STRATEGIES["trend_reversal"]
        )

    def test_template_with_overrides_json(self, capsys):
        rc = pt.main(
            [
                "--template",
                "trend_pullback",
                "--json",
                "--tenant",
                "tenant-cli",
                "--max-single",
                "9.5",
                "--weights",
                "trend_follow=0.8",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        ir = payload["ir"]
        assert ir["tenant"] == "tenant-cli"
        assert ir["risk_policy"]["max_position_pct"] == 9.5
        assert _allocators(ir)[0]["params"]["weights"] == {"trend_follow": 0.8}
        ci.validate_ir(ir)

    def test_template_human(self, capsys):
        assert pt.main(["--template", "nine_dim_chan"]) == 0
        out = capsys.readouterr().out
        assert "template=nine_dim_chan" in out
        assert "dry_run=true" in out

    def test_default_report(self, capsys):
        assert pt.main([]) == 0
        out = capsys.readouterr().out
        assert "all_ok=true" in out
        assert "total=3" in out

    def test_default_report_json(self, capsys):
        assert pt.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["report"]["all_ok"] is True

    def test_unknown_template_rc2(self, capsys):
        assert pt.main(["--template", "ghost_template"]) == 2
        capsys.readouterr()

    def test_bad_weights_rc2(self, capsys):
        assert pt.main(["--template", "trend_pullback", "--weights", "broken"]) == 2
        capsys.readouterr()

    def test_invalid_library_rc1(self, tmp_path, capsys):
        def mutate(lib):
            _template_ir(lib, "trend_pullback")["nodes"].append(
                {"id": "s_ghost", "type": "strategy", "strategy_id": "ghost_strategy"}
            )

        path = _write_library(tmp_path, _mutated_library(mutate))
        assert pt.main(["--path", str(path), "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
