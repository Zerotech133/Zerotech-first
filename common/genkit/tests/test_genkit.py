# -*- coding: utf-8 -*-
"""genkit Phase 1 基线工具包单元测试（G1–G8 公共件承重）。

覆盖 timezone / paths / fail_visible / schema / contract 全部公共 API 与边界。
运行：``python -m pytest common/genkit/tests/test_genkit.py -q``（仓库根目录）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.genkit import (
    CST,
    UTC,
    now_cst,
    utcnow,
    to_cst,
    to_utc,
    format_cst,
    cst_date_str,
    cst_month_str,  # noqa: F401
)
from common.genkit import (
    resolve_repo_root,
    resolve_path,
    resolve_project_root,
)
from common.genkit import (
    read_json_visible,
    safe_json_loads,
    truncation_note,
    list_or_empty,
)
from common.genkit import (
    SchemaViolation,
    field_state,
    validate_required,
    check_version,
)
from common.genkit import (
    assert_json_contract,
    assert_nonempty_generated,
    load_generated_json,
)


# ---------------------------------------------------------------- G1 timezone
def test_now_cst_is_tz_aware_and_utc8():
    dt = now_cst()
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_to_cst_naive_treated_as_cst():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert to_cst(naive).utcoffset().total_seconds() == 8 * 3600


def test_to_cst_from_utc():
    u = datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC)  # 04:00Z = 12:00 CST
    assert to_cst(u).hour == 12


def test_format_cst_carries_offset():
    s = format_cst(datetime(2026, 1, 1, 12, 0, 0))
    assert "+08:00" in s


def test_cst_date_and_month_str_format():
    dt = datetime(2026, 3, 15, 23, 30, 0, tzinfo=UTC)  # 03-16 07:30 CST
    assert cst_date_str(dt) == "2026-03-16"
    assert cst_month_str(dt) == "2026-03"


def test_cst_date_str_default_not_naive():
    s = cst_date_str()
    assert len(s) == 10 and s[4] == "-" and s[7] == "-"


# ---------------------------------------------------------------- G2 paths
def test_resolve_repo_root_default_and_env(monkeypatch):
    monkeypatch.delenv("AIOS_ROOT", raising=False)
    assert resolve_repo_root() == Path("C:/aios")
    monkeypatch.setenv("AIOS_ROOT", "D:/ci_root")
    assert resolve_repo_root() == Path("D:/ci_root")


def test_resolve_path_routes_through_root(monkeypatch):
    monkeypatch.setenv("AIOS_ROOT", "D:/ci_root")
    p = resolve_path("automation_runs", "xq_report", "latest.json")
    assert p == Path("D:/ci_root/automation_runs/xq_report/latest.json")


def test_resolve_project_root_default():
    assert resolve_project_root() == Path("C:/项目/老田的AI助手")


# ------------------------------------------------------- G3/G6/G7 fail_visible
def test_read_json_visible_missing(tmp_path):
    data, exists, reason = read_json_visible(tmp_path / "nope.json")
    assert data is None and exists is False
    assert reason.startswith("source_missing:")


def test_read_json_visible_parse_error(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    data, exists, reason = read_json_visible(f)
    assert data is None and exists is False
    assert reason.startswith("parse_error:")


def test_read_json_visible_ok(tmp_path):
    f = tmp_path / "ok.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")
    data, exists, reason = read_json_visible(f)
    assert data == {"a": 1} and exists is True and reason == ""


def test_safe_json_loads_roundtrip():
    data, exists, _ = safe_json_loads('{"x": 2}')
    assert data == {"x": 2} and exists is True


def test_truncation_note_empty_when_full():
    assert truncation_note(100, 100, "报告") == ""
    assert truncation_note(200, 100, "报告") == ""


def test_truncation_note_declares_kept_total():
    note = truncation_note(50, 200, "走查报告")
    assert "已截断 50/200" in note and "走查报告" in note


def test_list_or_empty_handles_null_missing_list_scalar():
    assert list_or_empty({"k": None}, "k") == []
    assert list_or_empty({}, "k") == []
    assert list_or_empty({"k": [1, 2]}, "k") == [1, 2]
    assert list_or_empty({"k": 7}, "k") == [7]


# ---------------------------------------------------------------- G5 schema
def test_field_state_three_way():
    assert field_state({}, "k") == "missing"
    assert field_state({"k": None}, "k") == "null"
    assert field_state({"k": 1}, "k") == "present"


def test_validate_required_reports_missing_and_null():
    v = validate_required({"a": 1, "b": None}, ["a", "b", "c"])
    kinds = {x.field: x.kind for x in v}
    assert kinds == {"b": "null_value", "c": "missing_key"}


def test_check_version_mismatch_and_ok():
    assert check_version({"version": "v3"}, "v3") is None
    bad = check_version({"version": "v2"}, "v3")
    assert isinstance(bad, SchemaViolation) and bad.kind == "version_mismatch"
    missing = check_version({}, "v3")
    assert missing.kind == "missing_key"


# ---------------------------------------------------------------- G8 contract
def test_assert_json_contract_ok_and_fail():
    assert_json_contract({"meta": 1, "summary": 2}, ["meta", "summary"])
    with pytest.raises(AssertionError):
        assert_json_contract({"meta": 1}, ["meta", "summary"])


def test_assert_nonempty_generated_rejects_naive():
    with pytest.raises(AssertionError):
        assert_nonempty_generated({"generated": "2026-01-01T12:00:00"}, "generated")
    # 带时区偏移应放行
    assert_nonempty_generated({"generated": "2026-01-01T12:00:00+08:00"}, "generated")


def test_load_generated_json_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_generated_json(tmp_path / "absent.json")


def test_sample_contract_test_pattern(tmp_path):
    """G8 脚手架演示：一个微型生成器产出 JSON，契约测试断言其结构 + G1 时区。

    该用例同时证明 genkit 自身可驱动「生成 → 落盘 → 契约校验」闭环，
    供 Phase 2 各生成器直接套用。
    """
    from common.genkit import now_cst, format_cst

    out = tmp_path / "ledger.json"
    payload = {
        "meta": {"pool": "x"},
        "summary": {"nav": 1.0},
        "generated": format_cst(now_cst()),  # G1：带时区
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_generated_json(out)
    assert_json_contract(loaded, required=["meta", "summary", "generated"])
    assert_nonempty_generated(loaded, "generated")
