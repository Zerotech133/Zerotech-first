"""只读审计训练候选清单。

该工具只读取候选 manifest 和来源文件，生成缺口报告；它不会修改来源、
许可证、权限快照，也不会把样本提升为可训练或可生产状态。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_SPLITS = frozenset({"train", "validation", "test"})
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUIRED = (
    "license_id", "license_subject", "purpose", "valid_from", "valid_until",
    "commercial_scope", "source_reference", "split", "split_group",
    "offline_review_receipt", "license_approved", "permission_snapshot",
    "sample_sha256",
)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if value == parsed.isoformat() else None


def _iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _valid_reference(value: Any) -> bool:
    if not _nonempty(value) or any(ch.isspace() for ch in value.strip()):
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"local", "http", "https"}:
        return False
    return bool(parsed.netloc or parsed.path)


def _valid_receipt(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("receipt_id", "subject", "signed_at", "signature",
                "public_key_fingerprint")
    if any(not _nonempty(value.get(name)) for name in required):
        return False
    if _iso_datetime(value["signed_at"]) is None:
        return False
    return bool(_SHA256.fullmatch(value["public_key_fingerprint"]))


def _valid_permission(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("snapshot_id", "subject", "valid_until", "sha256")
    if any(not _nonempty(value.get(name)) for name in required):
        return False
    if _iso_date(value["valid_until"]) is None:
        return False
    return bool(_SHA256.fullmatch(value["sha256"]))


def _source_path(root: Path, raw: Any) -> Path | None:
    if not _nonempty(raw):
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return resolved


def _hash_ok(root: Path, record: dict[str, Any], reasons: set[str]) -> None:
    expected = record.get("sample_sha256")
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        reasons.add("invalid:sample_sha256")
        return
    path = _source_path(root, record.get("source_path"))
    if path is None or not path.is_file():
        reasons.add("source_missing")
        return
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        reasons.add("source_unreadable")
        return
    if actual.lower() != expected.lower():
        reasons.add("sample_hash_mismatch")


def _record_reasons(record: dict[str, Any], root: Path,
                    as_of: date | None) -> set[str]:
    reasons: set[str] = set()
    for field in _REQUIRED:
        if field not in record or record[field] is None or record[field] == "":
            reasons.add(f"missing:{field}")
    if record.get("license_approved") is not True:
        reasons.add("license_not_approved")
    if record.get("split") not in _SPLITS:
        reasons.add("invalid:split")
    if not _valid_reference(record.get("source_reference")):
        reasons.add("invalid:source_reference")
    start, end = _iso_date(record.get("valid_from")), _iso_date(record.get("valid_until"))
    if start is None or end is None or end <= start:
        reasons.add("invalid:license_period")
    elif as_of is not None and end <= as_of:
        reasons.add("license_expired")
    if not _valid_receipt(record.get("offline_review_receipt")):
        reasons.add("invalid:offline_review_receipt")
    if not _valid_permission(record.get("permission_snapshot")):
        reasons.add("invalid:permission_snapshot")
    _hash_ok(root, record, reasons)
    return reasons


def audit_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """审计 manifest，返回稳定、可 JSON 序列化的报告。"""
    policy = manifest.get("policy") or {}
    root = Path(str(manifest.get("source_root") or "."))
    as_of = _iso_date(policy.get("as_of"))
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("records 必须是 JSON 列表")

    audited: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    groups: dict[str, set[str]] = {}
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raw = {}
        sample_id = str(raw.get("sample_id") or raw.get("source") or f"row-{index}")
        reasons = _record_reasons(raw, root, as_of)
        if sample_id in seen_ids:
            reasons.add("duplicate:sample_id")
        seen_ids[sample_id] = index
        group = raw.get("split_group")
        split = raw.get("split")
        if _nonempty(group) and split in _SPLITS:
            groups.setdefault(group, set()).add(split)
        audited.append({
            "sample_id": sample_id,
            "source": raw.get("source"),
            "split": split,
            "split_group": group,
            "sample_sha256": raw.get("sample_sha256"),
            "reasons": reasons,
        })

    for item in audited:
        group = item["split_group"]
        if group and len(groups.get(group, set())) > 1:
            item["reasons"].add("split_group_cross_split")

    training_enabled = policy.get("training_enabled") is True
    strategy_training_enabled = policy.get("strategy_training_enabled") is True
    financial_execution_enabled = policy.get("financial_execution_enabled") is True
    simulated_execution_enabled = policy.get("simulated_execution_enabled") is True
    commercialization_enabled = policy.get("commercialization_enabled") is True
    output_records = []
    for item in audited:
        reasons = set(item["reasons"])
        content_eligible = not reasons
        if not training_enabled:
            reasons.add("training_disabled")
        if not strategy_training_enabled:
            reasons.add("strategy_training_disabled")
        if strategy_training_enabled and training_enabled:
            reasons.add("strategy_training_requires_training_off")
        if strategy_training_enabled and financial_execution_enabled:
            reasons.add("strategy_training_requires_financial_execution_off")
        if strategy_training_enabled and simulated_execution_enabled:
            reasons.add("strategy_training_requires_simulated_execution_off")
        if strategy_training_enabled and commercialization_enabled:
            reasons.add("strategy_training_requires_commercialization_off")
        training_eligible = content_eligible and training_enabled
        strategy_training_eligible = (
            content_eligible
            and strategy_training_enabled
            and not training_enabled
            and not financial_execution_enabled
            and not simulated_execution_enabled
            and not commercialization_enabled
        )
        output_records.append({
            **{key: item[key] for key in ("sample_id", "source", "split",
                                          "split_group", "sample_sha256")},
            "content_eligible": content_eligible,
            "training_eligible": training_eligible,
            "strategy_training_eligible": strategy_training_eligible,
            "evaluation_only": not (training_eligible or strategy_training_eligible),
            "reasons": sorted(reasons),
        })

    content_count = sum(item["content_eligible"] for item in output_records)
    training_count = sum(item["training_eligible"] for item in output_records)
    strategy_training_count = sum(
        item["strategy_training_eligible"] for item in output_records
    )
    evaluation_only_count = sum(
        not (item["training_eligible"] or item["strategy_training_eligible"])
        for item in output_records
    )
    return {
        "schema": "training_candidate_governance_report/v1",
        "manifest_schema": manifest.get("schema_version"),
        "source_root": str(root),
        "policy": {
            "training_enabled": training_enabled,
            "strategy_training_enabled": strategy_training_enabled,
            "financial_execution_enabled": financial_execution_enabled,
            "simulated_execution_enabled": simulated_execution_enabled,
            "commercialization_enabled": commercialization_enabled,
            "as_of": policy.get("as_of"),
        },
        "counts": {
            "total": len(output_records),
            "content_eligible": content_count,
            "training_eligible": training_count,
            "strategy_training_eligible": strategy_training_count,
            "evaluation_only": evaluation_only_count,
        },
        "training_eligible": training_count > 0,
        "strategy_training_eligible": strategy_training_count > 0,
        "production_eligible": False,
        "records": output_records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读审计训练候选清单")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = audit_manifest(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"],
                      "training_eligible": report["training_eligible"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
