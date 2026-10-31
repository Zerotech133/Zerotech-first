# -*- coding: utf-8 -*-
"""outcome_contract_check：W3 outcome 契约只读检查器离线单测（不依赖生产账本）。

覆盖：正常 / 缺必需字段 / 坏行 / 重复 decision_id / 可空 hit-model / 空文件 /
--since legacy 口径 / 伪造 hit 守卫 / CLI 退出码与 JSON/Markdown 渲染。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "outcome_contract_check.py"


def _load():
    name = "outcome_contract_check_under_test"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # 兼容 dataclass/PEP 563 的模块注册约定
    spec.loader.exec_module(mod)
    return mod


def _valid_row(**overrides):
    row = {
        "decision_id": "D1-20261030-6d46c0beb1",
        "date": "2026-10-30",
        "symbol": "600519",
        "direction": "WATCH",
        "hit": None,
        "model": "glm/glm-5.3",
        "watch_reason": "l4_unresolved",
        "evidence_refs": [{"uri": "kline://600519/2026-10-30",
                           "hash": "abababababababab"}],
        "next_day_return": None,
        "next_close": None,
    }
    row.update(overrides)
    return row


def _write(path: Path, rows, bad_lines=None) -> Path:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    lines.extend(bad_lines or [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _check(tmp_path, rows, **kwargs):
    mod = _load()
    path = _write(tmp_path / "decision_outcomes.jsonl", rows)
    return mod.collect(path=path, **kwargs)


# ---------------------------------------------------------------------------
# 正常 / 可空字段
# ---------------------------------------------------------------------------
def test_valid_rows_pass(tmp_path):
    report = _check(tmp_path, [_valid_row(), _valid_row(
        decision_id="D1-20261030-a1b2c3d4e5", symbol="000858")])
    assert report["verdict"] == "pass"
    assert report["counts"]["rows"] == 2
    assert report["counts"]["valid_rows"] == 2
    assert report["counts"]["invalid_rows"] == 0
    assert report["violations"] == {}


def test_hit_model_nullable_pass(tmp_path):
    report = _check(tmp_path, [_valid_row(hit=None, model=None)])
    assert report["verdict"] == "pass"
    assert report["counts"]["hit_non_null"] == 0
    assert report["counts"]["model_non_null"] == 0


def test_hit_and_model_counts(tmp_path):
    rows = [
        _valid_row(hit=1, direction="BUY", next_day_return=0.01),
        _valid_row(decision_id="D1-20261030-a1b2c3d4e5", hit=None,
                   model="m1", direction="WATCH"),
        _valid_row(decision_id="D1-20261030-a1b2c3d4e6", hit=0, model=None,
                   direction="SELL", next_day_return=-0.02),
    ]
    report = _check(tmp_path, rows)
    assert report["counts"]["hit_non_null"] == 2
    assert report["counts"]["model_non_null"] == 2
    assert report["counts"]["hit_and_model"] == 1


# ---------------------------------------------------------------------------
# 必需字段 / 类型
# ---------------------------------------------------------------------------
def test_missing_decision_id_fails(tmp_path):
    row = _valid_row()
    row.pop("decision_id")
    report = _check(tmp_path, [row])
    assert report["verdict"] == "fail"
    assert report["missing"]["decision_id"] == 1
    assert report["violations"].get("missing_required") == 1


def test_missing_direction_fails(tmp_path):
    row = _valid_row()
    row.pop("direction")
    report = _check(tmp_path, [row])
    assert report["verdict"] == "fail"
    assert report["missing"]["direction"] == 1


def test_missing_date_and_symbol_fails(tmp_path):
    row = _valid_row()
    row.pop("date")
    row.pop("symbol")
    report = _check(tmp_path, [row])
    assert report["verdict"] == "fail"
    assert report["missing"]["date"] == 1
    assert report["missing"]["symbol"] == 1


def test_bad_date_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(date="2026-13-40")])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


def test_compact_date_pass(tmp_path):
    report = _check(tmp_path, [_valid_row(date="20261030")])
    assert report["verdict"] == "pass"


def test_hit_invalid_type_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(hit="yes")])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


def test_hit_out_of_range_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(hit=2)])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


def test_model_invalid_type_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(model=123)])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


def test_model_empty_string_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(model="  ")])
    assert report["verdict"] == "fail"


# ---------------------------------------------------------------------------
# watch_reason / evidence_refs 存在性
# ---------------------------------------------------------------------------
def test_missing_watch_reason_fails(tmp_path):
    row = _valid_row()
    row.pop("watch_reason")
    report = _check(tmp_path, [row])
    assert report["verdict"] == "fail"
    assert report["missing"]["watch_reason"] == 1
    assert report["violations"].get("missing_field") == 1


def test_missing_evidence_refs_fails(tmp_path):
    row = _valid_row()
    row.pop("evidence_refs")
    report = _check(tmp_path, [row])
    assert report["verdict"] == "fail"
    assert report["missing"]["evidence_refs"] == 1


def test_evidence_refs_empty_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(evidence_refs=[])])
    assert report["verdict"] == "fail"
    assert report["violations"].get("evidence_refs_empty") == 1


def test_evidence_refs_wrong_type_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(evidence_refs="kline://600519")])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


def test_watch_reason_wrong_type_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(watch_reason=123)])
    assert report["verdict"] == "fail"
    assert report["violations"].get("invalid_type") == 1


# ---------------------------------------------------------------------------
# 重复 / 坏行 / 空文件
# ---------------------------------------------------------------------------
def test_duplicate_decision_id_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(), _valid_row(
        symbol="000858", date="2026-10-29")])
    assert report["verdict"] == "fail"
    assert report["counts"]["duplicate_ids"] == 1
    assert report["duplicate_samples"][0]["lines"] == [1, 2]


def test_bad_json_line_fails(tmp_path):
    mod = _load()
    path = _write(tmp_path / "decision_outcomes.jsonl", [_valid_row()],
                  bad_lines=["{not-a-json"])
    report = mod.collect(path=path)
    assert report["verdict"] == "fail"
    assert report["counts"]["parse_errors"] == 1


def test_non_object_line_fails(tmp_path):
    mod = _load()
    path = _write(tmp_path / "decision_outcomes.jsonl", [_valid_row()],
                  bad_lines=["[]"])
    report = mod.collect(path=path)
    assert report["verdict"] == "fail"
    assert report["counts"]["parse_errors"] == 1


def test_empty_file_unknown(tmp_path):
    mod = _load()
    path = tmp_path / "decision_outcomes.jsonl"
    path.write_text("", encoding="utf-8")
    report = mod.collect(path=path)
    assert report["verdict"] == "unknown"
    assert report["counts"]["rows"] == 0


def test_missing_file_unknown(tmp_path):
    mod = _load()
    report = mod.collect(path=tmp_path / "nope.jsonl")
    assert report["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# 伪造 hit 守卫 / 告警
# ---------------------------------------------------------------------------
def test_hit_without_direction_fails(tmp_path):
    report = _check(tmp_path, [_valid_row(direction="WATCH", hit=1,
                                          next_day_return=0.01)])
    assert report["verdict"] == "fail"
    assert report["violations"].get("hit_without_direction") == 1


def test_hit_with_direction_pass(tmp_path):
    report = _check(tmp_path, [_valid_row(direction="BUY", hit=1,
                                          next_day_return=0.01)])
    assert report["verdict"] == "pass"


def test_direction_unknown_value_warns(tmp_path):
    report = _check(tmp_path, [_valid_row(direction="SIDEWAYS")])
    assert report["verdict"] == "pass"
    assert report["warnings"].get("direction_unknown_value") == 1


def test_decision_id_format_warns(tmp_path):
    report = _check(tmp_path, [_valid_row(decision_id="ABC123")])
    assert report["verdict"] == "pass"
    assert report["warnings"].get("decision_id_format") == 1


def test_hit_without_return_warns(tmp_path):
    report = _check(tmp_path, [_valid_row(direction="BUY", hit=1,
                                          next_day_return=None, next_close=None)])
    assert report["verdict"] == "pass"
    assert report["warnings"].get("hit_without_return") == 1


# ---------------------------------------------------------------------------
# --since：legacy 只查类型/重复/伪造 hit，不要求新字段
# ---------------------------------------------------------------------------
def test_since_legacy_not_required_but_strict_row_valid(tmp_path):
    legacy = _valid_row(date="2026-10-30")
    for field in ("decision_id", "direction", "watch_reason", "evidence_refs"):
        legacy.pop(field)
    strict = _valid_row(decision_id="D1-20261102-1122334455", date="2026-11-02")
    report = _check(tmp_path, [legacy, strict], since="2026-11-02")
    assert report["verdict"] == "pass"
    assert report["counts"]["legacy_rows"] == 1
    assert report["counts"]["strict_rows"] == 1
    assert report["missing"].get("decision_id") == 0


def test_since_strict_row_missing_field_fails(tmp_path):
    strict = _valid_row(date="2026-11-02")
    strict.pop("watch_reason")
    report = _check(tmp_path, [strict], since="2026-11-02")
    assert report["verdict"] == "fail"
    assert report["missing"]["watch_reason"] == 1


def test_since_no_matching_rows_unknown(tmp_path):
    legacy = _valid_row(date="2026-10-30")
    legacy.pop("decision_id")
    report = _check(tmp_path, [legacy], since="2026-11-02")
    assert report["verdict"] == "unknown"
    assert report["counts"]["strict_rows"] == 0


def test_since_bad_value_unknown(tmp_path):
    report = _check(tmp_path, [_valid_row()], since="not-a-date")
    assert report["verdict"] == "unknown"


def test_since_legacy_bad_type_still_fails(tmp_path):
    legacy = _valid_row(date="2026-10-30", hit="yes")
    legacy.pop("decision_id")
    report = _check(tmp_path, [legacy], since="2026-11-02")
    assert report["verdict"] == "fail"


# ---------------------------------------------------------------------------
# 渲染 / CLI
# ---------------------------------------------------------------------------
def test_report_json_serializable(tmp_path):
    report = _check(tmp_path, [_valid_row()])
    json.dumps(report, ensure_ascii=False)


def test_md_cell_escapes_pipe():
    mod = _load()
    assert mod._md_cell("a|b") == "a\\|b"


def test_render_markdown_contains_sections(tmp_path):
    mod = _load()
    path = _write(tmp_path / "decision_outcomes.jsonl", [_valid_row(hit="yes")])
    text = mod.render_markdown(mod.collect(path=path))
    assert "# W3 outcome 契约检查 · 只读" in text
    assert "| 指标 | 值 |" in text
    assert "违规分类" in text
    assert "只读声明" in text
    assert "未决口径" in text


def test_main_json_exit_codes(tmp_path, capsys):
    mod = _load()
    good = _write(tmp_path / "good.jsonl", [_valid_row()])
    bad = _write(tmp_path / "bad.jsonl", [_valid_row(hit="yes")])
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert mod.main(["--json", "--path", str(good)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    assert mod.main(["--json", "--path", str(bad)]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"
    assert mod.main(["--json", "--path", str(empty)]) == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "unknown"


def test_main_markdown_default_and_out(tmp_path, capsys):
    mod = _load()
    good = _write(tmp_path / "good.jsonl", [_valid_row()])
    out_file = tmp_path / "report.md"
    assert mod.main(["--path", str(good), "--out", str(out_file)]) == 0
    printed = capsys.readouterr().out
    assert "# W3 outcome 契约检查 · 只读" in printed
    assert out_file.read_text(encoding="utf-8").startswith("# W3 outcome")
