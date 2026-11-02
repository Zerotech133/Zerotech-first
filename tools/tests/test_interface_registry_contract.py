# -*- coding: utf-8 -*-
"""IFS v1 规范层契约测试骨架（WO-IFS-STD-20261103 · F0/F1）。

**不依赖任何真实服务 / 网络 / 生产数据**：只读 `docs/standards/` 下的
registry YAML、JSON Schema 与规范文档，校验接口规范层自身的一致性。

覆盖验收口径：
1) `INTERFACE_REGISTRY_v1.yaml` 可被 Python/yaml 解析；元信息/枚举/席位目录齐全；
2) 接口数 ≥20（当前 30）；IFS-02 的 12 个必填字段齐全 + 类型/取值域/唯一性；
3) owner 在册、consumer 非空、SLO 命中档位、paper_only 恒 true、write 语义正确；
4) 统一 ID chain：11 实体顺序/上下游邻接/格式正则/生成方/真源/无空洞/单生成方；
5) envelope/error：YAML annex 与两份 JSON Schema 一致（必填字段/错误码集合）；
6) 信封校验模板 `validate_envelope()` / `validate_error()`（成功/错误样例 + 反例）；
7) F0 文档交付物存在且交叉一致（IFS-01~14 / ID chain / error codes / matrix）。

增量接口接入方式（模板复用）::

    from tools.tests.test_interface_registry_contract import (
        validate_envelope, validate_error,
    )
    assert validate_envelope(payload) == []      # 成功响应
    assert validate_error(payload, code="bad_body") == []
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="需要 PyYAML 解析接口登记表")

REPO = Path(__file__).resolve().parents[2]
STANDARDS = REPO / "docs" / "standards"
REGISTRY_PATH = STANDARDS / "INTERFACE_REGISTRY_v1.yaml"
IFS_DOC = STANDARDS / "2026-11-03_接口对接规范_IFS_v1.md"
ID_CHAIN_DOC = STANDARDS / "2026-11-03_ID_CHAIN_v1.md"
ENVELOPE_DOC = STANDARDS / "2026-11-03_Envelope_Error_v1.md"
MATRIX_DOC = STANDARDS / "2026-11-03_OWNER_CONSUMER_SLO_MATRIX.md"
ENVELOPE_SCHEMA_PATH = STANDARDS / "schemas" / "interface_envelope_v1.schema.json"
ERROR_SCHEMA_PATH = STANDARDS / "schemas" / "interface_error_v1.schema.json"

REQUIRED_INTERFACE_FIELDS = (
    "id",
    "method",
    "path",
    "owner",
    "consumer",
    "source_of_truth",
    "schema",
    "auth",
    "write",
    "paper_only",
    "SLO",
    "status",
)
CHAIN_ORDER = (
    "pool_id",
    "card_id",
    "case_id",
    "decision_id",
    "handoff_id",
    "watch_plan_id",
    "trigger_id",
    "confirmation_id",
    "trade_id",
    "outcome_id",
    "training_episode_id",
)
CHAIN_SAMPLES = {
    "pool_id": "ACAD1-REC-20261102",
    "card_id": "ACAD1-CARD-20261102-003006",
    "case_id": "D1-CASE-20261102-003006",
    "decision_id": "D1-20261102-8f3a1c2b90",
    "handoff_id": "d1h-20261102170000-a1b2c3",
    "watch_plan_id": "wp-20261102-a1b2c3",
    "trigger_id": "trg-20261103-0f1e2d",
    "confirmation_id": "CFM-1A2B3C4D5E6F",
    "trade_id": "trd-20261103-1A2B3C4D",
    "outcome_id": "out-20261104-A1B2C3D4",
    "training_episode_id": "ep-20261104-B1C2D3E4",
}
ENVELOPE_REQUIRED = ["ok", "schema_version", "request_id", "source", "data", "warnings"]
ERROR_REQUIRED = ["ok", "schema_version", "request_id", "error"]
ERROR_DETAIL_REQUIRED = ["code", "message", "retryable", "details"]
REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{32}$")
SCHEMA_VERSION_RE = re.compile(r"^[a-z0-9_.-]+/v[0-9]+$")
PATH_RE = re.compile(r"^(/|internal:|mcp:|manifest:)")
VALID_AUTH = ("portal-token", "portal-token+roles", "internal-only", "mcp-config", "manifest")

SAMPLE_REQUEST_ID = "req-0123456789abcdef0123456789abcdef"
SAMPLE_SUCCESS = {
    "ok": True,
    "schema_version": "d1.research/v1",
    "request_id": SAMPLE_REQUEST_ID,
    "generated_at": "2026-11-03T07:05:00+08:00",
    "source": {
        "interface_id": "d1.research.get",
        "source_of_truth": "agent_platform/data/pool_review/",
        "source_mode": "aggregate",
        "as_of": "2026-11-03",
        "freshness_s": 3600,
        "stale": False,
    },
    "data": {"items": []},
    "warnings": [],
}
SAMPLE_ERROR = {
    "ok": False,
    "schema_version": "error/v1",
    "request_id": SAMPLE_REQUEST_ID,
    "error": {
        "code": "bad_target_action",
        "message": "watch 只能交 WA1/WA2/WA3/WATCH_TOWER",
        "retryable": False,
        "details": {"field": "target"},
    },
}


# ── 契约测试骨架：信封校验模板（后续接口直接复用） ───────────────────
def validate_envelope(payload):
    """校验成功信封，返回错误列表（空列表 = 通过；不依赖真实服务）。"""
    errors = []
    if not isinstance(payload, dict):
        return ["payload 必须为 object"]
    for field in ENVELOPE_REQUIRED:
        if field not in payload:
            errors.append("missing:%s" % field)
    if payload.get("ok") is not True:
        errors.append("ok 必须为 true")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not SCHEMA_VERSION_RE.match(schema_version):
        errors.append("schema_version 非法: %r" % (schema_version,))
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        errors.append("request_id 非法: %r" % (request_id,))
    source = payload.get("source")
    if not isinstance(source, dict):
        errors.append("source 必须为 object")
    else:
        for field in ("interface_id", "source_of_truth", "source_mode"):
            if not isinstance(source.get(field), str) or not source.get(field):
                errors.append("source.%s 缺失/非法" % field)
    if not isinstance(payload.get("warnings"), list):
        errors.append("warnings 必须为 list（无告警用 []）")
    return errors


def validate_error(payload, code=None):
    """校验错误信封，返回错误列表（空列表 = 通过）。"""
    errors = []
    if not isinstance(payload, dict):
        return ["payload 必须为 object"]
    for field in ERROR_REQUIRED:
        if field not in payload:
            errors.append("missing:%s" % field)
    if payload.get("ok") is not False:
        errors.append("ok 必须为 false")
    if payload.get("schema_version") != "error/v1":
        errors.append("schema_version 必须为 error/v1")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        errors.append("request_id 非法: %r" % (request_id,))
    detail = payload.get("error")
    if not isinstance(detail, dict):
        errors.append("error 必须为 object")
    else:
        for field in ERROR_DETAIL_REQUIRED:
            if field not in detail:
                errors.append("error 缺 %s" % field)
        if code is not None and detail.get("code") != code:
            errors.append("error.code 应为 %r，实际 %r" % (code, detail.get("code")))
        if not isinstance(detail.get("retryable"), bool):
            errors.append("error.retryable 必须为 bool")
        if not isinstance(detail.get("details"), dict):
            errors.append("error.details 必须为 object")
    return errors


# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def registry():
    assert REGISTRY_PATH.is_file(), "缺接口登记表: %s" % REGISTRY_PATH
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def interfaces(registry):
    return registry["interfaces"]


@pytest.fixture(scope="module")
def envelope_schema():
    return json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def error_schema():
    return json.loads(ERROR_SCHEMA_PATH.read_text(encoding="utf-8"))


# ── 1. registry 元信息 / 枚举 / 席位目录 ─────────────────────────────
class TestRegistryMeta:
    def test_yaml_parses_to_object(self, registry):
        assert isinstance(registry, dict)
        assert registry["schema_version"] == "interface_registry/v1"
        assert registry["registry_version"]
        assert registry["updated_at"]
        assert registry["source_plan"].endswith("IFS_v1与整改计划.md")

    def test_enums_and_catalogs(self, registry):
        assert registry["method_enum"] == ["GET", "POST", "INTERNAL", "MCP", "MANIFEST"]
        assert registry["status_enum"] == ["draft", "v1", "deprecated", "retired"]
        assert "portal-token" in registry["auth_enum"]
        assert isinstance(registry["slo_classes"], dict) and len(registry["slo_classes"]) >= 5
        assert isinstance(registry["owners"], dict) and len(registry["owners"]) >= 5
        for owner, meta in registry["owners"].items():
            assert isinstance(owner, str) and owner
            for field in ("dept", "role", "oncall", "escalation"):
                assert meta.get(field), "owner %s 缺 %s" % (owner, field)

    def test_standards_paths_point_to_committed_docs(self, registry):
        for key, rel in registry["standards"].items():
            path = REPO / rel
            assert path.is_file(), "standards.%s 指向的文件不存在: %s" % (key, path)

    def test_counts_anchor_matches_interfaces(self, registry, interfaces):
        counts = registry["counts"]
        assert counts["interfaces"] == len(interfaces)
        assert counts["interfaces"] >= 20
        assert counts["id_chain_entities"] == len(CHAIN_ORDER)


# ── 2. 接口字段 / 唯一性 / 取值域 ────────────────────────────────────
class TestInterfaces:
    def test_interface_count_at_least_20(self, interfaces):
        assert len(interfaces) >= 20, "接口登记数不足 20：%d" % len(interfaces)

    def test_required_fields_present(self, interfaces):
        for item in interfaces:
            missing = [field for field in REQUIRED_INTERFACE_FIELDS if field not in item]
            assert not missing, "%s 缺字段 %s" % (item.get("id"), missing)

    def test_field_types_and_domains(self, registry, interfaces):
        for item in interfaces:
            iid = item["id"]
            assert isinstance(iid, str) and re.match(r"^[a-z0-9][a-z0-9_.-]*$", iid), iid
            assert item["method"] in registry["method_enum"], iid
            assert isinstance(item["path"], str) and PATH_RE.match(item["path"]), iid
            assert item["owner"] in registry["owners"], iid
            assert item["status"] in registry["status_enum"], iid
            assert item["auth"] in VALID_AUTH, iid
            assert isinstance(item["write"], bool), iid
            assert item["paper_only"] is True, "%s 必须 paper_only=true（IFS-05）" % iid
            assert isinstance(item["consumer"], list) and item["consumer"], iid
            assert all(isinstance(name, str) and name for name in item["consumer"]), iid
            assert SCHEMA_VERSION_RE.match(item["schema"]), iid

    def test_source_of_truth_shape(self, interfaces):
        for item in interfaces:
            source = item["source_of_truth"]
            assert isinstance(source, dict), item["id"]
            assert source.get("kind") in ("pg_table", "catalog", "file", "ledger", "db", "dataset", "aggregate", "manifest"), item["id"]
            assert isinstance(source.get("ref"), str) and source["ref"], item["id"]
            assert isinstance(source.get("note", ""), str), item["id"]

    def test_unique_ids_and_endpoints(self, interfaces):
        ids = [item["id"] for item in interfaces]
        assert len(ids) == len(set(ids)), "interface id 重复"
        endpoints = [(item["method"], item["path"]) for item in interfaces]
        assert len(endpoints) == len(set(endpoints)), "method+path 重复"

    def test_slo_hits_registered_class(self, registry, interfaces):
        for item in interfaces:
            slo_class = item.get("slo_class")
            assert slo_class in registry["slo_classes"], "%s 未知 slo_class=%r" % (item["id"], slo_class)
            assert item["SLO"] == registry["slo_classes"][slo_class], item["id"]
            slo = item["SLO"]
            assert isinstance(slo["latency_ms_p95"], int) and slo["latency_ms_p95"] > 0
            assert 0 <= slo["error_rate_max"] <= 1
            assert 0 < slo["availability"] <= 1

    def test_write_semantics(self, interfaces):
        for item in interfaces:
            writes = item["write"]
            iid = item["id"]
            if item["method"] in ("GET", "MCP", "MANIFEST"):
                assert writes is False, "%s 只读方法不得 write=true" % iid
            if writes:
                assert item["auth"] in ("portal-token+roles", "internal-only"), iid
            else:
                assert item["auth"] in VALID_AUTH, iid

    def test_status_counts_match(self, registry, interfaces):
        counts = registry["counts"]
        by_status = {}
        by_owner = {}
        for item in interfaces:
            by_status[item["status"]] = by_status.get(item["status"], 0) + 1
            by_owner[item["owner"]] = by_owner.get(item["owner"], 0) + 1
        assert by_status == {key: value for key, value in counts["by_status"].items() if value}, (
            "status 统计漂移: %s vs %s" % (by_status, counts["by_status"])
        )
        assert by_owner == counts["by_owner"], "owner 统计漂移: %s vs %s" % (by_owner, counts["by_owner"])
        assert sum(1 for item in interfaces if item["write"]) == counts["write_interfaces"]
        assert sum(1 for item in interfaces if not item["write"]) == counts["read_interfaces"]

    def test_remediation_for_unfinished_interfaces(self, interfaces):
        for item in interfaces:
            if item["status"] in ("draft", "deprecated"):
                remediation = item.get("remediation")
                assert isinstance(remediation, dict), "%s 缺 remediation" % item["id"]
                assert remediation.get("phase"), item["id"]
                assert remediation.get("wo"), item["id"]

    def test_deprecated_have_replacement_notes(self, registry, interfaces):
        deprecated = [item for item in interfaces if item["status"] == "deprecated"]
        assert deprecated, "应至少登记 1 个 deprecated 旧入口（F4 收敛）"
        for item in deprecated:
            assert item.get("notes"), "%s 缺收敛说明" % item["id"]


# ── 3. 统一 ID chain ─────────────────────────────────────────────────
class TestIdChain:
    def test_order_matches_spec(self, registry):
        assert tuple(registry["id_chain"]["order"]) == CHAIN_ORDER

    def test_entities_complete(self, registry):
        entities = registry["id_chain"]["entities"]
        assert set(entities) == set(CHAIN_ORDER)
        assert len(entities) == 11

    def test_adjacency_consistent(self, registry):
        entities = registry["id_chain"]["entities"]
        for index in range(len(CHAIN_ORDER) - 1):
            upstream = CHAIN_ORDER[index]
            downstream = CHAIN_ORDER[index + 1]
            assert downstream in entities[upstream]["downstream"], upstream
            assert upstream in entities[downstream]["upstream"], downstream
        assert entities[CHAIN_ORDER[0]]["upstream"] == []
        assert entities[CHAIN_ORDER[-1]]["downstream"] == []

    def test_formats_compile_and_match_samples(self, registry):
        entities = registry["id_chain"]["entities"]
        for name in CHAIN_ORDER:
            pattern = entities[name]["format"]
            compiled = re.compile(pattern)
            sample = CHAIN_SAMPLES[name]
            assert compiled.fullmatch(sample), "%s 格式不匹配样例 %r" % (name, sample)

    def test_generator_present_and_known(self, registry, interfaces):
        registry_ids = {item["id"] for item in interfaces}
        for name in CHAIN_ORDER:
            generator = registry["id_chain"]["entities"][name]["generator"]
            assert generator.get("owner") in registry["owners"], name
            assert generator.get("interface") in registry_ids, name
            assert generator.get("component"), name
            assert generator.get("status") in ("v1", "draft"), name

    def test_source_of_truth_present(self, registry):
        for name in CHAIN_ORDER:
            source = registry["id_chain"]["entities"][name]["source_of_truth"]
            assert isinstance(source, dict), name
            assert source.get("kind"), name
            assert source.get("ref"), name

    def test_no_gaps(self, registry):
        entities = registry["id_chain"]["entities"]
        gaps = set(registry["id_chain"]["gaps"])
        draft_entities = {name for name, spec in entities.items() if spec["status"] == "draft"}
        # decision_id 属双格式偏差（deviations 登记），不算空洞
        assert gaps == draft_entities - {"decision_id"}, "空洞登记与实体状态不一致"

    def test_single_producer_per_entity(self, interfaces):
        producers = {name: [] for name in CHAIN_ORDER}
        for item in interfaces:
            produced = (item.get("ids") or {}).get("produces") or []
            for entity in produced:
                assert entity in producers, "%s 产出未登记实体 %r" % (item["id"], entity)
                producers[entity].append(item["id"])
        for name, owners in producers.items():
            assert len(owners) == 1, "%s 必须唯一生成方，实际 %s" % (name, owners)

    def test_interface_ids_subset_chain(self, interfaces):
        for item in interfaces:
            ids = item.get("ids") or {}
            for entity in (ids.get("produces") or []) + (ids.get("consumes") or []):
                assert entity in CHAIN_ORDER, "%s 引用链外 ID: %r" % (item["id"], entity)

    def test_consumes_have_producers(self, interfaces):
        registry_entities = set(CHAIN_ORDER)
        produced = set()
        for item in interfaces:
            produced.update((item.get("ids") or {}).get("produces") or [])
        assert produced <= registry_entities
        for item in interfaces:
            for entity in (item.get("ids") or {}).get("consumes") or []:
                assert entity in produced, "%s 消费的 %r 无生成方（ID 链空洞）" % (item["id"], entity)

    def test_deviations_documented(self, registry):
        deviations = registry["id_chain"]["deviations"]
        assert isinstance(deviations, list) and deviations
        ids = {item["id"] for item in deviations}
        assert {"decision-id-dual-format", "receipt-id-alias", "card-id-proxy"} <= ids
        for item in deviations:
            assert item.get("current") and item.get("target") and item.get("wo"), item["id"]

    def test_invariants_registered(self, registry):
        invariants = registry["id_chain"]["invariants"]
        assert isinstance(invariants, list) and len(invariants) >= 5
        joined = " ".join(invariants)
        for keyword in ("唯一", "上游", "幂等"):
            assert keyword in joined, "不变量缺关键词: %s" % keyword


# ── 4. envelope / error 规范与 JSON Schema ───────────────────────────
class TestEnvelopeError:
    def test_envelope_annex(self, registry):
        envelope = registry["envelope"]
        assert envelope["success_required"] == ENVELOPE_REQUIRED
        assert envelope["error_required"] == ERROR_REQUIRED
        assert envelope["error_schema_version"] == "error/v1"
        assert envelope["error_required_detail"] == ERROR_DETAIL_REQUIRED
        assert "interface_id" in envelope["success_constraints"]["source"]
        assert envelope["success_constraints"]["ok"] is True

    def test_error_codes_shape(self, registry):
        codes = registry["envelope"]["error_codes"]
        assert isinstance(codes, dict) and len(codes) >= 12
        for code, spec in codes.items():
            assert isinstance(code, str) and code
            assert isinstance(spec["http"], int) and 400 <= spec["http"] <= 599 or spec["http"] == 200
            assert isinstance(spec["retryable"], bool)
            assert isinstance(spec["scene"], str) and spec["scene"]

    def test_error_schema_matches_annex(self, registry, error_schema):
        assert error_schema["required"] == ERROR_REQUIRED
        assert error_schema["properties"]["schema_version"]["const"] == "error/v1"
        assert error_schema["properties"]["request_id"]["pattern"] == REQUEST_ID_RE.pattern
        detail = error_schema["properties"]["error"]
        assert detail["required"] == ERROR_DETAIL_REQUIRED
        assert set(detail["properties"]["code"]["enum"]) == set(
            registry["envelope"]["error_codes"]
        )

    def test_envelope_schema_matches_annex(self, envelope_schema):
        assert envelope_schema["required"] == ENVELOPE_REQUIRED
        assert envelope_schema["properties"]["ok"]["const"] is True
        source_required = envelope_schema["properties"]["source"]["required"]
        assert set(source_required) == {"interface_id", "source_of_truth", "source_mode"}
        assert envelope_schema["properties"]["warnings"]["type"] == "array"

    def test_validate_envelope_sample(self):
        assert validate_envelope(SAMPLE_SUCCESS) == []
        assert validate_envelope([1, 2]) == ["payload 必须为 object"]

    def test_validate_error_sample(self):
        assert validate_error(SAMPLE_ERROR) == []
        assert validate_error(SAMPLE_ERROR, code="bad_target_action") == []
        assert validate_error(SAMPLE_ERROR, code="not_found")

    def test_validators_reject_bad_payloads(self):
        bad_success = dict(SAMPLE_SUCCESS)
        bad_success["schema_version"] = ""
        assert validate_envelope(bad_success)
        bad_success2 = dict(SAMPLE_SUCCESS)
        bad_success2["ok"] = False
        assert validate_envelope(bad_success2)
        missing = dict(SAMPLE_SUCCESS)
        missing.pop("warnings")
        assert "missing:warnings" in validate_envelope(missing)
        bad_error = json.loads(json.dumps(SAMPLE_ERROR))
        bad_error["error"].pop("retryable")
        assert any("retryable" in error for error in validate_error(bad_error))
        bad_code = json.loads(json.dumps(SAMPLE_ERROR))
        bad_code["error"]["code"] = "not_registered_code"
        assert validate_error(bad_code, code="bad_target_action")

    def test_schema_files_parse(self, envelope_schema, error_schema):
        assert envelope_schema["$id"] == "interface_envelope.v1"
        assert error_schema["$id"] == "interface_error.v1"
        assert envelope_schema["type"] == error_schema["type"] == "object"


# ── 5. F0 文档交付物交叉校验 ─────────────────────────────────────────
class TestDeliverableDocs:
    def test_required_documents_exist(self):
        for path in (IFS_DOC, ID_CHAIN_DOC, ENVELOPE_DOC, MATRIX_DOC):
            assert path.is_file(), "缺交付物: %s" % path

    def test_ifs_has_fourteen_clauses(self):
        text = IFS_DOC.read_text(encoding="utf-8")
        clauses = re.findall(r"^### (IFS-\d{2}) ", text, flags=re.MULTILINE)
        assert clauses == ["IFS-%02d" % index for index in range(1, 15)], clauses

    def test_ifs_references_companion_deliverables(self):
        text = IFS_DOC.read_text(encoding="utf-8")
        for keyword in (
            "INTERFACE_REGISTRY_v1.yaml",
            "2026-11-03_ID_CHAIN_v1.md",
            "2026-11-03_Envelope_Error_v1.md",
            "2026-11-03_OWNER_CONSUMER_SLO_MATRIX.md",
            "无登记不接单",
        ):
            assert keyword in text, "IFS 规范缺引用/条款: %s" % keyword

    def test_id_chain_doc_lists_entities_in_order(self):
        text = ID_CHAIN_DOC.read_text(encoding="utf-8")
        positions = []
        for name in CHAIN_ORDER:
            position = text.find("`%s`" % name)
            assert position >= 0, "ID chain 文档缺实体: %s" % name
            positions.append(position)
        assert positions == sorted(positions), "ID chain 文档实体顺序与定义不一致"

    def test_envelope_doc_lists_all_error_codes(self, registry):
        text = ENVELOPE_DOC.read_text(encoding="utf-8")
        for code in registry["envelope"]["error_codes"]:
            assert code in text, "envelope 文档缺错误码: %s" % code
        for keyword in ("retryable", "details", "request_id", "schema_version"):
            assert keyword in text

    def test_matrix_doc_covers_all_interfaces(self, registry, interfaces):
        text = MATRIX_DOC.read_text(encoding="utf-8")
        for item in interfaces:
            assert item["id"] in text, "matrix 缺接口: %s" % item["id"]
        for owner in {item["owner"] for item in interfaces}:
            assert owner in text, "matrix 缺 owner: %s" % owner
        assert "SLO" in text and "paper_only" in text

    def test_matrix_does_not_drift_from_registry_counts(self, registry, interfaces):
        """矩阵中声明的统计必须与 registry 锚点一致（防文档漂移）。"""
        text = MATRIX_DOC.read_text(encoding="utf-8")
        counts = registry["counts"]
        assert "draft %d" % counts["by_status"]["draft"] in text
        assert "v1 %d" % counts["by_status"]["v1"] in text
        assert "deprecated %d" % counts["by_status"]["deprecated"] in text
        assert "write=true %d" % counts["write_interfaces"] in text
        assert "read-only %d" % counts["read_interfaces"] in text
