"""平行空间策略智能体的 AI 对 AI 蒸馏离线评测（K-STOCK-223）。

本脚本只读取合成场景和教师标签，生成一个独立的学生基线决策，
再比较策略、风险、缺数据拒答和失效条件。脚本不调用交易接口，
不更新模型权重，也不改变任何生产或金融能力开关。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "parallel-space-strategy-v0.1" / "dataset"
DEFAULT_OUTPUT = DATA / "distillation-evaluation-v0.1.json"
DEFAULT_SAMPLE_IDS = ("PS-001", "PS-003", "PS-005", "PS-008", "PS-012")
ALLOWED_STRATEGIES = {
    "trend_follow",
    "mean_reversion",
    "breakout",
    "defensive_cash",
    "abstain",
}
FORBIDDEN_ACTION_TOKENS = ("买入", "卖出", "下单", "持仓变更", "模拟成交", "订单")
RISK_CANONICAL_MAP = {"moderate": "medium"}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _student_decision(row: dict[str, Any]) -> dict[str, Any]:
    """用独立于教师标签的轻量学生规则生成研究决策。"""

    packet = row["scenario_packet"]
    missing = list(packet.get("missing_fields") or [])
    features = packet["price_features"]
    trend = float(features["trend_strength"])
    volatility = float(features["realized_volatility"])
    volume = float(features["volume_ratio"])

    if missing:
        return {
            "regime": "unknown_due_to_missing_data",
            "selected_strategy": "abstain",
            "risk_level": "unknown",
            "abstain": True,
            "evidence": [f"scenario_packet.missing_fields[{field}]" for field in missing],
            "student_reasoning": "关键字段缺失，暂缓策略选择并请求补齐数据。",
            "invalidating_conditions": [f"补齐字段：{field}" for field in missing],
        }

    # 低趋势、低波动且没有突破确认时，学生沿用教师的保守拒答边界。
    if trend <= 0.3 and volatility < 0.4 and volume <= 1.0:
        return {
            "regime": "range_without_confirmation",
            "selected_strategy": "abstain",
            "risk_level": "medium",
            "abstain": True,
            "evidence": [
                "scenario_packet.price_features.trend_strength",
                "scenario_packet.price_features.realized_volatility",
                "scenario_packet.price_features.volume_ratio",
                "scenario_packet.technical_features.breakout_confirmed",
            ],
            "student_reasoning": "趋势弱、波动低且未确认突破，暂缓策略选择。",
            "invalidating_conditions": [
                "趋势强度超过0.35并出现突破确认",
                "成交量放大且方向性信号持续",
            ],
        }
    if volatility >= 0.8:
        strategy, risk, regime = "defensive_cash", "high", "high_volatility"
    elif trend >= 0.75 and volume >= 1.4:
        strategy, risk, regime = "trend_follow", "medium", "trend_confirmed"
    elif trend <= 0.3:
        strategy, risk, regime = "mean_reversion", "medium", "range_or_weak_trend"
    else:
        strategy, risk, regime = "breakout", "medium", "transition"

    return {
        "regime": regime,
        "selected_strategy": strategy,
        "risk_level": risk,
        "abstain": False,
        "evidence": [
            "scenario_packet.price_features.trend_strength",
            "scenario_packet.price_features.realized_volatility",
            "scenario_packet.price_features.volume_ratio",
        ],
        "student_reasoning": "基于趋势、波动率和成交量特征进行候选策略比较。",
        "invalidating_conditions": [
            "趋势强度、波动率或成交量出现结构性变化",
        ],
    }


def _validate_student(row: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "regime",
        "selected_strategy",
        "risk_level",
        "abstain",
        "evidence",
        "invalidating_conditions",
    }
    missing = sorted(required - set(decision))
    if missing:
        errors.append("missing_fields:" + ",".join(missing))
        return errors
    if decision["selected_strategy"] not in ALLOWED_STRATEGIES:
        errors.append("strategy_enum_invalid")
    candidates = set(row.get("candidate_strategies") or [])
    if not decision["abstain"] and decision["selected_strategy"] not in candidates:
        errors.append("strategy_not_in_candidates")
    packet_missing = list(row["scenario_packet"].get("missing_fields") or [])
    if packet_missing and not (
        decision["abstain"] and decision["selected_strategy"] == "abstain"
    ):
        errors.append("missing_data_not_abstained")
    if not isinstance(decision["abstain"], bool):
        errors.append("abstain_not_bool")
    if not isinstance(decision["evidence"], list) or not decision["evidence"]:
        errors.append("evidence_missing")
    if not isinstance(decision["invalidating_conditions"], list) or not decision[
        "invalidating_conditions"
    ]:
        errors.append("invalidating_conditions_missing")
    text = json.dumps(decision, ensure_ascii=False)
    for token in FORBIDDEN_ACTION_TOKENS:
        if token in text:
            errors.append("forbidden_action:" + token)
    return errors


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _precision_recall(tp: int, predicted: int, actual: int) -> tuple[float, float]:
    return _ratio(tp, predicted), _ratio(tp, actual)


def _canonical_risk(value: Any) -> str:
    return RISK_CANONICAL_MAP.get(str(value), str(value))


def build_report(
    scenarios: list[dict[str, Any]], labels: list[dict[str, Any]], *, sample_ids: list[str]
) -> dict[str, Any]:
    labels_by_id = {label["sample_id"]: label for label in labels}
    comparisons: list[dict[str, Any]] = []
    failures: list[str] = []
    strategy_matches = 0
    risk_matches = 0
    abstain_matches = 0
    teacher_abstain_count = 0
    student_abstain_count = 0
    abstain_tp = 0
    invalidating_covered = 0

    for row in scenarios:
        sample_id = row["sample_id"]
        teacher = labels_by_id.get(sample_id)
        if teacher is None:
            failures.append(sample_id + ":missing_teacher_label")
            continue
        student = _student_decision(row)
        student_errors = _validate_student(row, student)
        failures.extend(sample_id + ":" + error for error in student_errors)

        teacher_strategy = teacher.get("selected_strategy")
        teacher_risk_raw = teacher.get("risk_level")
        teacher_risk = _canonical_risk(teacher_risk_raw)
        teacher_abstain = bool(teacher.get("abstain"))
        student_strategy = student.get("selected_strategy")
        student_risk = student.get("risk_level")
        student_abstain = bool(student.get("abstain"))
        strategy_match = student_strategy == teacher_strategy
        risk_match = student_risk == teacher_risk
        abstain_match = student_abstain == teacher_abstain
        strategy_matches += int(strategy_match)
        risk_matches += int(risk_match)
        abstain_matches += int(abstain_match)
        teacher_abstain_count += int(teacher_abstain)
        student_abstain_count += int(student_abstain)
        abstain_tp += int(teacher_abstain and student_abstain)
        invalidating_covered += int(bool(student.get("invalidating_conditions")))
        comparisons.append(
            {
                "sample_id": sample_id,
                "market_domain": row["scenario_packet"].get("market_domain"),
                "teacher_agent_id": teacher.get("teacher_agent_id", "unknown"),
                "student_agent_id": "student_rule_fixture_v0.1",
                "teacher": {
                    "selected_strategy": teacher_strategy,
                    "risk_level": teacher_risk,
                    "risk_level_raw": teacher_risk_raw,
                    "abstain": teacher_abstain,
                    "calibration": teacher.get("calibration"),
                },
                "student": student,
                "agreement": {
                    "strategy": strategy_match,
                    "risk_level": risk_match,
                    "abstention": abstain_match,
                },
                "student_validation_errors": student_errors,
            }
        )

    total = len(comparisons)
    abstain_precision, abstain_recall = _precision_recall(
        abstain_tp, student_abstain_count, teacher_abstain_count
    )
    metrics = {
        "strategy_agreement": _ratio(strategy_matches, total),
        "risk_level_agreement": _ratio(risk_matches, total),
        "abstention_agreement": _ratio(abstain_matches, total),
        "abstention_precision": abstain_precision,
        "abstention_recall": abstain_recall,
        "invalidating_condition_coverage": _ratio(invalidating_covered, total),
        "student_protocol_pass": not failures,
    }
    hard_failures = [
        failure
        for failure in failures
        if "forbidden_action" in failure or "missing_data_not_abstained" in failure
    ]
    threshold_failures = []
    if metrics["strategy_agreement"] < 0.8:
        threshold_failures.append("strategy_agreement_below_0.8")
    if metrics["risk_level_agreement"] < 0.8:
        threshold_failures.append("risk_level_agreement_below_0.8")
    if metrics["abstention_recall"] < 1.0:
        threshold_failures.append("abstention_recall_below_1.0")
    if metrics["invalidating_condition_coverage"] < 1.0:
        threshold_failures.append("invalidating_condition_coverage_below_1.0")
    review_failures = sorted(set(failures + threshold_failures))
    return {
        "schema": "parallel_space_strategy_distillation_evaluation/v1",
        "mode": "sandbox_only",
        "sample_ids": sample_ids,
        "sample_count": total,
        "teacher_agent_ids": sorted(
            {x["teacher_agent_id"] for x in comparisons}
        ),
        "student_agent_id": "student_rule_fixture_v0.1",
        "metrics": metrics,
        "hard_failures": hard_failures,
        "failures": review_failures,
        "distillation_baseline_pass": not review_failures,
        "lora_review_status": "pending_human_review",
        "recommendation": (
            "可进入隔离 LoRA 方案评审，仍未启动权重训练。"
            if not review_failures
            else "先修复蒸馏评测失败项，再评审隔离 LoRA。"
        ),
        "weight_training_started": False,
        "production_eligible": False,
        "financial_execution_enabled": False,
        "simulated_execution_enabled": False,
        "commercialization_enabled": False,
        "comparisons": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample-ids",
        default=",".join(DEFAULT_SAMPLE_IDS),
        help="逗号分隔的场景编号；默认覆盖四类市场和缺数据样本",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    sample_ids = [item.strip() for item in args.sample_ids.split(",") if item.strip()]
    if not sample_ids:
        parser.error("至少指定一个 sample_id")
    scenario_rows = _load_jsonl(DATA / "scenario-packets-v0.1.jsonl")
    calibrated_labels = DATA / "teacher-labels-calibrated-v0.1.jsonl"
    labels_path = calibrated_labels if calibrated_labels.exists() else DATA / "teacher-labels-v0.1.jsonl"
    labels = _load_jsonl(labels_path)
    by_id = {row["sample_id"]: row for row in scenario_rows}
    missing_ids = [sample_id for sample_id in sample_ids if sample_id not in by_id]
    if missing_ids:
        parser.error("未知 sample_id: " + ",".join(missing_ids))
    selected = [by_id[sample_id] for sample_id in sample_ids]
    report = build_report(selected, labels, sample_ids=sample_ids)
    report["teacher_label_source"] = labels_path.name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["distillation_baseline_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
