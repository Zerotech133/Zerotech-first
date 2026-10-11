import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
try:
    from audit_strategy_acceptance import audit_matrix, main
except ModuleNotFoundError:  # RED: implementation does not exist yet
    def audit_matrix(*args, **kwargs):
        raise NotImplementedError("audit_matrix 尚未实现")

    def main(*args, **kwargs):
        raise NotImplementedError("main 尚未实现")

REQUIRED = [
    "sample_count",
    "trading_days",
    "time_split",
    "source_hashes",
    "offline_review_receipt",
    "source_reference",
    "source_date_receipt",
    "benchmark",
    "cost_model",
    "missing_data_rejections",
    "transient_structure_snapshots",
]


def matrix(policy=None):
    return {
        "schema": "strategy_acceptance_matrix/v1",
        "global_requirements": [
            "source_reference",
            "source_date_receipt",
            "sample_sha256",
            "offline_review_receipt",
            "time_split",
            "split_group",
            "same_window_benchmark",
            "explicit_cost_model",
            "missing_data_rejection_log",
            "transient_structure_snapshot",
        ],
        "policy": {
            "standard_definitions_certified": True,
            "calendar_certified": True,
            "financial_execution_enabled": False,
            "training_enabled": False,
            "commercialization_enabled": False,
            **(policy or {}),
        },
        "strategies": [{
            "id": "chanlun",
            "name": "缠论",
            "minimums": {"sample_count": 2, "trading_days": 2},
            "required_evidence": REQUIRED,
            "missing_data_policy": "缺行情、缺结构或日期越界即拒绝",
            "transient_structure_evidence": ["fractal", "stroke", "segment", "center"],
        }],
    }


def evidence(**overrides):
    row = {
        "sample_count": 2,
        "trading_days": 2,
        "source_hashes": ["a" * 64, "c" * 64],
        "offline_review_receipt": {
            "receipt_id": "r1",
            "subject": "老田",
            "signed_at": "2026-10-12T00:00:00+08:00",
            "public_key_fingerprint": "b" * 64,
        },
        "source_reference": "local:knowledge_docs/chanlun.md",
        "source_date_receipt": {
            "receipt_id": "d1",
            "source_date": "2026-10-11",
            "observed_at": "2026-10-12T00:00:00+08:00",
            "source_sha256": "d" * 64,
        },
        "time_split": {
            "train": [{"sample_id": "a", "split_group": "g1", "date": "2026-01-01"}],
            "validation": [{"sample_id": "b", "split_group": "g2", "date": "2026-06-01"}],
            "test": [{"sample_id": "c", "split_group": "g3", "date": "2026-10-01"}],
        },
        "benchmark": {"id": "CSI300", "window": "same_window"},
        "cost_model": {"commission_bps": 3, "slippage_bps": 2},
        "missing_data_rejections": [{"case": "missing_quote", "status": "rejected"}],
        "transient_structure_snapshots": [{"sample_id": "a", "fields": ["fractal", "stroke", "segment", "center"]}],
    }
    row.update(overrides)
    return {"schema": "strategy_acceptance_evidence/v1", "strategies": {"chanlun": row}}


def p1_ok():
    return {
        "schema": "p1_observability_audit/v0.1",
        "read_only": True,
        "alert_fail": {"coverage_gap": False},
        "scheduler": {"actual_execution_evidence": True},
        "employment": {"three_source_skip_verified": True},
    }


def test_complete_evidence_is_ready_for_shadow_review_but_never_production():
    report = audit_matrix(matrix(), evidence(), p1_ok())
    item = report["strategies"][0]
    assert item["verdict"] == "ready_for_shadow_review"
    assert item["production_eligible"] is False
    assert report["training_eligible"] is False
    assert report["financial_execution_enabled"] is False
    assert report["commercialization_eligible"] is False
    assert report["all_strategies_ready_for_shadow_review"] is True


def test_missing_evidence_does_not_add_spurious_invalid_reason():
    report = audit_matrix(matrix(), {"strategies": {"chanlun": {}}}, p1_ok())
    reasons = report["strategies"][0]["reasons"]
    assert "missing:sample_count" in reasons
    assert "invalid:sample_count" not in reasons


def test_missing_evidence_is_blocked_with_named_reason():
    report = audit_matrix(matrix(), evidence(cost_model=None), p1_ok())
    item = report["strategies"][0]
    assert item["verdict"] == "blocked"
    assert "missing:cost_model" in item["reasons"]


def test_invalid_source_hashes_are_rejected():
    report = audit_matrix(matrix(), evidence(source_hashes=["bad"]), p1_ok())
    assert "invalid:source_hashes" in report["strategies"][0]["reasons"]


def test_malformed_offline_review_receipt_is_rejected():
    report = audit_matrix(
        matrix(),
        evidence(offline_review_receipt={"receipt_id": "r1"}),
        p1_ok(),
    )
    assert "invalid:offline_review_receipt" in report["strategies"][0]["reasons"]


def test_reduced_matrix_is_rejected_fail_closed():
    reduced = matrix()
    reduced["strategies"][0]["required_evidence"] = ["sample_count"]
    with pytest.raises(ValueError, match="mandatory"):
        audit_matrix(reduced, evidence(), p1_ok())


