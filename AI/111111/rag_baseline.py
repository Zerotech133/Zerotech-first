"""只读 RAG 基线与训练资格清单工具。

本模块只读取知识文档，生成哈希和保守资格状态；它不会修改源文件、安装训练依赖或启动交易能力。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

try:
    from .rag_web_backend import RagWebService
except ImportError:
    from rag_web_backend import RagWebService

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown"}
_REQUIRED_METADATA = (
    "license_id",
    "license_subject",
    "purpose",
    "valid_from",
    "valid_until",
    "commercial_scope",
    "source_reference",
    "split",
    "split_group",
    "offline_review_receipt",
    "license_approved",
    "permission_snapshot",
)
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _supported_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _missing_eligibility(metadata: Mapping[str, object], source_sha256: str) -> list[str]:
    missing = [
        field
        for field in _REQUIRED_METADATA
        if metadata.get(field) in (None, "", [], {})
    ]
    if metadata.get("sample_sha256") != source_sha256:
        missing.append("sample_sha256")
    if metadata.get("license_approved") is not True:
        missing.append("license_approved")
    permission = metadata.get("permission_snapshot")
    if not isinstance(permission, Mapping) or not permission.get("id"):
        missing.append("permission_snapshot")
    receipt = metadata.get("offline_review_receipt")
    if not isinstance(receipt, Mapping):
        missing.append("offline_review_receipt_format")
    else:
        if receipt.get("verified") is not True:
            missing.append("offline_review_receipt_verified")
        if not receipt.get("signature"):
            missing.append("offline_review_receipt_signature")
        if not receipt.get("proof_sha256"):
            missing.append("offline_review_receipt_proof_sha256")
    return sorted(set(missing))


def build_manifest(
    docs_dir: str | Path,
    metadata: Mapping[str, Mapping[str, object]] | None = None,
    generated_at: str | None = None,
) -> dict:
    """扫描支持文档并生成默认拒绝训练的清单。"""
    root = Path(docs_dir)
    if not root.is_dir():
        raise ValueError(f"知识目录不存在：{root}")
    metadata = metadata or {}
    records = []
    for path in _supported_files(root):
        relative = path.relative_to(root).as_posix()
        source_sha256 = _sha256(path)
        supplied = dict(metadata.get(relative, {}))
        missing = _missing_eligibility(supplied, source_sha256)
        eligible = not missing
        records.append(
            {
                "source": relative,
                "source_path": str(path.resolve()),
                "sample_sha256": source_sha256,
                "size_bytes": path.stat().st_size,
                "modified_ns": path.stat().st_mtime_ns,
                "license_id": supplied.get("license_id"),
                "license_subject": supplied.get("license_subject"),
                "purpose": supplied.get("purpose"),
                "valid_from": supplied.get("valid_from"),
                "valid_until": supplied.get("valid_until"),
                "commercial_scope": supplied.get("commercial_scope"),
                "source_reference": supplied.get("source_reference"),
                "split": supplied.get("split"),
                "split_group": supplied.get("split_group"),
                "offline_review_receipt": supplied.get("offline_review_receipt"),
                "training_eligible": eligible,
                "evaluation_only": not eligible,
                "eligibility_reasons": [] if eligible else [
                    f"缺少或未核验字段：{field}" for field in missing
                ],
            }
        )
    eligible_count = sum(1 for item in records if item["training_eligible"])
    return {
        "schema_version": "training-eligible-manifest-v0.1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_root": str(root.resolve()),
        "policy": {
            "default_status": "evaluation_only",
            "training_enabled": False,
            "financial_execution_enabled": False,
            "required_fields": list(_REQUIRED_METADATA) + ["sample_sha256"],
        },
        "counts": {
            "total": len(records),
            "training_eligible": eligible_count,
            "evaluation_only": len(records) - eligible_count,
        },
        "records": records,
    }


def build_title_cases(docs_dir: str | Path) -> list[dict]:
    """为每份文档生成一个仅用于检索命中率的标题问题。"""
    root = Path(docs_dir)
    if not root.is_dir():
        raise ValueError(f"知识目录不存在：{root}")
    cases = []
    for path in _supported_files(root):
        title = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _TITLE_RE.match(line.strip())
            if match:
                title = match.group(1)
                break
        source = path.relative_to(root).as_posix()
        cases.append(
            {"id": path.relative_to(root).with_suffix("").as_posix(), "question": title, "expected_sources": [source]}
        )
    return cases


def run_retrieval_eval(service, cases: Sequence[Mapping[str, object]], top_k: int = 4) -> dict:
    """运行只读检索评测，不生成训练样本或写入索引文件。"""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k 必须是正整数")
    results = []
    for case in cases:
        expected = {str(item) for item in case.get("expected_sources", [])}
        matches = service.search(str(case["question"]), top_k=top_k)
        returned = [str(match.get("source", "")) for match in matches]
        hit = bool(expected.intersection(returned))
        results.append(
            {
                "id": str(case.get("id", "")),
                "question": str(case["question"]),
                "expected_sources": sorted(expected),
                "returned_sources": returned,
                "scores": [round(float(match.get("score", 0)), 4) for match in matches],
                "retrieval_hit": hit,
                "citation_complete": hit,
            }
        )
    total = len(results)
    hits = sum(1 for item in results if item["retrieval_hit"])
    return {
        "summary": {
            "total": total,
            "hits": hits,
            "misses": total - hits,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        },
        "cases": results,
    }


def run_baseline(docs_dir: str | Path, client=None, top_k: int = 4) -> dict:
    """建立内存 RAG 索引并返回清单与检索评测结果。"""
    manifest = build_manifest(docs_dir)
    cases = build_title_cases(docs_dir)
    service = RagWebService(docs_dir=docs_dir, top_k=top_k, client=client)
    evaluation = run_retrieval_eval(service, cases, top_k=top_k)
    return {
        "manifest": manifest,
        "evaluation": evaluation,
        "gates": {
            "training_eligible": False,
            "lora_review_ready": False,
            "financial_execution_enabled": False,
        },
    }


def _write_json(path: str | Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成训练资格清单并运行只读 RAG 检索基线")
    parser.add_argument("--docs-dir", default="knowledge_docs")
    parser.add_argument("--manifest-out")
    parser.add_argument("--report-out")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args(argv)
    result = run_baseline(args.docs_dir, top_k=args.top_k)
    if args.manifest_out:
        _write_json(args.manifest_out, result["manifest"])
    if args.report_out:
        _write_json(args.report_out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
