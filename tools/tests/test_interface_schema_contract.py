# -*- coding: utf-8 -*-
"""IFS F1 契约测试：8 份单接口 schema + envelope 适配器 + registry 对齐。

WO-IFS-SCHEMA-20261103（task-31）。**不依赖真实服务/网络/生产数据**：
只读 `docs/standards/` 下的 schema/registry/文档，并用 `agent_platform.contracts.ifs`
纯函数适配器做正反例。

覆盖：
1) 8 份 schema 可加载（draft-07 合法）、必填字段、内嵌 examples 自校验；
2) 各接口关键约束：handoff v2 拒绝执行字段、confirmation v2 四 ID、paper_receipt
   trade_id/hash 链、outcome 缺失显式 null、watch/trigger 合并定义；
3) envelope/error 适配器：wrap_ok/wrap_error 正反例、21 错误码与 registry 一致、
   request_id/message/retryable 校验、纯函数无副作用；
4) 与 `INTERFACE_REGISTRY_v1.yaml` 对齐：38 接口数（F1d war_room.snapshot.get +1、ACAD1 research 6 接口 +6）、
   schema/schema_file 一致、standards 引用新文档；
5) 文档交付物存在（schema README + F1 接线说明含回滚）。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="需要 PyYAML 解析接口登记表")
jsonschema = pytest.importorskip("jsonschema", reason="需要 jsonschema 校验 schema/examples")
Draft7Validator = jsonschema.Draft7Validator

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform.contracts import ifs  # noqa: E402

STANDARDS = REPO / "docs" / "standards"
SCHEMAS = STANDARDS / "schemas"
REGISTRY_PATH = STANDARDS / "INTERFACE_REGISTRY_v1.yaml"
SCHEMA_README = SCHEMAS / "README.md"
WIRING_DOC = STANDARDS / "2026-11-03_F1接口Schema接线说明_v1.md"
F0_REGISTRY_TEST = REPO / "tools" / "tests" / "test_interface_registry_contract.py"

FIXED_REQUEST_ID = "req-0123456789abcdef0123456789abcdef"
FIXED_CONFIRMATION_ID = "CFM-1A2B3C4D5E6F"

#: version -> (文件, 根内指针, 登记该 schema 的 interface_id 列表, 关键必填字段)
SCHEMA_SPECS = (
    (
        "acad1_pool/v2",
        "acad1_pool_v2.schema.json",
        None,
        ("acad1.pool.get",),
        ("schema_version", "pool_id", "source", "counts", "items", "stale"),
    ),
    (
        "d1_research/v1",
        "d1_research_v1.schema.json",
        None,
        ("d1.research.get",),
        ("schema_version", "readonly", "source", "items", "summary"),
    ),
    (
        "d1_decision/v1",
        "d1_decision_v1.schema.json",
        None,
        ("d1.decision.create", "d1.decision.list"),
        ("schema_version", "decision_id", "code", "confidence", "rationale", "paper_only"),
    ),
    (
        "d1_handoff/v2",
        "d1_handoff_v2.schema.json",
        None,
        ("d1.handoff.create", "d1.handoff.list"),
        ("schema_version", "handoff_id", "decision_id", "watch_plans", "not_a_trade"),
    ),
    (
        "watch_plan/v1",
        "watch_plan_v1.schema.json",
        None,
        ("watch.plan.create", "d1.watch_plans.list"),
        ("schema_version", "watch_plan_id", "handoff_id", "decision_id", "conditions"),
    ),
    (
        "trading_bot_confirmation/v2",
        "trading_bot_confirmation_v2.schema.json",
        None,
        ("confirm.create", "confirm.list"),
        (
            "schema_version",
            "confirmation_id",
            "decision_id",
            "handoff_id",
            "watch_plan_id",
            "trigger_id",
            "not_a_trade",
        ),
    ),
    (
        "paper_receipt/v1",
        "paper_receipt_v1.schema.json",
        None,
        ("paper.execution.record",),
        ("schema_version", "trade_id", "confirmation_id", "hash", "prev_hash", "trading_permitted"),
    ),
    (
        "outcome/v1",
        "outcome_v1.schema.json",
        None,
        ("outcome.get",),
        ("schema_version", "outcome_id", "decision_id", "trade_id", "next_close", "hit"),
    ),
)
TRIGGER_EVENT_SPEC = (
    "trigger_event/v1",
    "watch_plan_v1.schema.json",
    "/$defs/trigger_event",
    ("watch.trigger.create",),
    ("schema_version", "trigger_id", "watch_plan_id", "handoff_id", "decision_id", "action"),
)
ALL_SPECS = SCHEMA_SPECS + (TRIGGER_EVENT_SPEC,)

EXECUTION_FIELDS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


# ── 工具 ─────────────────────────────────────────────────────────────
def _schema_path(filename):
    return SCHEMAS / filename


def _resolve_pointer(document, pointer):
    if not pointer:
        return document
    assert pointer.startswith("/")
    current = document
    for token in pointer.strip("/").split("/"):
        current = current[token]
    return current


def load_schema(version):
    for spec in ALL_SPECS:
        if spec[0] == version:
            document = json.loads(_schema_path(spec[1]).read_text(encoding="utf-8"))
            return _resolve_pointer(document, spec[2])
    raise KeyError("未登记 schema: %s" % version)


def schema_examples(version):
    schema = load_schema(version)
    return schema.get("examples") or []


@pytest.fixture(scope="module")
def registry():
    assert REGISTRY_PATH.is_file()
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def interfaces(registry):
    return {item["id"]: item for item in registry["interfaces"]}


# ── 1. schema 可加载 / 结构 / examples ───────────────────────────────
class TestSchemaDocuments:
    @pytest.mark.parametrize("spec", ALL_SPECS, ids=[spec[0] for spec in ALL_SPECS])
    def test_schema_loads_and_is_valid_draft07(self, spec):
        version, filename, pointer, _ids, _required = spec
        path = _schema_path(filename)
        assert path.is_file(), "缺 schema 文件: %s" % path
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["$schema"] == "http://json-schema.org/draft-07/schema#"
        assert document["type"] == "object"
        assert document["properties"]
        Draft7Validator.check_schema(document)
        sub = _resolve_pointer(document, pointer)
        assert sub["type"] == "object"
        assert sub["properties"]
        assert sub["required"]

    @pytest.mark.parametrize("spec", ALL_SPECS, ids=[spec[0] for spec in ALL_SPECS])
    def test_schema_version_const(self, spec):
        version, _filename, _pointer, _ids, _required = spec
        schema = load_schema(version)
        assert schema["properties"]["schema_version"]["const"] == version

    @pytest.mark.parametrize("spec", ALL_SPECS, ids=[spec[0] for spec in ALL_SPECS])
    def test_embedded_examples_validate(self, spec):
        version, _filename, _pointer, _ids, _required = spec
        schema = load_schema(version)
        examples = schema.get("examples") or []
        assert examples, "%s 缺内嵌 examples" % version
        validator = Draft7Validator(schema)
        for example in examples:
            errors = sorted(validator.iter_errors(example), key=lambda error: error.path)
            assert not errors, "%s example 非法: %s" % (version, errors[0].message if errors else "")
        # 示例必须与声明的 schema_version 一致
        assert examples[0]["schema_version"] == version

    @pytest.mark.parametrize("spec", ALL_SPECS, ids=[spec[0] for spec in ALL_SPECS])
    def test_required_fields_declared(self, spec):
        version, _filename, _pointer, _ids, required_fields = spec
        schema = load_schema(version)
        missing = [field for field in required_fields if field not in schema["required"]]
        assert not missing, "%s required 缺 %s" % (version, missing)


# ── 2. 关键业务约束（无执行字段 / 四 ID / trade_id / 显式 null） ─────
class TestSchemaConstraints:
    def _assert_rejected(self, version, mutated):
        schema = load_schema(version)
        assert not Draft7Validator(schema).is_valid(mutated), "%s 应拒绝: %r" % (version, mutated)

    def test_handoff_forbids_execution_fields(self):
        example = schema_examples("d1_handoff/v2")[0]
        for field in EXECUTION_FIELDS:
            mutated = dict(example)
            mutated[field] = 100
            self._assert_rejected("d1_handoff/v2", mutated)
        assert example["not_a_trade"] is True
        assert example["paper_only"] is True

    def test_decision_and_research_forbid_execution_fields(self):
        for version in ("d1_decision/v1", "d1_research/v1", "watch_plan/v1"):
            example = schema_examples(version)[0]
            mutated = dict(example)
            mutated["order"] = 100
            self._assert_rejected(version, mutated)

    def test_handoff_requires_watch_plans_and_decision_id(self):
        example = json.loads(json.dumps(schema_examples("d1_handoff/v2")[0]))
        assert example["watch_plans"]
        without_plans = dict(example)
        without_plans.pop("watch_plans")
        self._assert_rejected("d1_handoff/v2", without_plans)
        example["decision_id"] = "not-a-decision"
        self._assert_rejected("d1_handoff/v2", example)

    def test_confirmation_requires_four_ids(self):
        example = json.loads(json.dumps(schema_examples("trading_bot_confirmation/v2")[0]))
        for field in ("decision_id", "handoff_id", "watch_plan_id", "trigger_id"):
            mutated = dict(example)
            mutated.pop(field)
            self._assert_rejected("trading_bot_confirmation/v2", mutated)
        example["confirmation_id"] = "CFM-1A2B3C"  # 长度不足 12
        self._assert_rejected("trading_bot_confirmation/v2", example)

    def test_paper_receipt_trade_id_and_hash_chain(self):
        example = json.loads(json.dumps(schema_examples("paper_receipt/v1")[0]))
        assert re.match(r"^trd-\d{8}-[0-9A-F]{8}$", example["trade_id"])
        assert example["trading_permitted"] is False
        assert example["paper_only"] is True
        bad_trade = dict(example)
        bad_trade["trade_id"] = "trade-1"
        self._assert_rejected("paper_receipt/v1", bad_trade)
        bad_hash = dict(example)
        bad_hash["hash"] = "abc"
        self._assert_rejected("paper_receipt/v1", bad_hash)
        live_field = dict(example)
        live_field["broker"] = "x"
        self._assert_rejected("paper_receipt/v1", live_field)

    def test_outcome_missing_market_data_must_be_explicit_null(self):
        example = json.loads(json.dumps(schema_examples("outcome/v1")[0]))
        explicit_null = dict(example)
        explicit_null["next_close"] = None
        explicit_null["next_day_return"] = None
        explicit_null["hit"] = None
        explicit_null["trade_id"] = None
        assert Draft7Validator(load_schema("outcome/v1")).is_valid(explicit_null)
        missing_key = dict(example)
        missing_key.pop("next_close")
        self._assert_rejected("outcome/v1", missing_key)
        example["outcome_id"] = "outcome-1"
        self._assert_rejected("outcome/v1", example)

    def test_trigger_event_merged_definition(self):
        schema = load_schema("trigger_event/v1")
        sample = {
            "schema_version": "trigger_event/v1",
            "trigger_id": "trg-20261103-0f1e2d",
            "watch_plan_id": "wp-20261102-a1b2c3",
            "handoff_id": "d1h-20261102170000-a1b2c3",
            "decision_id": "D1-20261102-8f3a1c2b90",
            "symbol": "sz003006",
            "action": "BUY",
            "ts": "2026-11-03T09:35:00+08:00",
            "paper_only": True,
        }
        assert Draft7Validator(schema).is_valid(sample)
        bad = dict(sample)
        bad["trigger_id"] = "t-1"
        assert not Draft7Validator(schema).is_valid(bad)
        assert schema["properties"]["schema_version"]["const"] == "trigger_event/v1"

    def test_acad1_pool_source_mode_explicit(self):
        example = schema_examples("acad1_pool/v2")[0]
        assert example["source"]["source_mode"] in ("pg_official", "projection")
        missing_published = json.loads(json.dumps(example))
        missing_published["source"].pop("published_at")
        self._assert_rejected("acad1_pool/v2", missing_published)
        bad_mode = json.loads(json.dumps(example))
        bad_mode["source"]["source_mode"] = "json_projection"
        self._assert_rejected("acad1_pool/v2", bad_mode)


# ── 3. envelope / error 适配器 ──────────────────────────────────────
class TestEnvelopeAdapter:
    def test_envelope_for_each_schema(self):
        for spec in ALL_SPECS:
            version, _filename, _pointer, interface_ids, _required = spec
            example = schema_examples(version)[0]
            envelope = ifs.wrap_ok(
                interface_ids[0],
                version,
                example,
                source_of_truth="agent_platform/data/test/",
                source_mode="ledger",
                request_id=FIXED_REQUEST_ID,
                generated_at="2026-11-03T08:00:00+08:00",
            )
            assert ifs.validate_envelope(envelope) == [], version
            assert envelope["ok"] is True
            assert envelope["schema_version"] == version
            assert envelope["request_id"] == FIXED_REQUEST_ID
            assert envelope["warnings"] == []
            Draft7Validator(load_schema(version)).validate(envelope["data"])

    def test_wrap_ok_with_ids_warnings_and_source(self):
        envelope = ifs.wrap_ok(
            "d1.research.get",
            "d1_research/v1",
            {"items": []},
            source_of_truth="agent_platform/data/pool_review/",
            source_mode="aggregate",
            as_of="2026-11-03",
            freshness_s=60,
            stale=True,
            ids={"pool_id": "ACAD1-REC-20261102"},
            warnings=[{"code": "stale_projection", "message": "投影落后", "details": {"lag_days": 1}}],
            request_id=FIXED_REQUEST_ID,
        )
        assert ifs.validate_envelope(envelope) == []
        assert envelope["source"]["pool_id"] == "ACAD1-REC-20261102"
        assert envelope["source"]["stale"] is True
        assert envelope["warnings"][0]["code"] == "stale_projection"

    def test_wrap_ok_rejects_bad_arguments(self):
        with pytest.raises(ValueError):
            ifs.wrap_ok("d1.research.get", "bad-version", {}, source_of_truth="x", source_mode="ledger")
        with pytest.raises(ValueError):
            ifs.wrap_ok("d1.research.get", "d1_research/v1", {})
        with pytest.raises(ValueError):
            ifs.wrap_ok("D1 Research", "d1_research/v1", {}, source_of_truth="x", source_mode="ledger")
        with pytest.raises(ValueError):
            ifs.wrap_ok(
                "d1.research.get",
                "d1_research/v1",
                {},
                source_of_truth="x",
                source_mode="ledger",
                warnings=[{"code": "x"}],
            )

    def test_error_codes_match_registry(self, registry):
        registered = registry["envelope"]["error_codes"]
        assert set(ifs.ERROR_CODES) == set(registered)
        for code, spec in registered.items():
            assert ifs.ERROR_CODES[code]["http"] == spec["http"], code
            assert ifs.ERROR_CODES[code]["retryable"] == spec["retryable"], code
            assert ifs.ERROR_CODES[code]["scene"] == spec["scene"], code

    def test_wrap_error_all_codes(self, registry):
        for code, spec in registry["envelope"]["error_codes"].items():
            envelope = ifs.wrap_error(code, "示例错误", request_id=FIXED_REQUEST_ID)
            assert ifs.validate_error(envelope) == [], code
            assert envelope["ok"] is False
            assert envelope["schema_version"] == "error/v1"
            assert envelope["error"]["code"] == code
            assert envelope["error"]["retryable"] is spec["retryable"]
            assert envelope["error"]["details"] == {}
            assert ifs.error_http_status(code) == spec["http"]

    def test_wrap_error_options_and_rejections(self):
        envelope = ifs.wrap_error(
            "rate_limited",
            "稍后重试",
            details={"scope": "x"},
            retryable=True,
            param="q",
            retry_after_s=3,
            request_id=FIXED_REQUEST_ID,
        )
        assert ifs.validate_error(envelope) == []
        assert envelope["error"]["retry_after_s"] == 3
        assert envelope["error"]["param"] == "q"
        with pytest.raises(ValueError):
            ifs.wrap_error("not_registered", "x")
        with pytest.raises(ValueError):
            ifs.wrap_error("bad_body", "")
        with pytest.raises(ValueError):
            ifs.wrap_error("bad_body", "x", http_status=500)

    def test_validators_reject_bad_payloads(self):
        good = ifs.wrap_error("bad_body", "非法 JSON", request_id=FIXED_REQUEST_ID)
        bad = json.loads(json.dumps(good))
        bad["error"].pop("retryable")
        assert ifs.validate_error(bad)
        bad2 = json.loads(json.dumps(good))
        bad2["error"]["code"] = "unregistered"
        assert ifs.validate_error(bad2)
        success = ifs.wrap_ok(
            "d1.research.get", "d1_research/v1", {}, source_of_truth="x", source_mode="ledger",
            request_id=FIXED_REQUEST_ID,
        )
        bad_success = json.loads(json.dumps(success))
        bad_success.pop("warnings")
        assert ifs.validate_envelope(bad_success)
        bad_success["ok"] = False
        assert ifs.validate_envelope(bad_success)

    def test_error_and_success_not_interchangeable(self):
        success = ifs.wrap_ok(
            "d1.research.get", "d1_research/v1", {}, source_of_truth="x", source_mode="ledger",
            request_id=FIXED_REQUEST_ID,
        )
        error = ifs.wrap_error("bad_body", "x", request_id=FIXED_REQUEST_ID)
        assert ifs.validate_envelope(error)
        assert ifs.validate_error(success)

    def test_request_id_format(self):
        request_id = ifs.new_request_id()
        assert re.match(ifs.REQUEST_ID_PATTERN, request_id)
        envelope = ifs.wrap_ok(
            "d1.research.get", "d1_research/v1", {}, source_of_truth="x", source_mode="ledger"
        )
        assert re.match(ifs.REQUEST_ID_PATTERN, envelope["request_id"])

    def test_source_block_validation(self):
        block = ifs.source_block("d1.research.get", "x", "ledger", ids={"pool_id": "p"})
        assert block["pool_id"] == "p"
        with pytest.raises(ValueError):
            ifs.source_block("d1.research.get", "x", "bad-mode")
        with pytest.raises(ValueError):
            ifs.source_block("d1.research.get", "x", "ledger", ids={"source_mode": "x"})
        with pytest.raises(ValueError):
            ifs.source_block("Bad ID", "x", "ledger")

    def test_adapter_is_pure_stdlib(self):
        source = Path(ifs.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        import_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                import_roots.add(node.module.split(".")[0])
        assert import_roots <= {"__future__", "re", "time", "uuid", "typing"}, import_roots

    def test_adapter_no_side_effects(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for _ in range(5):
            ifs.wrap_ok(
                "d1.research.get", "d1_research/v1", {}, source_of_truth="x",
                source_mode="ledger", request_id=FIXED_REQUEST_ID,
            )
            ifs.wrap_error("bad_body", "x", request_id=FIXED_REQUEST_ID)
        assert list(tmp_path.iterdir()) == []


# ── 4. registry 对齐（接口数不变 / schema + schema_file 一致） ────────
class TestRegistryAlignment:
    def test_interface_count_matches_counts(self, registry):
        assert len(registry["interfaces"]) == 38
        assert registry["counts"]["interfaces"] == 38

    def test_war_room_registered_without_schema_file(self, registry, interfaces):
        """F1d 接口登记：schema war_room/v1；独立 JSON Schema/schema_file 待补（缺口显式）。"""
        item = interfaces["war_room.snapshot.get"]
        assert item["schema"] == "war_room/v1"
        assert "schema_file" not in item, "war_room 独立 schema 未交付前不得声明 schema_file"
        assert item["SLO"] == registry["slo_classes"]["read_heavy"]
        assert item["status"] == "draft"

    def test_acad1_research_registered_without_schema_file(self, registry, interfaces):
        """ACAD1 research 6 接口：本地契约 acad1_research/v1；独立 schema/envelope 待补（缺口显式）。"""
        expected = (
            "acad1.research.pool",
            "acad1.research.stats",
            "acad1.research.reviews",
            "acad1.research.decisions",
            "acad1.research.evidence",
            "acad1.research.calendar",
        )
        for interface_id in expected:
            item = interfaces[interface_id]
            assert item["schema"] == "acad1_research/v1"
            assert "schema_file" not in item, (
                "%s 独立 schema 未交付前不得声明 schema_file" % interface_id)
            assert item["SLO"] == registry["slo_classes"]["read_heavy"]
            assert item["status"] == "draft"
            assert item["owner"] == "ACAD1"

    def test_every_delivered_schema_is_referenced(self, interfaces):
        for spec in ALL_SPECS:
            version, _filename, _pointer, interface_ids, _required = spec
            for interface_id in interface_ids:
                assert interface_id in interfaces, "registry 缺接口: %s" % interface_id
                assert interfaces[interface_id]["schema"] == version, interface_id

    def test_schema_file_paths_exist_and_match(self, interfaces):
        for interface in interfaces.values():
            schema_file = interface.get("schema_file")
            if not schema_file:
                continue
            relative, _, pointer = schema_file.partition("#")
            path = REPO / relative
            assert path.is_file(), "%s schema_file 不存在: %s" % (interface["id"], relative)
            document = json.loads(path.read_text(encoding="utf-8"))
            if pointer:
                sub = _resolve_pointer(document, pointer)
                assert sub["type"] == "object"
            else:
                assert document["type"] == "object"

    def test_target_interfaces_schema_files(self, interfaces):
        expected = {
            "acad1.pool.get": "acad1_pool_v2.schema.json",
            "d1.research.get": "d1_research_v1.schema.json",
            "d1.decision.create": "d1_decision_v1.schema.json",
            "d1.decision.list": "d1_decision_v1.schema.json",
            "d1.handoff.create": "d1_handoff_v2.schema.json",
            "d1.handoff.list": "d1_handoff_v2.schema.json",
            "d1.watch_plans.list": "watch_plan_v1.schema.json",
            "watch.plan.create": "watch_plan_v1.schema.json",
            "watch.trigger.create": "watch_plan_v1.schema.json#/$defs/trigger_event",
            "confirm.create": "trading_bot_confirmation_v2.schema.json",
            "confirm.list": "trading_bot_confirmation_v2.schema.json",
            "paper.execution.record": "paper_receipt_v1.schema.json",
            "outcome.get": "outcome_v1.schema.json",
        }
        for interface_id, suffix in expected.items():
            schema_file = interfaces[interface_id].get("schema_file") or ""
            assert schema_file.endswith(suffix), "%s schema_file=%r" % (interface_id, schema_file)

    def test_paper_and_outcome_schema_versions_aligned(self, interfaces):
        assert interfaces["paper.execution.record"]["schema"] == "paper_receipt/v1"
        assert interfaces["outcome.get"]["schema"] == "outcome/v1"

    def test_registry_envelope_matches_adapter(self, registry):
        envelope = registry["envelope"]
        assert envelope["success_required"] == list(ifs.SUCCESS_FIELDS)
        assert envelope["error_required"] == list(ifs.ERROR_FIELDS)
        assert envelope["error_required_detail"] == list(ifs.ERROR_DETAIL_FIELDS)
        assert envelope["error_schema_version"] == ifs.ERROR_SCHEMA_VERSION

    def test_registry_standards_reference_new_docs(self, registry):
        standards = registry["standards"]
        assert standards["f1_schema_wiring"] == (
            "docs/standards/2026-11-03_F1接口Schema接线说明_v1.md"
        )
        assert standards["schema_index"] == "docs/standards/schemas/README.md"
        for key in ("f1_schema_wiring", "schema_index"):
            assert (REPO / standards[key]).is_file(), "standards.%s 文件不存在" % key


# ── 5. 文档交付物（接线说明 / 回滚 / schema 索引） ───────────────────
class TestF1Docs:
    def test_docs_exist(self):
        assert SCHEMA_README.is_file()
        assert WIRING_DOC.is_file()

    def test_wiring_doc_covers_all_schemas(self):
        text = WIRING_DOC.read_text(encoding="utf-8")
        for spec in ALL_SPECS:
            version, filename = spec[0], spec[1]
            assert version in text or filename in text, "接线文档缺 %s" % version

    def test_wiring_doc_has_wiring_and_rollback(self):
        text = WIRING_DOC.read_text(encoding="utf-8")
        for keyword in (
            "接线项",
            "回滚",
            "INTERFACE_REGISTRY_v1.yaml",
            "agent_platform/contracts/ifs.py",
            "git revert",
        ):
            assert keyword in text, "接线文档缺: %s" % keyword

    def test_schema_readme_lists_all_files(self):
        text = SCHEMA_README.read_text(encoding="utf-8")
        for spec in SCHEMA_SPECS:
            assert spec[1] in text, "schema README 缺: %s" % spec[1]
        assert "trigger_event" in text
        assert "interface_envelope_v1.schema.json" in text

    def test_f0_contract_test_still_present(self):
        """F0 registry 契约测试仍存在（本单不修改，避免破坏既有门禁）。"""
        assert F0_REGISTRY_TEST.is_file()
        assert "test_interface_registry_contract" in F0_REGISTRY_TEST.name
