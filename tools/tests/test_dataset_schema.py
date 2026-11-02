# -*- coding: utf-8 -*-
"""dataset schema v0.1 + K5 登记测试（ADR-018 D6/D7 / ADR-019 D6/D7，task-24）。

覆盖：schema 可加载/draft-07 元校验、5 种 kind 合法样例、缺 required/非法 kind、各 kind 专项约束、
pg_table 三种定位形态、contract_artifact 的 sha256/retention、additionalProperties 扩展、K5 登记与 kernel_guard 生效。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft7Validator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
SCHEMA_PATH = ROOT / "platform" / "schemas" / "dataset.schema.json"
KERNEL_PATH = ROOT / "platform" / "kernel_v1.yaml"

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft7Validator(SCHEMA)

BASE = {"id": "ds-example", "name": "示例数据集", "kind": "dir",
        "owner": "DATA1", "domain": "接口数据域"}
SHA = "a" * 64


def _valid(**extra) -> dict:
    d = dict(BASE)
    d.update(extra)
    return d


def _errors(instance: dict) -> list:
    return list(VALIDATOR.iter_errors(instance))


def _run(args):
    p = subprocess.run([PY, "-X", "utf8", *args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


# ---------- schema 自身 ----------
def test_schema_meta_valid_draft07():
    assert SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert SCHEMA["title"] == "zerobound/dataset/v0.1"
    Draft7Validator.check_schema(SCHEMA)  # 元校验：schema 自身合法
    assert SCHEMA.get("type") == "object"
    assert SCHEMA.get("additionalProperties") is True


def test_schema_required_and_kind_enum():
    assert set(SCHEMA["required"]) == {"id", "name", "kind", "owner", "domain"}
    kinds = SCHEMA["properties"]["kind"]["enum"]
    assert kinds == ["dir", "sqlite_table", "corpus_layer", "pg_table", "contract_artifact"]
    # 版本/兼容策略必须可读（v0.1 冻结、新增 kind 须 ADR）
    policy = SCHEMA["description"] + SCHEMA.get("$comment", "")
    assert "冻结" in policy and "ADR" in policy


# ---------- 合法样例（5 种 kind） ----------
@pytest.mark.parametrize("sample", [
    _valid(path="quant_backtest/data"),
    _valid(kind="sqlite_table", path="research/market.db", table="market_data"),
    _valid(kind="corpus_layer", path="知识库/corpus"),
    _valid(kind="pg_table", schema="public", table="v2_acad1_pool"),
    _valid(kind="pg_table", physical={"schema": "public", "table": "v2_acad1_pool"},
           connector="pg@127.0.0.1:5446/zhice_os_v2"),
    _valid(kind="pg_table", pg_table="v2_acad1_pool"),
    _valid(kind="contract_artifact", artifact_id="acad1_evidence_bundle", sha256=SHA,
           owner="ACAD1", retention_days=30,
           path="C:\\Temp\\acad1_evidence_bundle.json",
           produced_by_job="job/acad1-evidence", schema_ref="acad1-evidence/v1"),
])
def test_valid_samples_pass(sample):
    errs = _errors(sample)
    assert not errs, [e.message for e in errs]


# ---------- 缺 required / 非法 kind ----------
@pytest.mark.parametrize("missing", ["id", "name", "kind", "owner", "domain"])
def test_missing_required_rejected(missing):
    sample = dict(BASE)
    sample.pop(missing)
    if missing == "kind":
        sample.pop("kind", None)
    assert _errors(sample), "缺 %s 应被拒" % missing


def test_invalid_kind_rejected():
    assert _errors(_valid(kind="pgsql", path="x"))


# ---------- 各 kind 专项约束 ----------
@pytest.mark.parametrize("sample", [
    _valid(kind="dir"),                                    # dir 缺 path
    _valid(kind="sqlite_table", path="a.db"),              # sqlite 缺 table
    _valid(kind="corpus_layer"),                           # corpus 缺 path
    _valid(kind="pg_table"),                               # pg_table 无任何定位信息
    _valid(kind="pg_table", physical={"schema": "public"}),  # physical 缺 table
    _valid(kind="contract_artifact", sha256=SHA, owner="ACAD1",
           retention_days=30),                             # 缺 artifact_id
    _valid(kind="contract_artifact", artifact_id="a_b_c", sha256=SHA,
           owner="ACAD1"),                                 # 缺 retention_days
    _valid(kind="contract_artifact", artifact_id="a_b_c", sha256="xyz",
           owner="ACAD1", retention_days=30),              # sha256 非 64hex
])
def test_kind_specific_rejected(sample):
    assert _errors(sample), "应被拒：%r" % sample


def test_contract_artifact_sha256_null_allowed():
    sample = _valid(kind="contract_artifact", artifact_id="a_b_c", sha256=None,
                    owner="ACAD1", retention_days=7)
    assert not _errors(sample), "sha256 首次登记可 null（ADR-019 D4）"


def test_additional_properties_allowed():
    sample = _valid(path="quant_backtest/data", registered=True, exposed=False,
                    access="read_only", ref_aliases=["quant_data"],
                    unified_entry=True, available=True, row_estimate=None,
                    freshness=None, vendor_extra={"x": 1})
    assert not _errors(sample)


# ---------- K5 登记（kernel_v1.yaml） ----------
def _k5_artifacts() -> list[dict]:
    data = yaml.safe_load(KERNEL_PATH.read_text(encoding="utf-8")) or {}
    k5 = (data.get("contracts") or {}).get("K5_data_evidence") or {}
    return list(k5.get("artifacts") or [])


def test_kernel_k5_contains_new_entries():
    rows = {a.get("path"): a for a in _k5_artifacts()}
    assert "agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml" in rows
    assert rows["agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml"]["kind"] == "contract"
    assert rows["agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml"]["status"] == "existing"
    assert "ADR-018" in rows["agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml"]["note"]

    schema_row = rows["platform/schemas/dataset.schema.json"]
    assert schema_row["kind"] == "schema"
    assert schema_row["status"] == "existing"
    assert "ADR-019" in schema_row["note"]
    # dataset_registry 条目补 schema 指针，不重复添加、不误改 status
    assert schema_row is not rows["tools/datawarehouse/dataset_registry.py"]
    assert rows["tools/datawarehouse/dataset_registry.py"]["status"] == "existing"
    assert "platform/schemas/dataset.schema.json" in rows["tools/datawarehouse/dataset_registry.py"]["note"]
    # evidence_bundle 不在本批范围，保持 planned
    assert rows["platform/schemas/evidence_bundle.schema.json"]["status"] == "planned"
    # 不重复添加
    paths = [a.get("path") for a in _k5_artifacts()]
    assert paths.count("platform/schemas/dataset.schema.json") == 1
    assert paths.count("agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml") == 1


# ---------- kernel_guard 生效（K5 冻结后的门禁语义） ----------
def test_kernel_guard_lists_new_artifacts():
    rc, out, err = _run(["tools/kernel_guard.py", "--list"])
    assert rc == 0, (rc, out, err)
    assert "platform/schemas/dataset.schema.json" in out
    assert "agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml" in out
    assert "K5_data_evidence" in out


def test_kernel_guard_blocks_schema_without_adr():
    rc, out, err = _run(["tools/kernel_guard.py",
                         "--files", "platform/schemas/dataset.schema.json"])
    assert rc == 1, (rc, out, err)
    assert "BLOCK" in out


def test_kernel_guard_allows_schema_with_adr_commit_msg():
    rc, out, err = _run(["tools/kernel_guard.py",
                         "--files", "platform/schemas/dataset.schema.json",
                         "--commit-msg", "feat(K5): dataset schema v0.1 ADR-019"])
    assert rc == 0, (rc, out, err)
    assert "ADR-019" in out


def test_kernel_guard_task_acceptance_command():
    """任务验收命令：kernel_v1.yaml 改动 + 提交信息带 ADR-018/ADR-019 → rc=0。"""
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml",
                         "--commit-msg", "ADR-018/ADR-019"])
    assert rc == 0, (rc, out, err)
    assert "ADR-018" in out or "ADR-019" in out
    rc2, out2, _ = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml"])
    assert rc2 == 1 and "BLOCK" in out2