def test_invalid_source_date_receipt_is_rejected():
    report = audit_matrix(matrix(), evidence(source_date_receipt={"receipt_id": "d1"}), p1_ok())
    assert "invalid:source_date_receipt" in report["strategies"][0]["reasons"]


def test_time_split_requires_ordered_nonempty_dates_and_unique_samples():
    bad_split = {
        "train": [{"sample_id": "same", "split_group": "g1", "date": "2026-06-01"}],
        "validation": [{"sample_id": "same", "split_group": "g2", "date": "2026-05-01"}],
        "test": [],
    }
    report = audit_matrix(matrix(), evidence(time_split=bad_split), p1_ok())
    reasons = report["strategies"][0]["reasons"]
    assert "invalid:time_split" in reasons
    assert "sample_id_cross_split" in reasons
    assert "time_order_invalid" in reasons


def test_nan_and_infinite_values_are_rejected():
    report = audit_matrix(
        matrix(),
        evidence(sample_count="nan", cost_model={"commission_bps": "inf", "slippage_bps": 2}),
        p1_ok(),
    )
    reasons = report["strategies"][0]["reasons"]
    assert "invalid:sample_count" in reasons
    assert "invalid:cost_model" in reasons


def test_p1_schema_is_required():
    p1 = p1_ok()
    del p1["schema"]
    report = audit_matrix(matrix(), evidence(), p1)
    assert "p1:invalid_report_schema" in report["strategies"][0]["reasons"]


def test_evidence_schema_is_required():
    bad = evidence()
    bad.pop("schema", None)
    report = audit_matrix(matrix(), bad, p1_ok())
    assert "invalid:evidence_schema" in report["strategies"][0]["reasons"]


def test_invalid_source_reference_is_rejected():
    report = audit_matrix(matrix(), evidence(source_reference="knowledge_docs/chanlun.md"), p1_ok())
    assert "invalid:source_reference" in report["strategies"][0]["reasons"]


def test_source_hashes_must_cover_sample_count():
    report = audit_matrix(matrix(), evidence(source_hashes=["a" * 64]), p1_ok())
    assert "source_hashes_count_mismatch" in report["strategies"][0]["reasons"]


def test_global_requirements_are_mandatory():
    reduced = matrix()
    reduced["global_requirements"] = ["source_reference"]
    with pytest.raises(ValueError, match="mandatory"):
        audit_matrix(reduced, evidence(), p1_ok())


def test_p1_report_must_be_read_only():
    p1 = p1_ok()
    p1["read_only"] = False
    report = audit_matrix(matrix(), evidence(), p1)
    assert "p1:report_not_read_only" in report["strategies"][0]["reasons"]


def test_split_group_cross_split_is_rejected():
    leaked = {
        "train": [{"sample_id": "a", "split_group": "same"}],
        "validation": [{"sample_id": "b", "split_group": "same"}],
        "test": [],
    }
    report = audit_matrix(matrix(), evidence(time_split=leaked), p1_ok())
    assert "split_group_cross_split" in report["strategies"][0]["reasons"]


def test_configured_scheduler_without_actual_receipt_is_blocked():
    p1 = copy.deepcopy(p1_ok())
    p1["scheduler"]["actual_execution_evidence"] = False
    report = audit_matrix(matrix(), evidence(), p1)
    assert report["strategies"][0]["verdict"] == "blocked"
    assert "p1:scheduler_execution_evidence_missing" in report["strategies"][0]["reasons"]


def test_uncertified_calendar_blocks_even_complete_evidence():
    report = audit_matrix(matrix({"calendar_certified": False}), evidence(), p1_ok())
    assert report["strategies"][0]["verdict"] == "blocked"
    assert "global:calendar_uncertified" in report["strategies"][0]["reasons"]


def test_invalid_matrix_schema_fails_closed():
    with pytest.raises(ValueError, match="schema"):
        audit_matrix({"schema": "wrong", "policy": {}}, {}, None)


def test_cli_invalid_input_writes_fail_closed_report(tmp_path):
    matrix_path = tmp_path / "matrix.json"
    output_path = tmp_path / "report.json"
    matrix_path.write_text("{\"schema\": \"wrong\"}", encoding="utf-8")
    code = main([
        "--matrix", str(matrix_path),
        "--output", str(output_path),
    ])
    assert code != 0
    report = __import__("json").loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert report["read_only"] is True
    assert report["training_eligible"] is False
    assert report["financial_execution_enabled"] is False


def test_cli_stdout_contains_all_gate_fields(tmp_path, capsys):
    matrix_path = tmp_path / "matrix.json"
    evidence_path = tmp_path / "evidence.json"
    p1_path = tmp_path / "p1.json"
    output_path = tmp_path / "report.json"
    matrix_path.write_text(__import__("json").dumps(matrix(), ensure_ascii=False), encoding="utf-8")
    evidence_path.write_text(__import__("json").dumps(evidence(), ensure_ascii=False), encoding="utf-8")
    p1_path.write_text(__import__("json").dumps(p1_ok(), ensure_ascii=False), encoding="utf-8")
    assert main([
        "--matrix", str(matrix_path),
        "--evidence", str(evidence_path),
        "--p1-report", str(p1_path),
        "--output", str(output_path),
    ]) == 0
    summary = __import__("json").loads(capsys.readouterr().out)
    for key in (
        "financial_execution_enabled",
        "commercialization_eligible",
        "all_strategies_ready_for_shadow_review",
    ):
        assert key in summary





