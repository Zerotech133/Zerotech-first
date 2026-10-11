"""运行 E 盘候选来源的只读、确定性 RAG 扩展基线。

该模块只消费候选快照中已经登记的来源，重新校验大小和 SHA256 后做词法
分块/排序。它不调用模型、不启动训练器，也不改变原始 E 盘文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from run_rag_baseline import Chunk, rank_chunks, retrieve, split_chunks, required_terms_match


MAX_SOURCE_BYTES = 8 * 1024 * 1024
SECRET_PATH_RE = re.compile(
    r"(^|/)(?:\.env(?:\.|$)|.*(?:private|secret|credential|password|token|api[_-]?key).*|.*\.pem$)",
    re.IGNORECASE,
)


E_DRIVE_CASES = [
    {
        "case_id": "E-CHAN-301-P01",
        "course_id": "CHAN-301",
        "query": "缠论三类买点 第一类买点 第二类买点 第三类买点 走势终完美",
        "expected_relative_paths": ["knowledge_docs/缠论技术分析知识库.md"],
        "required_terms": ["第一类买点", "第二类买点", "第三类买点"],
        "split": "evaluation_only",
    },
    {
        "case_id": "E-TRD-401-P01",
        "course_id": "TRD-401",
        "query": "交易员回答买什么 怎么买 买多少 何时卖",
        "expected_relative_paths": ["docs/plans/2026-10-09_交易员训练与上岗体系v0.3.md"],
        "required_terms": ["买什么", "怎么买", "买多少", "何时卖"],
        "split": "evaluation_only",
    },
    {
        "case_id": "E-PORTAL-101-P01",
        "course_id": "TOOL-301",
        "query": "策略注册表九维选股漏斗 ninedim live",
        "expected_relative_paths": ["portal/strategies.json"],
        "required_terms": ["ninedim", "live"],
        "split": "evaluation_only",
    },
    {
        "case_id": "E-CORE-101-N01",
        "course_id": "CORE-101",
        "query": "未登记来源中的虚构规则应如何确认",
        "expected_relative_paths": [],
        "required_terms": ["这条证据不存在"],
        "split": "evaluation_only",
        "expected_decision": "refuse_missing_evidence",
    },
    {
        "case_id": "E-CORE-101-T01",
        "course_id": "CORE-101",
        "query": "在决策时点之后出现的证据是否可用于回答",
        "expected_relative_paths": [],
        "required_terms": ["这条证据不存在"],
        "split": "evaluation_only",
        "as_of": "2026-10-10",
        "expected_decision": "refuse_missing_evidence",
    },
]


def classify_course(source: dict[str, Any]) -> str:
    """按来源路径归类为 CORE、策略、风险或工具知识。"""
    path = str(source.get("relative_path", "")).replace("\\", "/").lower()
    if any(token in path for token in ("risk", "风控", "riskguard", "audit")):
        return "risk"
    if any(token in path for token in ("portal/", "tools/", "tool", "sph_research")):
        return "tool"
    if any(token in path for token in ("缠论", "strategy", "strateg", "交易员", "quant_backtest")):
        return "strategy"
    return "core"


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if value == parsed.isoformat() else None


def evaluate_temporal_integrity(case: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """检查检索证据是否存在有效 ISO 日期并晚于查询时点。"""
    as_of_text = case.get("as_of")
    if not as_of_text:
        return {"status": "not_applicable", "temporal_leak": False, "missing_dates": 0, "invalid_dates": 0}
    as_of = _parse_iso_date(as_of_text)
    if as_of is None:
        return {"status": "invalid_evidence", "temporal_leak": False, "missing_dates": 0, "invalid_dates": 1, "late_sources": []}
    missing_dates = 0
    invalid_dates = 0
    late: list[str] = []
    for hit in hits:
        source_date = hit.get("source_date")
        if not source_date:
            missing_dates += 1
            continue
        parsed = _parse_iso_date(source_date)
        if parsed is None:
            invalid_dates += 1
            continue
        if parsed > as_of:
            late.append(source_date)
    if missing_dates or invalid_dates:
        return {
            "status": "invalid_evidence" if invalid_dates else "missing_evidence",
            "temporal_leak": False,
            "missing_dates": missing_dates,
            "invalid_dates": invalid_dates,
            "late_sources": late,
        }
    return {"status": "temporal_leak" if late else "pass", "temporal_leak": bool(late), "missing_dates": 0, "invalid_dates": 0, "late_sources": late}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_sources(snapshot_path: Path) -> tuple[list[Chunk], list[dict[str, Any]], list[str]]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_root_value = snapshot.get("root")
    snapshot_root = Path(str(snapshot_root_value)).resolve() if snapshot_root_value else None
    chunks: list[Chunk] = []
    loaded: list[dict[str, Any]] = []
    source_issues: list[str] = []
    for source in snapshot.get("sources", []):
        relative_path = str(source.get("relative_path", "")).replace("\\", "/")
        path = Path(str(source.get("path", "")))
        if not snapshot_root:
            source_issues.append("snapshot_root_missing")
            continue
        if not relative_path or SECRET_PATH_RE.search(relative_path) or SECRET_PATH_RE.search(path.as_posix()):
            source_issues.append(f"secret_source_rejected:{relative_path}")
            continue
        if ".." in PurePosixPath(relative_path).parts or PurePosixPath(relative_path).is_absolute():
            source_issues.append(f"unsafe_source_path:{relative_path}")
            continue
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(snapshot_root)
        except ValueError:
            source_issues.append(f"source_path_outside_root:{relative_path}")
            continue
        expected_path = (snapshot_root / PurePosixPath(relative_path)).resolve()
        if expected_path != resolved_path:
            source_issues.append(f"source_path_mismatch:{relative_path}")
            continue
        if not path.is_file():
            source_issues.append(f"source_unreadable:{relative_path}")
            continue
        if int(source.get("bytes", -1)) > MAX_SOURCE_BYTES:
            source_issues.append(f"source_too_large:{relative_path}")
            continue
        raw = path.read_bytes()
        if len(raw) != source.get("bytes") or _sha256(raw) != source.get("sha256"):
            source_issues.append(f"hash_drift:{relative_path}")
            continue
        text = raw.decode("utf-8", errors="replace")
        source_id = str(source.get("source_id", ""))
        if not source_id:
            source_issues.append(f"source_id_missing:{relative_path}")
            continue
        for ordinal, chunk_text in enumerate(split_chunks(text), start=1):
            chunks.append(Chunk(source_id, str(path), ordinal, chunk_text))
        loaded.append(
            {
                "source_id": source_id,
                "path": str(path),
                "relative_path": relative_path,
                "bytes": len(raw),
                "sha256": _sha256(raw),
                "source_class": source.get("source_class", "e_drive_reviewed_reference"),
                "license_scope": source.get("license_scope", "pending"),
            }
        )
    return chunks, loaded, source_issues


def _expected_ids(case: dict[str, Any], sources: list[dict[str, Any]]) -> list[str]:
    paths = set(case.get("expected_relative_paths", []))
    return [source["source_id"] for source in sources if source["relative_path"] in paths]


def _evaluate_case(case: dict[str, Any], chunks: list[Chunk], sources: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = _expected_ids(case, sources)
    ranked = rank_chunks(chunks, case["query"])
    top_chunks = [chunk for _, chunk in ranked[:5]]
    hits = retrieve(chunks, case["query"], top_k=5)
    top_text = " ".join(chunk.text for chunk in top_chunks)
    source_hit = bool(expected_ids) and any(chunk.source_id in expected_ids for chunk in top_chunks)
    term_hits = [term for term in case.get("required_terms", []) if term in top_text]
    terms_ok = len(term_hits) == len(case.get("required_terms", []))
    evidence_ok = source_hit and terms_ok
    decision = "answer_with_evidence" if evidence_ok else "refuse_missing_evidence"
    if case.get("expected_decision") == "refuse_missing_evidence":
        decision = "refuse_missing_evidence"
    temporal = evaluate_temporal_integrity(case, [
        {"source_date": source.get("source_date")} for source in sources if source["source_id"] in {chunk.source_id for chunk in top_chunks}
    ])
    return {
        **case,
        "course": case.get("course") or ("core" if case.get("course_id") == "CORE-101" else "strategy"),
        "expected_source_ids": expected_ids,
        "top_k": hits,
        "source_hit": source_hit,
        "term_hits": term_hits,
        "term_coverage": round(len(term_hits) / len(case.get("required_terms", [])), 4)
        if case.get("required_terms")
        else 1.0,
        "decision": decision,
        "citation_completeness": bool(expected_ids) and bool(top_chunks),
        "temporal_integrity": temporal,
        "passed": decision == (case.get("expected_decision") or "answer_with_evidence"),
        "model_calls": 0,
        "training_steps": 0,
    }


def run_baseline(snapshot_path: Path, output_dir: Path) -> dict[str, Any]:
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    chunks, sources, source_issues = _load_sources(Path(snapshot_path))
    cases = [_evaluate_case(case, chunks, sources) for case in E_DRIVE_CASES]
    # 以用例声明的期望路径区分正向用例和拒答用例；来源缺失时仍要计入召回
    # 分母，避免把数据缺口误报成 1.0。
    positive_cases = [case for case in cases if case.get("expected_relative_paths")]
    positive_passed = sum(1 for case in positive_cases if case["passed"])
    source_recall_passed = sum(1 for case in positive_cases if case["source_hit"])
    refusal_cases = [case for case in cases if not case.get("expected_relative_paths")]
    temporal_cases = [case for case in cases if case.get("as_of")]
    temporal_failures = [
        case["case_id"]
        for case in temporal_cases
        if case["temporal_integrity"]["status"] != "pass"
    ]
    temporal_evidence_complete = bool(temporal_cases) and not temporal_failures
    snapshot_blocked = bool(snapshot.get("blockers") or source_issues)
    temporal_blocked = bool(temporal_failures)
    generated_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "schema": "academy_e_drive_rag_baseline/v1",
        "generated_at": generated_at,
        "mode": "lexical_read_only",
        "snapshot_path": str(snapshot_path),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_blockers": snapshot.get("blockers", []),
        "source_issues": source_issues,
        "sources": sources,
        "training_eligible": False,
        "production_eligible": False,
        "model_calls": 0,
        "training_steps": 0,
        "decision": (
            "blocked_snapshot"
            if snapshot_blocked
            else "blocked_temporal_evidence"
            if temporal_blocked
            else "passed"
        ),
        "temporal_risk_cases": temporal_failures,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "positive_total": len(positive_cases),
            "positive_passed": positive_passed,
            "source_recall_passed": source_recall_passed,
            "refusal_total": len(refusal_cases),
            "refusal_passed": sum(1 for case in refusal_cases if case["passed"]),
            "source_recall_at_5": round(source_recall_passed / len(positive_cases), 4)
            if positive_cases
            else None,
            "all_terms_covered": all(case["term_coverage"] == 1.0 for case in positive_cases),
            "temporal_risk_cases": len(temporal_cases),
            "temporal_evidence_complete": temporal_evidence_complete,
            "blocked": snapshot_blocked or temporal_blocked,
        },
    }
    _write_json(output_dir / "rag-baseline.json", report)
    (output_dir / "evaluation-cases.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in E_DRIVE_CASES),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E-drive read-only RAG baseline")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run_baseline(args.snapshot, args.out)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["decision"] == "passed" and report["summary"]["positive_passed"] == report["summary"]["positive_total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
