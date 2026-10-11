"""重建零界学院 E 盘只读候选来源快照。

该工具只登记来源、证据和哈希，不批准许可证、不冻结训练集，也不启动模型、
模拟成交或交易。日期只能来自证据台账，禁止从文件名或 mtime 推断。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, date, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


SCHEMA = "academy_e_drive_candidate_snapshot/v1"
MANIFEST_SCHEMA = "academy_e_drive_candidate_manifest/v1"
TEXT_EXTS = {".md", ".py", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".ts", ".tsx", ".js"}
MAX_SOURCE_BYTES = 8 * 1024 * 1024
ALLOWED_EVIDENCE_STATUSES = {
    "complete",
    "pending_source_date",
    "complete_but_not_substitute",
}
SECRET_PATH_RE = re.compile(
    r"(^|/)(?:\.env(?:\.|$)|.*(?:private|secret|credential|password|token|api[_-]?key).*|.*\.pem$)",
    re.IGNORECASE,
)

# 评估 allowlist。10-08 原文保留在列表中，缺失时必须形成明确 blocker。
SELECTED_PATHS = (
    "knowledge_docs/缠论技术分析知识库.md",
    "knowledge_docs/金融专业知识库.md",
    "knowledge_docs/AI工程实践知识库.md",
    "docs/plans/2026-10-09_交易员训练与上岗体系v0.3.md",
    "docs/plans/2026-10-08_量子双刀策略研究报告.md",
    "docs/plans/2026-10-09_量子双刀策略研究报告.md",
    "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md",
    "docs/plans/2026-10-11_多Agent协作开发选型与施工图_K-STOCK-104.md",
    "docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md",
    "quant_backtest/INTEL_RD_PLAN.md",
    "quant_backtest/chanlun_signal.py",
    "quant_backtest/nine_dim.py",
    "agent_platform/a0_chief.py",
    "jev-eval/signal_review_gate.py",
    "portal/strategies.json",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_id_for(relative_path: str) -> str:
    """根据规范化相对路径生成稳定、不可变的来源 ID。"""
    normalized = str(relative_path).replace("\\", "/")
    return "E-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _safe_relative(raw: str) -> str | None:
    rel = str(raw).replace("\\", "/")
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None
    parts = PurePosixPath(rel).parts
    if ".." in parts:
        return None
    return "/".join(parts)


def _read_index(index_path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rel = str(item.get("path", "")).replace("\\", "/")
        if rel:
            records[rel] = item
    return records


def _parse_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if value == parsed.isoformat() else None


def _parse_observed_at(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _parse_evidence_ref(value: Any) -> str | None:
    """校验证据引用是可复核的本地或 Web URI。"""
    if not isinstance(value, str):
        return None
    ref = value.strip()
    if not ref or any(char.isspace() for char in ref):
        return None
    parsed = urlparse(ref)
    if parsed.scheme not in {"local", "http", "https"}:
        return None
    if parsed.scheme == "local":
        if not (parsed.netloc or parsed.path):
            return None
    elif not parsed.netloc:
        return None
    return ref


def _read_evidence(path: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("evidence_entries_invalid")
    result: dict[str, dict[str, Any]] = {}
    invalid: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            invalid.add("<invalid-entry>")
            continue
        rel = _safe_relative(entry.get("relative_path", ""))
        if not rel or rel in result:
            invalid.add(rel or "<invalid-path>")
            continue
        evidence_ref = _parse_evidence_ref(entry.get("evidence_ref"))
        if evidence_ref is None:
            invalid.add(rel)
            continue
        if entry.get("source_date") is not None and _parse_date(entry.get("source_date")) is None:
            invalid.add(rel)
            continue
        if _parse_observed_at(entry.get("observed_at")) is None:
            invalid.add(rel)
            continue
        evidence_status = entry.get("evidence_status")
        if evidence_status is None:
            evidence_status = "complete" if entry.get("source_date") else "pending_source_date"
        if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
            invalid.add(rel)
            continue
        result[rel] = {
            "source_date": _parse_date(entry.get("source_date")),
            "observed_at": entry["observed_at"],
            "evidence_ref": evidence_ref,
            "evidence_status": evidence_status,
        }
    return result, invalid


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_snapshot(index_path: Path, root: Path, evidence_path: Path, output_dir: Path) -> dict[str, Any]:
    records = _read_index(index_path)
    evidence, invalid_evidence = _read_evidence(evidence_path)
    root = Path(root).resolve()
    blockers: set[str] = {
        "human_review_pending",
        "standard_definitions_uncertified",
        "calendar_completeness_uncertified",
        "proposal_not_activated",
    }
    sources: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    hash_drift_paths: list[str] = []
    missing_source_paths: list[str] = []

    for rel in SELECTED_PATHS:
        if SECRET_PATH_RE.search(rel):
            rejected.append({"path": rel, "reason": "secret_source_rejected"})
            continue
        item = records.get(rel)
        path = root / Path(rel)
        if item is None or not path.is_file():
            missing_source_paths.append(rel)
            if rel == "docs/plans/2026-10-08_量子双刀策略研究报告.md":
                blockers.add("missing_exact_2026-10-08_quantum_double_blade_report")
            else:
                blockers.add(f"source_missing:{rel}")
            continue
        if path.suffix.lower() not in TEXT_EXTS:
            blockers.add(f"source_extension_rejected:{rel}")
            continue
        raw = path.read_bytes()
        digest = _sha256(raw)
        if len(raw) > MAX_SOURCE_BYTES:
            rejected.append({"path": rel, "reason": "source_too_large"})
            blockers.add("source_too_large")
            continue
        if item.get("readable") is False or len(raw) != item.get("bytes") or digest != item.get("sha256"):
            blockers.add("hash_drift")
            hash_drift_paths.append(rel)
            continue
        if rel in invalid_evidence:
            blockers.add("source_evidence_invalid")
            rejected.append({"path": rel, "reason": "source_evidence_invalid"})
            continue
        entry = evidence.get(rel)
        if entry is None:
            blockers.add("source_evidence_missing")
            entry = {"source_date": None, "observed_at": None, "evidence_ref": None, "evidence_status": "missing"}
        if entry.get("source_date") is None:
            blockers.add(f"source_date_missing:{rel}")
        source = {
            "source_id": source_id_for(rel),
            "path": str(path),
            "relative_path": rel,
            "bytes": len(raw),
            "sha256": digest,
            "source_class": "e_drive_reviewed_reference",
            "license_scope": "pending",
            "raw_source_available": True,
            **entry,
        }
        sources.append(source)

    if rejected:
        blockers.update(entry["reason"] for entry in rejected)
    if not sources:
        blockers.add("no_candidate_sources")
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshot_id = "e-drive-reference-v0.3-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "root": str(root),
        "mode": "read_only",
        "license_status": "pending",
        "training_eligible": False,
        "production_eligible": False,
        "sources": sources,
        "rejected": rejected,
        "blockers": sorted(blockers),
        "hash_drift_paths": sorted(hash_drift_paths),
        "missing_source_paths": sorted(missing_source_paths),
        "status": "evaluation_only",
        "missing_exact_source": "docs/plans/2026-10-08_量子双刀策略研究报告.md",
        "related_but_not_substitute": "docs/plans/2026-10-09_量子双刀策略研究报告.md",
    }
    entries = [
        {
            "sample_id": "EVAL-" + source["source_id"],
            "course_id": "RES-201",
            "task_type": "source_grounded_summary",
            "input_refs": [source["source_id"] + "#full"],
            "evidence_refs": [source["source_id"] + "#full"],
            "target_output": None,
            "review_status": "pending",
            "license_scope": "pending",
            "split": "evaluation_only",
            "split_group": source["source_id"],
            "sample_sha256": source["sha256"],
            "generation_mode": "candidate_only",
            "quality_flags": ["source_snapshot_pending", "human_review_pending"],
        }
        for source in sources
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": "candidate_only",
        "generated_at": generated_at,
        "dataset_version": "e-drive-reference-v0.3",
        "source_snapshot_id": snapshot_id,
        "training_eligible": False,
        "production_eligible": False,
        "entries": entries,
        "blockers": sorted(blockers | {"license_scope_pending", "evaluation_only_by_policy"}),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "candidate-source-snapshot-v0.3.json", snapshot)
    _write_json(output_dir / "candidate-eval-manifest-v0.3.json", manifest)
    return {"snapshot": snapshot, "manifest": manifest, "sources": sources, "blockers": sorted(blockers)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a read-only E-drive candidate source snapshot")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(r"E:\老田的AI助手"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_snapshot(args.index, args.root, args.evidence, args.output)
    print(json.dumps({"sources": len(result["sources"]), "blockers": result["blockers"], "training_eligible": False}, ensure_ascii=False))
    return 0 if result["sources"] and not result["blockers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

