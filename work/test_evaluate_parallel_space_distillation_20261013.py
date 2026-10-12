"""K-STOCK-223 蒸馏评测器离线测试。"""

import json
import runpy
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("evaluate_parallel_space_distillation_20261013.py")
MOD = runpy.run_path(str(MODULE_PATH))


def test_student_abstains_when_key_fields_are_missing():
    row = {
        "scenario_packet": {
            "missing_fields": ["events"],
            "price_features": {
                "trend_strength": 0.6,
                "realized_volatility": 0.4,
                "volume_ratio": 1.1,
            },
        },
        "candidate_strategies": ["trend_follow"],
    }
    decision = MOD["_student_decision"](row)
    assert decision["abstain"] is True
    assert decision["selected_strategy"] == "abstain"
    assert "events" in decision["invalidating_conditions"][0]


def test_report_covers_selected_scenarios_and_keeps_execution_disabled():
    data = MOD["DATA"]
    load = MOD["_load_jsonl"]
    scenarios = load(data / "scenario-packets-v0.1.jsonl")
    labels = load(data / "teacher-labels-v0.1.jsonl")
    ids = ["PS-001", "PS-003", "PS-005", "PS-008", "PS-012"]
    selected = [{"sample_id": sid, **next(x for x in scenarios if x["sample_id"] == sid)} for sid in ids]
    report = MOD["build_report"](selected, labels, sample_ids=ids)
    assert report["sample_count"] == 5
    assert report["distillation_baseline_pass"] is True
    assert report["metrics"]["abstention_recall"] == 1.0
    assert report["weight_training_started"] is False
    assert report["financial_execution_enabled"] is False
    assert report["simulated_execution_enabled"] is False
    assert report["commercialization_enabled"] is False


def test_student_protocol_rejects_forbidden_action_text():
    row = {
        "sample_id": "PS-X",
        "scenario_packet": {
            "missing_fields": [],
            "price_features": {
                "trend_strength": 0.8,
                "realized_volatility": 0.2,
                "volume_ratio": 1.5,
            },
        },
        "candidate_strategies": ["trend_follow"],
    }
    decision = MOD["_student_decision"](row)
    decision["student_reasoning"] += "；买入动作禁止出现在研究输出中"
    errors = MOD["_validate_student"](row, decision)
    assert any(error.startswith("forbidden_action:") for error in errors)
