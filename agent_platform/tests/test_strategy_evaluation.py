# -*- coding: utf-8 -*-
"""独立策略结果仲裁与增量评价测试。"""

import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_evaluation import (  # noqa: E402
    StrategyEvaluationError,
    arbitrate_strategy_results,
    evaluate_incremental,
)
from strategy_registry import Verdict  # noqa: E402


def _verdict(
    strategy_id, recommendation, confidence=0.8, trace_id="trace-1", evidence_refs=None
):
    extras = {}
    if evidence_refs is not None:
        extras["evidence_refs"] = evidence_refs
    return Verdict(
        strategy_id=strategy_id,
        symbol="600519.SH",
        recommendation=recommendation,
        confidence=confidence,
        trace_id=trace_id,
        extras=extras,
    )


def test_no_conflict_consensus_and_contribution_from_verdicts():
    result = arbitrate_strategy_results(
        [
            _verdict("trend", "buy", 0.8, "trace-a", ["evidence:trend"]),
            _verdict("momentum", "strong_buy", 0.6, "trace-b", ["evidence:momentum"]),
        ]
    )

    assert result["consensus"] == "buy"
    assert result["consensus_recommendation"] == "strong_buy"
    assert result["conflict"] is False
    assert result["blocked"] is False
    assert result["participating_strategies"] == ["trend", "momentum"]
    assert result["strategy_contributions"]["trend"]["signed_contribution"] == 0.8
    assert (
        result["strategy_contributions"]["momentum"]["recommendation"] == "strong_buy"
    )
    assert result["evidence_refs"] == ["evidence:trend", "evidence:momentum"]
    assert result["trace_ids"] == ["trace-a", "trace-b"]


def test_scheduler_results_are_supported_and_conflict_is_blocked_without_majority_vote():
    scheduler_results = [
        {
            "strategy_id": "s1",
            "status": "success",
            "verdict": _verdict("s1", "buy", 0.9, "run-1"),
            "evidence_refs": ["scheduler:s1"],
        },
        {
            "strategy_id": "s2",
            "status": "success",
            "verdict": _verdict("s2", "buy", 0.9, "run-2"),
        },
        {
            "strategy_id": "s3",
            "status": "success",
            "verdict": _verdict("s3", "sell", 0.1, "run-3"),
        },
    ]

    result = arbitrate_strategy_results(scheduler_results)

    assert result["consensus"] is None
    assert result["conflict"] is True
    assert result["blocked"] is True
    assert "冲突" in result["blocked_reason"]
    assert result["participating_strategy_ids"] == ["s1", "s2", "s3"]
    assert result["strategy_contributions"]["s3"]["signed_contribution"] == -0.1
    assert result["evidence_refs"] == ["scheduler:s1"]


def test_failed_scheduler_result_does_not_participate():
    result = arbitrate_strategy_results(
        [
            {
                "strategy_id": "failed",
                "status": "error",
                "error": "插件故障",
                "trace_id": "failed-trace",
                "evidence_refs": ["error:e1"],
            },
            {
                "strategy_id": "healthy",
                "status": "success",
                "verdict": _verdict("healthy", "sell", trace_id="healthy-trace"),
            },
        ]
    )

    assert result["consensus"] == "sell"
    assert result["conflict"] is False
    assert result["blocked"] is False
    assert result["participating_strategies"] == ["healthy"]
    assert result["excluded_results"] == [
        {
            "strategy_id": "failed",
            "reason": "插件故障",
            "evidence_refs": ["error:e1"],
            "trace_id": "failed-trace",
        }
    ]


def test_evidence_and_trace_are_preserved_for_verdict_and_incremental_result():
    consensus = arbitrate_strategy_results(
        [_verdict("s1", "hold", trace_id="trace-consensus", evidence_refs=["ev-1"])],
        trace_id="batch-trace",
    )
    assert consensus["evidence_refs"] == ["ev-1"]
    assert consensus["trace_id"] == "batch-trace"

    incremental = evaluate_incremental(
        {
            "sample_size": 120,
            "wins": 72,
            "win_rate": 0.60,
            "return": 0.18,
            "max_drawdown": 0.12,
            "evidence_refs": ["candidate:ev"],
            "trace_id": "candidate-trace",
        },
        {
            "sample_size": 100,
            "wins": 55,
            "win_rate": 0.55,
            "return": 0.10,
            "max_drawdown": 0.15,
            "evidence_refs": ["baseline:ev"],
            "trace_id": "baseline-trace",
        },
    )
    assert incremental["evidence_refs"] == ["candidate:ev", "baseline:ev"]
    assert incremental["trace_ids"] == ["candidate-trace", "baseline-trace"]
    assert incremental["trace_id"] == ["candidate-trace", "baseline-trace"]


def test_incremental_metrics_calculation_and_zero_sample_boundary():
    result = evaluate_incremental(
        {
            "sample_count": 120,
            "win_count": 72,
            "winrate": 0.60,
            "pnl": 0.18,
            "drawdown": 0.12,
        },
        {
            "samples": 100,
            "wins": 55,
            "win_rate": 0.55,
            "return": 0.10,
            "max_drawdown": 0.15,
        },
    )
    assert result["incremental_sample_size"] == 20
    assert result["incremental_wins"] == 17
    assert result["incremental_win_rate"] == pytest.approx(0.85)
    assert result["win_rate_delta"] == pytest.approx(0.05)
    assert result["return_delta"] == pytest.approx(0.08)
    assert result["max_drawdown_delta"] == pytest.approx(-0.03)

    empty_delta = evaluate_incremental(
        {"sample_size": 10, "wins": 2}, {"sample_size": 10, "wins": 2}
    )
    assert empty_delta["incremental_sample_size"] == 0
    assert empty_delta["incremental_wins"] == 0
    assert empty_delta["incremental_win_rate"] is None


def test_incremental_rejects_invalid_boundaries():
    with pytest.raises(StrategyEvaluationError, match="不能小于"):
        evaluate_incremental({"sample_size": 9}, {"sample_size": 10})
    with pytest.raises(StrategyEvaluationError, match=r"\[0, 1\]"):
        evaluate_incremental({"win_rate": 1.1}, {})
    with pytest.raises(StrategyEvaluationError, match="不能为负数"):
        evaluate_incremental({"max_drawdown": -0.1}, {})
