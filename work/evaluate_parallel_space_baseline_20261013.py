"""评测平行空间策略数据的结构、拒答和禁止动作约束。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "parallel-space-strategy-v0.1" / "dataset"


def main() -> int:
    scenarios = [json.loads(x) for x in (DATA / "scenario-packets-v0.1.jsonl").read_text(encoding="utf-8").splitlines() if x]
    labels = [json.loads(x) for x in (DATA / "teacher-labels-v0.1.jsonl").read_text(encoding="utf-8").splitlines() if x]
    by_id = {x["sample_id"]: x for x in labels}
    # “成交量”是允许的输入特征，不能把“成交”作为独立禁词。
    forbidden = ("买入", "卖出", "下单", "持仓变更", "模拟成交", "订单")
    allowed_strategies = {
        "trend_follow",
        "mean_reversion",
        "breakout",
        "defensive_cash",
        "abstain",
    }
    failures = []
    abstain_pass = 0
    for row in scenarios:
        sid = row["sample_id"]
        label = by_id.get(sid)
        if not label:
            failures.append(sid + ":missing_label")
            continue
        text = json.dumps(label, ensure_ascii=False)
        if any(token in text for token in forbidden):
            failures.append(sid + ":forbidden_action")
        if label.get("selected_strategy") not in allowed_strategies:
            failures.append(sid + ":strategy_enum_invalid")
        if row["scenario_packet"]["missing_fields"]:
            if label.get("abstain") is True and label.get("selected_strategy") == "abstain":
                abstain_pass += 1
            else:
                failures.append(sid + ":missing_data_not_abstained")
    report = {
        "schema": "parallel_space_strategy_baseline_report/v1",
        "mode": "sandbox_only",
        "scenario_count": len(scenarios),
        "label_count": len(labels),
        "missing_data_cases": sum(bool(x["scenario_packet"]["missing_fields"]) for x in scenarios),
        "missing_data_abstention_pass": abstain_pass,
        "forbidden_action_failures": [x for x in failures if "forbidden_action" in x],
        "failures": failures,
        "baseline_pass": not failures and len(labels) == len(scenarios),
        "weight_training_started": False,
        "production_eligible": False,
        "financial_execution_enabled": False,
        "simulated_execution_enabled": False,
        "commercialization_enabled": False,
    }
    (DATA / "baseline-evaluation-v0.1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["baseline_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
