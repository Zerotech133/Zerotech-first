"""K-STOCK-225 全量蒸馏和风险校准测试。"""

import runpy
from pathlib import Path


MODULE_PATH = Path(__file__).with_name(
    "evaluate_parallel_space_distillation_full_20261013.py"
)
MOD = runpy.run_path(str(MODULE_PATH))


def test_full_report_contains_all_scenarios_and_calibration_mismatches():
    report = MOD["build_full_report"]()
    assert report["evaluation_scope"] == "all_synthetic_scenarios"
    assert report["sample_count"] == 12
    assert report["risk_calibration"]["risk_agreement"] == 1.0
    assert report["risk_calibration"]["calibration_status"] == "aligned"
    assert report["calibration_gate"]["blocking_samples"] == []
    assert report["distillation_baseline_pass"] is True
    assert report["lora_review_status"] == "ready_for_isolated_lora_review"
    assert report["weight_training_started"] is False


def test_full_report_keeps_missing_data_gate_visible():
    report = MOD["build_full_report"]()
    assert report["metrics"]["abstention_recall"] == 1.0
    assert report["failures"] == []
    assert report["financial_execution_enabled"] is False
    assert report["simulated_execution_enabled"] is False
    assert report["commercialization_enabled"] is False
