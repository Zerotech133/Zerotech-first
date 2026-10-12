"""生成可审计的教师标签校准层（保留原始教师标签不变）。"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "parallel-space-strategy-v0.1" / "dataset"
RAW = DATA / "teacher-labels-v0.1.jsonl"
CALIBRATED = DATA / "teacher-labels-calibrated-v0.1.jsonl"
MANIFEST = DATA / "risk-label-calibration-v0.1.json"
RISK_MAP = {"moderate": "medium"}


def build_calibrated_labels() -> tuple[list[dict], dict]:
    labels = [
        json.loads(line)
        for line in RAW.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    overrides = []
    calibrated = []
    for label in labels:
        sample_id = label["sample_id"]
        original_risk = label.get("risk_level")
        risk = RISK_MAP.get(original_risk, original_risk)
        reason = "统一 moderate/medium 为 canonical medium。" if original_risk in RISK_MAP else "保留原风险标签。"
        if sample_id == "PS-002":
            # 教师已经选择保守拒答；只记录边界解释，不改变其策略结论。
            reason = "低趋势、低波动且未确认突破，保留保守拒答并使用 medium 风险。"
            risk = "medium"
        elif sample_id == "PS-003":
            # 缺 events 时无法量化事件风险，按数据协议使用 unknown。
            reason = "events 缺失，风险不可量化；按缺数据协议改为 unknown，保留 abstain。"
            risk = "unknown"
        item = dict(label)
        item["risk_level"] = risk
        item["calibration"] = {
            "original_risk_level": original_risk,
            "calibrated_risk_level": risk,
            "reason": reason,
        }
        calibrated.append(item)
        if original_risk != risk or sample_id in {"PS-002", "PS-003"}:
            overrides.append(
                {
                    "sample_id": sample_id,
                    "original_risk_level": original_risk,
                    "calibrated_risk_level": risk,
                    "selected_strategy": item.get("selected_strategy"),
                    "abstain": item.get("abstain"),
                    "reason": reason,
                }
            )
    manifest = {
        "schema": "parallel_space_strategy_risk_label_calibration/v1",
        "raw_teacher_labels": "teacher-labels-v0.1.jsonl",
        "calibrated_teacher_labels": "teacher-labels-calibrated-v0.1.jsonl",
        "raw_labels_unchanged": True,
        "raw_teacher_labels_sha256": hashlib.sha256(RAW.read_bytes()).hexdigest(),
        "canonical_risk_levels": ["low", "medium", "high", "unknown"],
        "mapping": RISK_MAP,
        "overrides": overrides,
        "training_started": False,
        "financial_execution_enabled": False,
        "simulated_execution_enabled": False,
        "commercialization_enabled": False,
    }
    return calibrated, manifest


def main() -> int:
    calibrated, manifest = build_calibrated_labels()
    CALIBRATED.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in calibrated),
        encoding="utf-8",
    )
    manifest["calibrated_teacher_labels_sha256"] = hashlib.sha256(
        CALIBRATED.read_bytes()
    ).hexdigest()
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
