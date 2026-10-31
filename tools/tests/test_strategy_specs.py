# -*- coding: utf-8 -*-
"""量学战法规格库（research/strategy_specs/）与 CLI 的回归测试。

覆盖：
  1) 24 个 YAML 齐全且可加载、id 唯一；
  2) 任务 schema 必填字段齐全（含页码/证据页）；
  3) validate 全绿（函数与 CLI 双路径）；
  4) list --status untested 返回 17 项、tested 6 项、concept 1 项；
  5) 未知 id 报错退出非 0；
  6) get --id 的 JSON 输出可解析；
  7) validate 能拦住缺字段 / id 重复（负向用例，临时目录）。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "research" / "strategy_specs"
SCRIPT = REPO / "tools" / "research" / "strategy_specs.py"

_spec = importlib.util.spec_from_file_location("strategy_specs_tool", SCRIPT)
specs_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(specs_mod)  # type: ignore[union-attr]

EXPECTED = 24
REQUIRED_FIELDS = (
    "id",
    "name",
    "source",
    "chapter",
    "type",
    "params",
    "entry_rule",
    "exit_rule",
    "test_status",
    "notes",
    "updated_at",
)
VALID_TYPES = {"形态", "事件", "过滤器", "概念"}
VALID_STATUSES = {"tested_rejected", "tested_backtested", "untested", "concept"}


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(cwd or REPO),
    )


def load_all() -> list[dict]:
    return specs_mod.load_specs(SPEC_DIR)


# ---------------------------------------------------------------------------
# 1) 24 个 YAML 齐全可加载
# ---------------------------------------------------------------------------
def test_spec_files_complete_and_loadable():
    files = sorted(SPEC_DIR.glob("*.yaml"))
    assert len(files) == EXPECTED, f"规格文件应为 {EXPECTED} 个，实际 {len(files)}"
    for path in files:
        assert path.name[:3].isdigit(), f"文件名缺少三位序号前缀: {path.name}"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{path.name} 顶层不是 dict"

    specs = load_all()
    assert len(specs) == EXPECTED
    ids = [s["id"] for s in specs]
    assert len(set(ids)) == EXPECTED, "id 存在重复"
    assert all(isinstance(i, str) and i.startswith("LX") for i in ids)


# ---------------------------------------------------------------------------
# 2) 必填字段 / 页码 / 枚举
# ---------------------------------------------------------------------------
def test_required_fields_and_page_evidence():
    for spec in load_all():
        for field in REQUIRED_FIELDS:
            assert field in spec, f"{spec.get('id')} 缺少必填字段 {field}"
        assert isinstance(spec["params"], dict)
        assert spec["entry_rule"].strip() and spec["exit_rule"].strip()
        assert spec["type"] in VALID_TYPES, f"{spec['id']} type 非法: {spec['type']}"
        assert spec["test_status"] in VALID_STATUSES, f"{spec['id']} test_status 非法"
        assert any(k in spec for k in ("page_start", "page_end", "evidence_page")), (
            f"{spec['id']} 缺少页码/证据页"
        )
        if "page_start" in spec and "page_end" in spec:
            assert 1 <= spec["page_start"] <= spec["page_end"]
        assert isinstance(spec["evidence_page"], int) and spec["evidence_page"] > 0
        assert spec["evidence_quote"].strip()


# ---------------------------------------------------------------------------
# 3) validate 全绿（函数 + CLI）
# ---------------------------------------------------------------------------
def test_validate_function_all_green():
    report = specs_mod.validate_specs(SPEC_DIR)
    assert report["ok"] is True, report["errors"]
    assert report["errors"] == []
    assert report["count"] == EXPECTED


def test_validate_cli_exit_zero_and_reports_24():
    proc = run_cli("validate")
    assert proc.returncode == 0, proc.stderr
    assert "24/24" in proc.stdout or "校验通过" in proc.stdout


# ---------------------------------------------------------------------------
# 4) 状态分布：untested=17 / tested=6 / concept=1
# ---------------------------------------------------------------------------
def test_status_distribution_matches_registry():
    specs = load_all()
    untested = specs_mod.filter_specs(specs, "untested")
    tested = specs_mod.filter_specs(specs, "tested")
    concept = [s for s in specs if s["test_status"] == "concept"]
    assert len(untested) == 17, f"untested 应为 17 项，实际 {len(untested)}"
    assert len(tested) == 6, f"tested 应为 6 项，实际 {len(tested)}"
    assert len(concept) == 1
    assert len(untested) + len(tested) + len(concept) == EXPECTED
    for spec in tested:
        assert spec["test_status"] == "tested_rejected", "按 H24–H29 结论全部 rejected"
        assert spec["h_ids"], f"{spec['id']} 已测但未登记 h_ids"
        assert (REPO / spec["tested_ref"]).is_file(), f"{spec['id']} tested_ref 指向的报告不存在"


def test_list_untested_cli_returns_17():
    proc = run_cli("list", "--status", "untested", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "untested"
    assert payload["count"] == 17
    assert len(payload["items"]) == 17
    assert all(item["test_status"] == "untested" for item in payload["items"])


# ---------------------------------------------------------------------------
# 5) 未知 id 报错
# ---------------------------------------------------------------------------
def test_unknown_id_errors_nonzero():
    proc = run_cli("get", "--id", "LX99_NOT_EXIST")
    assert proc.returncode != 0
    assert "未找到" in (proc.stdout + proc.stderr)
    with pytest.raises(KeyError):
        specs_mod.get_spec(load_all(), "LX99_NOT_EXIST")


# ---------------------------------------------------------------------------
# 6) get --id / list --json 机器可读
# ---------------------------------------------------------------------------
def test_get_known_id_json_roundtrip():
    proc = run_cli("get", "--id", "LX02_GOLDEN_PILLAR", "--json")
    assert proc.returncode == 0, proc.stderr
    spec = json.loads(proc.stdout)
    assert spec["id"] == "LX02_GOLDEN_PILLAR"
    assert spec["test_status"] == "tested_rejected"
    assert spec["h_ids"] == ["H29"]
    assert spec["page_start"] == 74 and spec["page_end"] == 78


# ---------------------------------------------------------------------------
# 7) 负向用例：validate 能拦住缺字段 / id 重复（临时目录）
# ---------------------------------------------------------------------------
def _temp_specs(tmp_path: Path) -> Path:
    dst = tmp_path / "specs"
    shutil.copytree(SPEC_DIR, dst)
    return dst


def test_validate_detects_missing_required_field(tmp_path):
    spec_dir = _temp_specs(tmp_path)
    target = sorted(spec_dir.glob("*.yaml"))[0]
    lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if not ln.startswith("name:")]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = specs_mod.validate_specs(spec_dir)
    assert report["ok"] is False
    assert any("name" in item and "缺少必填字段" in item for item in report["errors"])

    proc = run_cli("validate", "--spec-dir", str(spec_dir))
    assert proc.returncode != 0
    assert "校验失败" in proc.stderr


def test_validate_detects_duplicate_id(tmp_path):
    spec_dir = _temp_specs(tmp_path)
    files = sorted(spec_dir.glob("*.yaml"))
    text = files[2].read_text(encoding="utf-8")
    dup_id = yaml.safe_load(files[1].read_text(encoding="utf-8"))["id"]
    old_id = yaml.safe_load(text)["id"]
    files[2].write_text(text.replace(f"id: {old_id}", f"id: {dup_id}"), encoding="utf-8")

    report = specs_mod.validate_specs(spec_dir)
    assert report["ok"] is False
    assert any("重复" in item for item in report["errors"])
