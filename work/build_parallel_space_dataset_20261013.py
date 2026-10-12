"""生成平行空间策略场景和本地教师标签（K-STOCK-221）。"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work" / "parallel-space-strategy-v0.1" / "dataset"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:4b"
ALLOWED_STRATEGIES = {
    "trend_follow",
    "mean_reversion",
    "breakout",
    "defensive_cash",
    "abstain",
}


def _scenario(
    sample_id: str,
    domain: str,
    regime: str,
    trend: float,
    volatility: float,
    volume: float,
    missing: list[str] | None = None,
) -> dict:
    missing = missing or []
    return {
        "sample_id": sample_id,
        "task_type": "missing_data_abstention" if missing else "strategy_selection",
        "scenario_packet": {
            "market_domain": domain,
            "as_of": "2026-10-12",
            "timeframe": "1d",
            "price_features": {
                "trend_strength": trend,
                "realized_volatility": volatility,
                "volume_ratio": volume,
            },
            "technical_features": {
                "breakout_confirmed": trend >= 0.75 and volume >= 1.4,
                "mean_reversion_zscore": round((0.5 - trend) * 2, 2),
                "drawdown_20d": round(-max(0.0, volatility - 0.45), 2),
            },
            "fundamental_features": {
                "earnings_visibility": "unknown" if "fundamentals" in missing else "medium",
                "valuation": "unknown" if "valuation" in missing else "neutral",
            },
            "event_features": {
                "event_bias": "unknown" if "events" in missing else "neutral",
                "event_count_30d": None if "events" in missing else 2,
            },
            "chart_description": (
                f"{domain} {regime}，趋势强度{trend}，波动率{volatility}，"
                f"成交量比{volume}。"
            ),
            "missing_fields": missing,
        },
        "candidate_strategies": [
            "trend_follow",
            "mean_reversion",
            "breakout",
            "defensive_cash",
        ],
        "synthetic": True,
        "provenance_note": "synthetic_fixture_for_parallel_space_only",
    }


SCENARIOS = [
    _scenario("PS-001", "A股", "bull_trend", 0.86, 0.28, 1.65),
    _scenario("PS-002", "A股", "range", 0.18, 0.22, 0.92),
    _scenario("PS-003", "A股", "event_uncertain", 0.55, 0.48, 1.10, ["events"]),
    _scenario("PS-004", "指数", "breakout", 0.79, 0.36, 1.58),
    _scenario("PS-005", "指数", "bear_trend", -0.74, 0.61, 1.34),
    _scenario("PS-006", "指数", "data_gap", 0.62, 0.40, 1.20, ["valuation", "fundamentals"]),
    _scenario("PS-007", "期货", "trend_continuation", 0.77, 0.52, 1.48),
    _scenario("PS-008", "期货", "high_volatility", 0.42, 0.88, 1.75),
    _scenario("PS-009", "期货", "missing_events", 0.48, 0.47, 1.05, ["events"]),
    _scenario("PS-010", "虚拟币", "high_volatility_breakout", 0.81, 0.96, 2.10),
    _scenario("PS-011", "虚拟币", "range", 0.12, 0.72, 0.84),
    _scenario("PS-012", "虚拟币", "missing_risk", 0.68, 0.91, 1.25, ["valuation", "events"]),
]


def _rule_label(row: dict) -> dict:
    packet = row["scenario_packet"]
    missing = packet["missing_fields"]
    if missing:
        return {
            "regime": "unknown_due_to_missing_data",
            "selected_strategy": "abstain",
            "risk_level": "unknown",
            "abstain": True,
            "teacher_reasoning": "关键字段缺失，先补齐数据再比较策略。",
            "invalidating_conditions": ["缺失字段未补齐"],
        }
    trend = packet["price_features"]["trend_strength"]
    vol = packet["price_features"]["realized_volatility"]
    volume = packet["price_features"]["volume_ratio"]
    if vol >= 0.8:
        strategy, risk = "defensive_cash", "high"
    elif trend >= 0.75 and volume >= 1.4:
        strategy, risk = "trend_follow", "moderate"
    elif trend <= 0.3:
        strategy, risk = "mean_reversion", "moderate"
    else:
        strategy, risk = "breakout", "medium"
    return {
        "regime": packet["chart_description"].split()[1],
        "selected_strategy": strategy,
        "risk_level": risk,
        "abstain": False,
        "teacher_reasoning": "基于趋势、波动率和成交量特征进行策略比较。",
        "invalidating_conditions": ["趋势或波动率特征发生结构性变化"],
    }


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    required = {"regime", "selected_strategy", "risk_level", "abstain"}
    if not required <= set(value):
        return None
    if value.get("selected_strategy") not in ALLOWED_STRATEGIES:
        return None
    if not isinstance(value.get("abstain"), bool):
        return None
    if not isinstance(value.get("invalidating_conditions", []), list):
        value["invalidating_conditions"] = [
            str(value["invalidating_conditions"])
        ]
    return value


def _teacher(row: dict, timeout: int = 90) -> tuple[dict, str]:
    packet = row["scenario_packet"]
    prompt = (
        "你是平行空间策略教师，只做研究分析，不输出买入、卖出、下单或持仓指令。"
        "只根据给定JSON回答，返回严格JSON，字段必须为 regime、selected_strategy、"
        "risk_level、abstain、teacher_reasoning、invalidating_conditions。"
        "如果missing_fields非空，selected_strategy必须为abstain且abstain=true。\n"
        + json.dumps(packet, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": MODEL,
            "stream": False,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": 220},
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        label = _parse_json(((payload.get("message") or {}).get("content")))
        if label:
            if packet["missing_fields"] and not label.get("abstain"):
                return _rule_label(row), "rule_guard_fallback"
            return label, "ollama"
    except Exception:
        pass
    return _rule_label(row), "rule_fixture"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", action="store_true", help="调用本地Ollama教师")
    ap.add_argument(
        "--teacher-limit",
        type=int,
        default=3,
        help="最多调用多少条本地教师，剩余样本使用规则教师回退",
    )
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    scenario_path = OUT / "scenario-packets-v0.1.jsonl"
    scenario_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in SCENARIOS),
        encoding="utf-8",
    )
    labels = []
    for index, row in enumerate(SCENARIOS):
        use_teacher = a.teacher and index < max(0, a.teacher_limit)
        label, source = _teacher(row) if use_teacher else (_rule_label(row), "rule_fixture")
        labels.append({
            "sample_id": row["sample_id"],
            "teacher_agent_id": "qwen3.5:4b" if source == "ollama" else "rule_fixture",
            "teacher_source": source,
            **label,
        })
    (OUT / "teacher-labels-v0.1.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in labels),
        encoding="utf-8",
    )
    missing_count = sum(bool(r["scenario_packet"]["missing_fields"]) for r in SCENARIOS)
    ollama_count = sum(r["teacher_source"] == "ollama" for r in labels)
    manifest = {
        "schema": "parallel_space_strategy_dataset_manifest/v1",
        "mode": "sandbox_only",
        "synthetic_fixture": True,
        "scenario_count": len(SCENARIOS),
        "missing_data_cases": missing_count,
        "teacher_labels": len(labels),
        "ollama_teacher_labels": ollama_count,
        "weight_training_started": False,
        "financial_execution_enabled": False,
        "simulated_execution_enabled": False,
        "commercialization_enabled": False,
        "source": "generated_synthetic_scenarios",
    }
    (OUT / "dataset-manifest-v0.1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
