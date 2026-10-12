"""策略分层验收的只读门禁审计。

该工具只读取声明式验收矩阵、策略证据和 P1 可观测性报告，输出稳定的
verdict。缺证据、时序泄漏、标准定义或日历未认证时默认阻断；工具不会
修改来源、策略、账本、权限或任何金融执行开关。
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

_SCHEMA = "strategy_acceptance_matrix/v1"
_SPLITS = ("train", "validation", "test")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_EXECUTION_FLAGS = (
    "financial_execution_enabled",
    "training_enabled",
    "commercialization_enabled",
)
_OPTIONAL_EXECUTION_FLAGS = ("simulated_execution_enabled",)
_GLOBAL_REQUIREMENTS = (
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
)
_MANDATORY_EVIDENCE = (
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
)
_EVIDENCE_SCHEMA = "strategy_acceptance_evidence/v1"
_P1_SCHEMA = "p1_observability_audit/v0.1"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _validate_matrix(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(matrix, dict):
        raise ValueError("matrix 必须是对象")
    if matrix.get("schema") != _SCHEMA:
        raise ValueError(f"schema 必须是 {_SCHEMA}")
    global_requirements = matrix.get("global_requirements")
    if not isinstance(global_requirements, list):
        raise ValueError("global_requirements 必须是列表并包含全部 mandatory 要求")
    if any(not isinstance(item, str) or not item.strip() for item in global_requirements):
        raise ValueError("global_requirements 必须只包含非空字符串")
    missing_global = sorted(set(_GLOBAL_REQUIREMENTS) - set(global_requirements))
    if missing_global:
        raise ValueError(
            "global_requirements 缺少 mandatory 要求: " + ", ".join(missing_global)
        )
    policy = matrix.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("policy 必须是对象")
    required_policy = (
        "standard_definitions_certified",
        "calendar_certified",
        *_EXECUTION_FLAGS,
    )
    missing_policy = [key for key in required_policy if key not in policy]
    if missing_policy:
        raise ValueError("policy 缺少 mandatory 字段: " + ", ".join(missing_policy))
    invalid_policy = [key for key in required_policy if not isinstance(policy[key], bool)]
    if invalid_policy:
        raise ValueError("policy 字段必须是布尔值: " + ", ".join(invalid_policy))
    strategies = matrix.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("strategies 必须是非空列表")
    seen: set[str] = set()
    for item in strategies:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("strategies 中每项必须包含 id")
        sid = item["id"].strip()
        if not sid or sid in seen:
            raise ValueError("strategies 的 id 必须唯一且非空")
        seen.add(sid)
        required = item.get("required_evidence")
        if not isinstance(required, list) or not required:
            raise ValueError(f"strategy {sid} 缺少 required_evidence")
        if any(not isinstance(key, str) or not key.strip() for key in required):
            raise ValueError(f"strategy {sid} 的 required_evidence 必须只包含非空字符串")
        missing_required = sorted(set(_MANDATORY_EVIDENCE) - set(required))
        if missing_required:
            raise ValueError(
                f"strategy {sid} 缺少 mandatory evidence: " + ", ".join(missing_required)
            )
        minimums = item.get("minimums")
        if not isinstance(minimums, dict):
            raise ValueError(f"strategy {sid} 缺少 minimums")
        for key in ("sample_count", "trading_days"):
            if key in minimums:
                try:
                    if not math.isfinite(float(minimums[key])):
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError(f"strategy {sid} 的 minimums.{key} 必须是有限数字")
    return strategies


def _p1_reasons(report: dict[str, Any] | None) -> list[str]:
    if not isinstance(report, dict):
        return ["p1:report_missing"]
    reasons: list[str] = []
    if report.get("schema") != _P1_SCHEMA:
        reasons.append("p1:invalid_report_schema")
    if report.get("read_only") is not True:
        reasons.append("p1:report_not_read_only")
    alert = report.get("alert_fail")
    scheduler = report.get("scheduler")
    employment = report.get("employment")
    if not isinstance(alert, dict):
        alert = {}
    if not isinstance(scheduler, dict):
        scheduler = {}
    if not isinstance(employment, dict):
        employment = {}
    if alert.get("coverage_gap") is not False:
        reasons.append("p1:alert_coverage_gap")
    if scheduler.get("actual_execution_evidence") is not True:
        reasons.append("p1:scheduler_execution_evidence_missing")
    if employment.get("three_source_skip_verified") is not True:
        reasons.append("p1:three_source_skip_unverified")
    return reasons


def _global_reasons(policy: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if policy.get("standard_definitions_certified") is not True:
        reasons.append("global:standard_definition_uncertified")
    if policy.get("calendar_certified") is not True:
        reasons.append("global:calendar_uncertified")
    for name in _EXECUTION_FLAGS:
        if policy.get(name) is True:
            reasons.append(f"global:{name}_must_be_false")
    for name in _OPTIONAL_EXECUTION_FLAGS:
        if policy.get(name) is True:
            reasons.append(f"global:{name}_must_be_false")
    return reasons


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _valid_source_reference(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or any(char.isspace() for char in value):
        return False
    lowered = value.lower()
    return any(
        lowered.startswith(prefix) and len(value) > len(prefix)
        for prefix in ("local:", "local://", "file:", "http://", "https://")
    )


def _time_split_reasons(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != set(_SPLITS):
        return ["invalid:time_split"]
    reasons: list[str] = []
    if any(not isinstance(value.get(k), list) or not value[k] for k in _SPLITS):
        reasons.extend(("invalid:time_split", "time_order_invalid"))
    groups: dict[str, set[str]] = {}
    sample_locations: dict[str, set[str]] = {}
    dates: dict[str, list[date]] = {split: [] for split in _SPLITS}
    for split in _SPLITS:
        for row in value[split]:
            if not isinstance(row, dict) or not _present(row.get("sample_id")):
                reasons.append("invalid:time_split")
                continue
            group = row.get("split_group")
            if not _present(group):
                reasons.append("invalid:time_split")
            else:
                groups.setdefault(str(group), set()).add(split)
            sample_id = str(row.get("sample_id"))
            sample_locations.setdefault(sample_id, set()).add(split)
            parsed_date = _parse_iso_date(row.get("date"))
            if parsed_date is None:
                reasons.append("invalid:time_split")
            else:
                dates[split].append(parsed_date)
    if any(len(splits) > 1 for splits in groups.values()):
        reasons.append("split_group_cross_split")
    if any(len(splits) > 1 for splits in sample_locations.values()):
        reasons.append("sample_id_cross_split")
    if any(len(dates[split]) != len(value[split]) for split in _SPLITS):
        reasons.append("invalid:time_split")
    elif all(dates[split] for split in _SPLITS) and not (
        max(dates["train"]) < min(dates["validation"])
        and max(dates["validation"]) < min(dates["test"])
    ):
        reasons.append("time_order_invalid")
    return sorted(set(reasons))


def _valid_offline_receipt(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("receipt_id", "subject", "signed_at", "public_key_fingerprint")
    if any(not _present(value.get(key)) for key in required):
        return False
    try:
        parsed = datetime.fromisoformat(str(value["signed_at"]))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        return False
    return bool(_SHA256.fullmatch(str(value["public_key_fingerprint"])))


def _valid_source_date_receipt(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("receipt_id", "source_date", "observed_at", "source_sha256")
    if any(not _present(value.get(key)) for key in required):
        return False
    source_date = _parse_iso_date(value.get("source_date"))
    observed_at = _parse_timestamp(value.get("observed_at"))
    if source_date is None or observed_at is None:
        return False
    if source_date > observed_at.date():
        return False
    return bool(_SHA256.fullmatch(str(value["source_sha256"])))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _evidence_reasons(spec: dict[str, Any], record: dict[str, Any]) -> list[str]:
    reasons: set[str] = set()
    for key in spec["required_evidence"]:
        if not _present(record.get(key)):
            reasons.add(f"missing:{key}")
    minimums = spec.get("minimums") or {}
    for key in ("sample_count", "trading_days"):
        if key not in minimums:
            continue
        value = record.get(key)
        if not _present(value):
            continue
        numeric = _finite_number(value)
        minimum = _finite_number(minimums[key])
        if numeric is None or minimum is None:
            reasons.add(f"invalid:{key}")
        else:
            if numeric < minimum:
                reasons.add(f"{key}_below_minimum")
    time_split = record.get("time_split")
    if _present(time_split):
        reasons.update(_time_split_reasons(time_split))
    benchmark = record.get("benchmark")
    if _present(benchmark) and (
        not isinstance(benchmark, dict)
        or not _present(benchmark.get("id"))
        or benchmark.get("window") != "same_window"
    ):
        reasons.add("invalid:benchmark")
    cost = record.get("cost_model")
    if _present(cost):
        if not isinstance(cost, dict):
            reasons.add("invalid:cost_model")
        else:
            for key in ("commission_bps", "slippage_bps"):
                numeric = _finite_number(cost.get(key))
                if numeric is None or numeric < 0:
                    reasons.add("invalid:cost_model")
    source_reference = record.get("source_reference")
    if _present(source_reference) and not _valid_source_reference(source_reference):
        reasons.add("invalid:source_reference")
    source_date_receipt = record.get("source_date_receipt")
    if _present(source_date_receipt) and not _valid_source_date_receipt(source_date_receipt):
        reasons.add("invalid:source_date_receipt")
    hashes = record.get("source_hashes")
    if _present(hashes):
        if (
            not isinstance(hashes, list)
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes)
            or len(set(hashes)) != len(hashes)
        ):
            reasons.add("invalid:source_hashes")
        else:
            sample_count = _finite_number(record.get("sample_count"))
            if sample_count is not None and len(hashes) < sample_count:
                reasons.add("source_hashes_count_mismatch")
    receipt = record.get("offline_review_receipt")
    if _present(receipt) and not _valid_offline_receipt(receipt):
        reasons.add("invalid:offline_review_receipt")
    rejects = record.get("missing_data_rejections")
    if _present(rejects) and (
        not isinstance(rejects, list)
        or any(not isinstance(row, dict) or row.get("status") != "rejected" for row in rejects)
    ):
        reasons.add("invalid:missing_data_rejections")
    snapshots = record.get("transient_structure_snapshots")
    expected_fields = set(spec.get("transient_structure_evidence") or [])
    if _present(snapshots):
        if not isinstance(snapshots, list) or not snapshots:
            reasons.add("invalid:transient_structure_snapshots")
        elif expected_fields:
            actual = set()
            for row in snapshots:
                if isinstance(row, dict) and isinstance(row.get("fields"), list):
                    actual.update(str(field) for field in row["fields"])
            if not expected_fields.issubset(actual):
                reasons.add("incomplete:transient_structure_snapshots")
    return sorted(reasons)


def audit_matrix(
    matrix: dict[str, Any],
    evidence: dict[str, Any] | None,
    p1_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """审计矩阵并返回只读、默认阻断的报告。"""
    specs = _validate_matrix(matrix)
    policy = matrix["policy"]
    p1_reasons = _p1_reasons(p1_report)
    global_reasons = _global_reasons(policy)
    strategy_training_enabled = policy.get("strategy_training_enabled") is True
    simulated_execution_enabled = policy.get("simulated_execution_enabled") is True
    evidence_reasons: set[str] = set()
    if not isinstance(evidence, dict) or evidence.get("schema") != _EVIDENCE_SCHEMA:
        evidence_reasons.add("invalid:evidence_schema")
    records = evidence.get("strategies", {}) if isinstance(evidence, dict) else {}
    if not isinstance(records, dict):
        evidence_reasons.add("invalid:evidence_records")
        records = {}
    output: list[dict[str, Any]] = []
    for spec in specs:
        sid = spec["id"]
        record = records.get(sid)
        reasons = set(global_reasons) | set(p1_reasons) | evidence_reasons
        if not isinstance(record, dict):
            reasons.add("missing:evidence_record")
            record = {}
        reasons.update(_evidence_reasons(spec, record))
        verdict = "ready_for_shadow_review" if not reasons else "blocked"
        output.append({
            "id": sid,
            "name": spec.get("name", sid),
            "verdict": verdict,
            "production_eligible": False,
            "training_eligible": False,
            "reasons": sorted(reasons),
            "evidence_summary": {
                "sample_count": record.get("sample_count"),
                "trading_days": record.get("trading_days"),
                "required_evidence": list(spec["required_evidence"]),
            },
        })
    return {
        "schema": "strategy_acceptance_report/v0.1",
        "matrix_schema": matrix["schema"],
        "read_only": True,
        "financial_execution_enabled": False,
        "training_enabled": False,
        "strategy_training_enabled": strategy_training_enabled,
        "simulated_execution_enabled": simulated_execution_enabled,
        "commercialization_enabled": False,
        "policy": {
            "standard_definitions_certified": policy.get("standard_definitions_certified") is True,
            "calendar_certified": policy.get("calendar_certified") is True,
            "financial_execution_enabled": False,
            "training_enabled": False,
            "strategy_training_enabled": strategy_training_enabled,
            "simulated_execution_enabled": simulated_execution_enabled,
            "commercialization_enabled": False,
        },
        "gate_reasons": sorted(set(global_reasons) | set(p1_reasons) | evidence_reasons),
        "p1_status": "ready" if not p1_reasons else "blocked",
        "strategies": output,
        "all_strategies_ready_for_shadow_review": bool(output)
        and all(row["verdict"] == "ready_for_shadow_review" for row in output),
        "training_eligible": False,
        "strategy_training_eligible": (
            strategy_training_enabled
            and bool(output)
            and all(row["verdict"] == "ready_for_shadow_review" for row in output)
        ),
        "production_eligible": False,
        "commercialization_eligible": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读策略分层验收审计")
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--p1-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else {}
        p1 = json.loads(args.p1_report.read_text(encoding="utf-8")) if args.p1_report else None
        report = audit_matrix(matrix, evidence, p1)
    except Exception as exc:  # CLI 必须在输入异常时写入可审计的阻断报告
        report = {
            "schema": "strategy_acceptance_report/v0.1",
            "status": "error",
            "read_only": True,
            "financial_execution_enabled": False,
            "training_enabled": False,
            "strategy_training_enabled": False,
            "simulated_execution_enabled": False,
            "commercialization_enabled": False,
            "gate_reasons": ["audit:input_invalid"],
            "p1_status": "blocked",
            "strategies": [],
            "all_strategies_ready_for_shadow_review": False,
            "training_eligible": False,
            "strategy_training_eligible": False,
            "production_eligible": False,
            "commercialization_eligible": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": "error", "gate_reasons": report["gate_reasons"]}, ensure_ascii=False))
        return 2
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "p1_status": report["p1_status"],
        "strategies": {row["id"]: row["verdict"] for row in report["strategies"]},
        "training_eligible": report["training_eligible"],
        "production_eligible": report["production_eligible"],
        "financial_execution_enabled": report["financial_execution_enabled"],
        "strategy_training_enabled": report["strategy_training_enabled"],
        "strategy_training_eligible": report["strategy_training_eligible"],
        "commercialization_eligible": report["commercialization_eligible"],
        "all_strategies_ready_for_shadow_review": report["all_strategies_ready_for_shadow_review"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




