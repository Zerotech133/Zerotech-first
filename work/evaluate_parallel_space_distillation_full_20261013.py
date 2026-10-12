"""全量 12 场景的平行空间策略蒸馏评测和风险校准（K-STOCK-225）。"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "parallel-space-strategy-v0.1" / "dataset"
BASELINE_SCRIPT = ROOT / "work" / "evaluate_parallel_space_distillation_20261013.py"
OUTPUT = DATA / "distillation-evaluation-full-v0.1.json"
CALIBRATED_LABELS = DATA / "teacher-labels-calibrated-v0.1.jsonl"


def _load_baseline_module():
    spec = importlib.util.spec_from_file_location("parallel_distillation_baseline", BASELINE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载基础蒸馏评测器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_risk_calibration(
    comparisons: list[dict[str, Any]],
) -> dict[str, Any]:
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    mismatches: list[dict[str, Any]] = []
    for row in comparisons:
        teacher_risk = row["teacher"].get("risk_level", "unknown")
        student_risk = row["student"].get("risk_level", "unknown")
        confusion[teacher_risk][student_risk] += 1
        if teacher_risk != student_risk:
            mismatches.append(
                {
                    "sample_id": row["sample_id"],
                    "market_domain": row["market_domain"],
                    "teacher_risk": teacher_risk,
                    "student_risk": student_risk,
                    "teacher_abstain": row["teacher"].get("abstain"),
                    "student_abstain": row["student"].get("abstain"),
                }
            )
    total = len(comparisons)
    matched = total - len(mismatches)
    return {
        "teacher_to_student_confusion": {
            teacher: dict(sorted(student_counts.items()))
            for teacher, student_counts in sorted(confusion.items())
        },
        "teacher_risk_distribution": dict(
            sorted(Counter(row["teacher"].get("risk_level", "unknown") for row in comparisons).items())
        ),
        "student_risk_distribution": dict(
            sorted(Counter(row["student"].get("risk_level", "unknown") for row in comparisons).items())
        ),
        "risk_agreement": round(matched / total, 4) if total else 0.0,
        "risk_mismatch_count": len(mismatches),
        "risk_mismatches": mismatches,
        "calibration_status": (
            "aligned" if not mismatches else "needs_teacher_label_review"
        ),
    }


def build_full_report() -> dict[str, Any]:
    baseline = _load_baseline_module()
    scenarios = _load_jsonl(DATA / "scenario-packets-v0.1.jsonl")
    if not CALIBRATED_LABELS.exists():
        raise RuntimeError("缺少教师标签校准文件，请先运行 build_distillation_calibration_20261013.py")
    labels = _load_jsonl(CALIBRATED_LABELS)
    sample_ids = [row["sample_id"] for row in scenarios]
    report = baseline.build_report(scenarios, labels, sample_ids=sample_ids)
    report["schema"] = "parallel_space_strategy_distillation_evaluation/v2"
    report["evaluation_scope"] = "all_synthetic_scenarios"
    report["teacher_label_source"] = CALIBRATED_LABELS.name
    report["risk_calibration"] = build_risk_calibration(report["comparisons"])
    report["calibration_gate"] = {
        "required_before_lora_review": True,
        "status": report["risk_calibration"]["calibration_status"],
        "blocking_samples": [
            item["sample_id"] for item in report["risk_calibration"]["risk_mismatches"]
        ],
    }
    if report["distillation_baseline_pass"] and not report["calibration_gate"]["blocking_samples"]:
        report["lora_review_status"] = "ready_for_isolated_lora_review"
        report["recommendation"] = (
            "满足隔离 LoRA 评审前置条件；只进入评审，不安装依赖、不启动权重训练。"
        )
    if report["risk_calibration"]["risk_mismatches"]:
        report["recommendation"] = (
            "先复核教师风险标签和震荡/缺数据边界样本，再评审隔离 LoRA；"
            "本报告不启动权重训练。"
        )
    return report


def main() -> int:
    report = build_full_report()
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["distillation_baseline_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
