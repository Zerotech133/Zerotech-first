# -*- coding: utf-8 -*-
"""独立多策略结果仲裁与增量评价。

本模块只消费 Verdict 或 scheduler 结果并返回内存中的评价结果；不接交易执行，
也不修改 Aggregator 或任何策略账本。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math


BULLISH_RECOMMENDATIONS = frozenset(("buy", "strong_buy"))
BEARISH_RECOMMENDATIONS = frozenset(("sell", "strong_sell"))
NEUTRAL_RECOMMENDATIONS = frozenset(("hold",))
_SUCCESS_STATUSES = frozenset(("success", "ok", "accepted"))
_FAILURE_STATUSES = frozenset(("error", "failed", "failure", "rejected", "timeout"))


class StrategyEvaluationError(ValueError):
    """策略评价输入不符合边界契约。"""


def _read(value, key, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_refs(value):
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _merge_refs(target, *sources):
    for source in sources:
        for ref in _as_refs(source):
            if ref not in target:
                target.append(ref)
    return target


def _extract_verdict(record):
    if isinstance(record, Mapping) and "verdict" in record:
        return record.get("verdict")
    return record


def _iter_records(results):
    if isinstance(results, (str, bytes)) or results is None:
        raise StrategyEvaluationError("results 必须是 Verdict 或 scheduler 结果序列")
    if isinstance(results, Mapping):
        if "results" in results or "strategy_results" in results:
            nested = results.get("results", results.get("strategy_results"))
            return _iter_records(nested)
        if "verdict" in results or "recommendation" in results or "status" in results:
            return [results]
        return [dict(value, strategy_id=key) if isinstance(value, Mapping)
                and "strategy_id" not in value else value
                for key, value in results.items()]
    if isinstance(results, Iterable):
        return list(results)
    return [results]


def _normalise_record(record, index):
    outer_status = _read(record, "status", "success")
    strategy_id = _read(record, "strategy_id", "strategy-%d" % index)
    outer_refs = _read(record, "evidence_refs")
    outer_trace = _read(record, "trace_id")
    verdict = _extract_verdict(record)
    verdict_refs = _read(verdict, "evidence_refs")
    extras = _read(verdict, "extras", {})
    if isinstance(extras, Mapping):
        verdict_refs = verdict_refs if verdict_refs is not None else extras.get("evidence_refs")
    refs = []
    _merge_refs(refs, outer_refs, verdict_refs)
    trace_id = outer_trace or _read(verdict, "trace_id", "")

    if str(outer_status).lower() not in _SUCCESS_STATUSES:
        return {
            "participates": False,
            "strategy_id": strategy_id,
            "reason": _read(record, "error", "status=%s" % outer_status),
            "evidence_refs": refs,
            "trace_id": trace_id,
        }
    if verdict is None:
        return {
            "participates": False,
            "strategy_id": strategy_id,
            "reason": "success 结果缺少 verdict",
            "evidence_refs": refs,
            "trace_id": trace_id,
        }

    recommendation = _read(verdict, "recommendation")
    if not isinstance(recommendation, str):
        return {
            "participates": False,
            "strategy_id": strategy_id,
            "reason": "verdict 缺少 recommendation",
            "evidence_refs": refs,
            "trace_id": trace_id,
        }
    recommendation = recommendation.strip().lower()
    if recommendation not in (BULLISH_RECOMMENDATIONS | BEARISH_RECOMMENDATIONS |
                               NEUTRAL_RECOMMENDATIONS):
        return {
            "participates": False,
            "strategy_id": strategy_id,
            "reason": "不支持的 recommendation=%s" % recommendation,
            "evidence_refs": refs,
            "trace_id": trace_id,
        }
    strategy_id = _read(verdict, "strategy_id", strategy_id)
    confidence = _read(verdict, "confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = float(confidence)
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    direction = ("buy" if recommendation in BULLISH_RECOMMENDATIONS else
                 "sell" if recommendation in BEARISH_RECOMMENDATIONS else "hold")
    sign = 1 if direction == "buy" else -1 if direction == "sell" else 0
    return {
        "participates": True,
        "strategy_id": strategy_id,
        "recommendation": recommendation,
        "direction": direction,
        "confidence": confidence,
        "score": _read(verdict, "score", 0.0),
        "signed_contribution": sign * confidence,
        "evidence_refs": refs,
        "trace_id": trace_id,
    }


def _trace_value(trace_ids):
    unique = []
    for trace_id in trace_ids:
        if trace_id and trace_id not in unique:
            unique.append(trace_id)
    if not unique:
        return None
    return unique[0] if len(unique) == 1 else unique


def arbitrate_strategy_results(results, trace_id=None):
    """仲裁 Verdict 或统一 scheduler 结果。

    任一有效策略同时出现多空方向即 ``conflict=True`` 且默认 ``blocked=True``；
    不按票数或置信度做多数决。失败、超时、拒绝及非法 Verdict 不参与仲裁。
    """
    records = [_normalise_record(record, index)
               for index, record in enumerate(_iter_records(results))]
    participants = [record for record in records if record["participates"]]
    directions = {record["direction"] for record in participants}
    conflict = "buy" in directions and "sell" in directions
    if conflict or not participants:
        consensus = None
    elif directions == {"buy"}:
        consensus = "buy"
    elif directions == {"sell"}:
        consensus = "sell"
    else:
        consensus = "hold"

    contributions = {}
    contribution_list = []
    evidence_refs = []
    trace_ids = []
    for record in records:
        _merge_refs(evidence_refs, record["evidence_refs"])
        if record["trace_id"]:
            trace_ids.append(record["trace_id"])
    for record in participants:
        contribution = {
            "strategy_id": record["strategy_id"],
            "recommendation": record["recommendation"],
            "direction": record["direction"],
            "confidence": record["confidence"],
            "weight": record["confidence"],
            "score": record["score"],
            "signed_contribution": record["signed_contribution"],
            "evidence_refs": list(record["evidence_refs"]),
            "trace_id": record["trace_id"] or None,
        }
        contributions[record["strategy_id"]] = contribution
        contribution_list.append(contribution)

    if trace_id is not None:
        trace_value = trace_id
    else:
        trace_value = _trace_value(trace_ids)
    excluded = [
        {"strategy_id": record["strategy_id"], "reason": record["reason"],
         "evidence_refs": list(record["evidence_refs"]),
         "trace_id": record["trace_id"] or None}
        for record in records if not record["participates"]
    ]
    consensus_recommendation = None
    if consensus in ("buy", "sell"):
        options = [record["recommendation"] for record in participants
                   if record["direction"] == consensus]
        if ("strong_buy" if consensus == "buy" else "strong_sell") in options:
            consensus_recommendation = "strong_buy" if consensus == "buy" else "strong_sell"
        else:
            consensus_recommendation = consensus
    elif consensus == "hold":
        consensus_recommendation = "hold"

    return {
        "consensus": consensus,
        "consensus_recommendation": consensus_recommendation,
        "conflict": conflict,
        "blocked": bool(conflict or not participants),
        "blocked_reason": ("多空策略结果冲突，禁止自动放行" if conflict else
                           "没有可参与仲裁的成功策略" if not participants else ""),
        "participating_strategies": [record["strategy_id"] for record in participants],
        "participating_strategy_ids": [record["strategy_id"] for record in participants],
        "strategy_contributions": contributions,
        "contributions": contributions,
        "contribution_list": contribution_list,
        "excluded_results": excluded,
        "evidence_refs": evidence_refs,
        "trace_id": trace_value,
        "trace_ids": list(dict.fromkeys(trace_ids)),
    }


def _metric(metrics, names):
    for name in names:
        if name in metrics:
            return metrics[name], name
    return None, None


def _number(value, field, *, non_negative=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyEvaluationError("%s 必须是数字" % field)
    value = float(value)
    if not math.isfinite(value):
        raise StrategyEvaluationError("%s 必须是有限数字" % field)
    if non_negative and value < 0:
        raise StrategyEvaluationError("%s 不能为负数" % field)
    return value


def _metrics_input(value, name):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise StrategyEvaluationError("%s 必须是映射" % name)
    return dict(value)


def evaluate_incremental(candidate_metrics, baseline_metrics):
    """计算 candidate 相对 baseline 的增量评价，不执行交易或写入账本。

    ``win_rate``、``return`` 和 ``max_drawdown`` 是候选与基线的差值；同时提供
    ``incremental_win_rate``（当两侧有 wins 且增量样本大于零时）供增量样本解读。
    """
    candidate = _metrics_input(candidate_metrics, "candidate_metrics")
    baseline = _metrics_input(baseline_metrics, "baseline_metrics")
    candidate_sample, _ = _metric(candidate, ("sample_size", "sample_count", "samples"))
    baseline_sample, _ = _metric(baseline, ("sample_size", "sample_count", "samples"))
    candidate_sample = _number(candidate_sample, "candidate sample_size", non_negative=True)
    baseline_sample = _number(baseline_sample, "baseline sample_size", non_negative=True)
    if candidate_sample is not None and baseline_sample is not None and candidate_sample < baseline_sample:
        raise StrategyEvaluationError("candidate sample_size 不能小于 baseline sample_size")
    incremental_sample = (candidate_sample - baseline_sample
                          if candidate_sample is not None and baseline_sample is not None else None)

    candidate_rate, _ = _metric(candidate, ("win_rate", "winrate"))
    baseline_rate, _ = _metric(baseline, ("win_rate", "winrate"))
    candidate_rate = _number(candidate_rate, "candidate win_rate")
    baseline_rate = _number(baseline_rate, "baseline win_rate")
    for rate, field in ((candidate_rate, "candidate win_rate"), (baseline_rate, "baseline win_rate")):
        if rate is not None and not 0 <= rate <= 1:
            raise StrategyEvaluationError("%s 必须在 [0, 1] 内" % field)
    win_rate_delta = (candidate_rate - baseline_rate
                      if candidate_rate is not None and baseline_rate is not None else None)

    candidate_wins, _ = _metric(candidate, ("wins", "win_count"))
    baseline_wins, _ = _metric(baseline, ("wins", "win_count"))
    candidate_wins = _number(candidate_wins, "candidate wins", non_negative=True)
    baseline_wins = _number(baseline_wins, "baseline wins", non_negative=True)
    incremental_wins = (candidate_wins - baseline_wins
                        if candidate_wins is not None and baseline_wins is not None else None)
    if incremental_wins is not None and incremental_wins < 0:
        raise StrategyEvaluationError("candidate wins 不能小于 baseline wins")
    incremental_win_rate = None
    if incremental_wins is not None and incremental_sample:
        incremental_win_rate = incremental_wins / incremental_sample
    elif incremental_sample == 0 and incremental_wins == 0:
        incremental_win_rate = None

    candidate_return, _ = _metric(candidate, ("return", "returns", "total_return", "pnl", "profit"))
    baseline_return, _ = _metric(baseline, ("return", "returns", "total_return", "pnl", "profit"))
    candidate_return = _number(candidate_return, "candidate return")
    baseline_return = _number(baseline_return, "baseline return")
    return_delta = (candidate_return - baseline_return
                    if candidate_return is not None and baseline_return is not None else None)

    candidate_drawdown, _ = _metric(candidate, ("max_drawdown", "drawdown"))
    baseline_drawdown, _ = _metric(baseline, ("max_drawdown", "drawdown"))
    candidate_drawdown = _number(candidate_drawdown, "candidate max_drawdown", non_negative=True)
    baseline_drawdown = _number(baseline_drawdown, "baseline max_drawdown", non_negative=True)
    drawdown_delta = (candidate_drawdown - baseline_drawdown
                      if candidate_drawdown is not None and baseline_drawdown is not None else None)

    evidence_refs = []
    _merge_refs(evidence_refs, candidate.get("evidence_refs"), baseline.get("evidence_refs"))
    candidate_trace = candidate.get("trace_id")
    baseline_trace = baseline.get("trace_id")
    trace_ids = [trace_id for trace_id in (candidate_trace, baseline_trace) if trace_id]
    result = {
        "incremental_sample_size": incremental_sample,
        "sample_size": incremental_sample,
        "incremental_wins": incremental_wins,
        "incremental_win_rate": incremental_win_rate,
        "win_rate": win_rate_delta,
        "win_rate_delta": win_rate_delta,
        "incremental_return": return_delta,
        "return": return_delta,
        "return_delta": return_delta,
        "incremental_max_drawdown": drawdown_delta,
        "max_drawdown": drawdown_delta,
        "drawdown": drawdown_delta,
        "max_drawdown_delta": drawdown_delta,
        "candidate_metrics": dict(candidate),
        "baseline_metrics": dict(baseline),
        "evidence_refs": evidence_refs,
        "candidate_trace_id": candidate_trace,
        "baseline_trace_id": baseline_trace,
        "trace_id": _trace_value(trace_ids),
        "trace_ids": list(dict.fromkeys(trace_ids)),
    }
    return result


# 语义明确的函数别名，便于调用方按领域命名接入。
evaluate_strategy_results = arbitrate_strategy_results
evaluate_consensus = arbitrate_strategy_results
arbitrate = arbitrate_strategy_results


__all__ = [
    "BULLISH_RECOMMENDATIONS",
    "BEARISH_RECOMMENDATIONS",
    "NEUTRAL_RECOMMENDATIONS",
    "StrategyEvaluationError",
    "arbitrate",
    "arbitrate_strategy_results",
    "evaluate_consensus",
    "evaluate_incremental",
    "evaluate_strategy_results",
]
