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
    parser.add_argument("--offline-regression", action="store_true", help="使用可控替身运行离线回归")
    parser.add_argument("--cases", help="离线回归 JSON/JSONL 评测样例")
    args = parser.parse_args(argv)
    if args.offline_regression:
        result = run_offline_regression(args.docs_dir, cases_path=args.cases, report_out=args.report_out, top_k=args.top_k)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result = run_baseline(args.docs_dir, top_k=args.top_k)
    if args.manifest_out:
        _write_json(args.manifest_out, result["manifest"])
    if args.report_out:
        _write_json(args.report_out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


class OfflineEmbeddingClient:
    """可重复的本地向量替身；只在内存中计算，不连接 Ollama。"""

    def __init__(self, dimensions: int = 256):
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 32:
            raise ValueError("dimensions 必须是不小于 32 的整数")
        self.dimensions = dimensions
        self.embed_calls = 0

    @staticmethod
    def _tokens(text: str) -> list[str]:
        lowered = str(text).lower()
        tokens = re.findall(r"[a-z0-9_./-]+", lowered)
        for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.extend(run)
            tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
        return [token for token in tokens if token]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        return vector

    def embed(self, model, input):  # noqa: ARG002 - 与 KnowledgeBase client 接口一致
        values = [input] if isinstance(input, str) else list(input)
        self.embed_calls += len(values)
        return {"embeddings": [self._vector(value) for value in values]}


class _OfflineService:
    """把 KnowledgeBase 包成与 RagWebService 相同的只读 search 接口。"""

    def __init__(self, docs_dir: str | Path, top_k: int = 4, client: OfflineEmbeddingClient | None = None):
        try:
            from .knowledge_base import KnowledgeBase
        except ImportError:
            from knowledge_base import KnowledgeBase

        self.docs_dir = Path(docs_dir)
        self.client = client or OfflineEmbeddingClient()
        self._kb = KnowledgeBase(client=self.client, top_k=top_k)
        self._kb.ingest(self._kb.read_documents(self.docs_dir))

    def search(self, question: str, top_k: int | None = None) -> list[dict]:
        requested = self._kb.top_k if top_k is None else top_k
        old_top_k = self._kb.top_k
        self._kb.top_k = requested
        try:
            matches = self._kb.search(question)
        finally:
            self._kb.top_k = old_top_k
        output = []
        for match in matches:
            item = dict(match)
            try:
                item["source"] = Path(item.get("source", "")).relative_to(self.docs_dir).as_posix()
            except ValueError:
                item["source"] = str(item.get("source", ""))
            output.append(item)
        return output


def load_evaluation_cases(path: str | Path | None) -> list[dict]:
    """读取 JSON/JSONL 评测样例，并统一 query/question 与来源字段。"""
    if path is None:
        return []
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    raw = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".jsonl":
        payload = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        decoded = json.loads(raw)
        payload = decoded if isinstance(decoded, list) else decoded.get("cases", [])
    cases = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"评测样例第 {index + 1} 项必须是对象")
        question = item.get("question", item.get("query"))
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"评测样例第 {index + 1} 项缺少 question/query")
        expected = item.get("expected_sources", item.get("expected_relative_paths", []))
        if isinstance(expected, str):
            expected = [expected]
        if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)):
            raise ValueError(f"评测样例第 {index + 1} 项 expected_sources 必须是数组")
        case = dict(item)
        case["id"] = str(item.get("id", item.get("case_id", f"case-{index + 1}")))
        case["question"] = question
        case["expected_sources"] = [str(value).replace("\\", "/") for value in expected]
        case.setdefault("split", "evaluation_only")
        cases.append(case)
    return cases


def _metric(total: int, passed: int) -> dict:
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "hit_rate": round(passed / total, 4) if total else None,
    }


def _unknown_refusal_eval(service: _OfflineService, cases: Sequence[Mapping[str, object]], top_k: int, threshold: float = 0.35) -> dict:
    results = []
    for case in cases:
        matches = service.search(str(case["question"]), top_k=top_k)
        scores = [round(float(match.get("score", 0.0)), 6) for match in matches]
        refused = not matches or max(scores, default=0.0) < threshold
        expected = str(case.get("expected_decision", "refuse_missing_evidence"))
        passed = refused if expected.startswith("refuse") else not refused
        results.append({
            "id": str(case.get("id", "")),
            "question": str(case["question"]),
            "expected_decision": expected,
            "returned_sources": [str(match.get("source", "")) for match in matches],
            "scores": scores,
            "threshold": threshold,
            "refused": refused,
            "passed": passed,
        })
    passed = sum(1 for result in results if result["passed"])
    return {**_metric(len(results), passed), "refusal_rate": round(passed / len(results), 4) if results else None, "cases": results}


def run_offline_regression(
    docs_dir: str | Path,
    cases_path: str | Path | None = None,
    report_out: str | Path | None = None,
    top_k: int = 4,
) -> dict:
    """运行可重复的离线 RAG 回归，绝不实例化或调用 Ollama 客户端。"""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k 必须是正整数")
    root = Path(docs_dir)
    manifest = build_manifest(root)
    service = _OfflineService(root, top_k=top_k)
    title_cases = build_title_cases(root)
    supplied_cases = load_evaluation_cases(cases_path)
    positive_cases = [case for case in supplied_cases if case.get("expected_sources")]
    semantic_cases = positive_cases or title_cases
    unknown_cases = [case for case in supplied_cases if not case.get("expected_sources")]
    title_eval = run_retrieval_eval(service, title_cases, top_k=top_k)
    semantic_eval = run_retrieval_eval(service, semantic_cases, top_k=top_k)
    source_total = len(title_cases) + len(positive_cases)
    source_passed = sum(item["retrieval_hit"] for item in title_eval["cases"])
    semantic_positive = run_retrieval_eval(service, positive_cases, top_k=top_k)
    source_passed += sum(item["retrieval_hit"] for item in semantic_positive["cases"])
    source_metric = round(source_passed / source_total, 4) if source_total else None
    unknown_metric = _unknown_refusal_eval(service, unknown_cases, top_k=top_k)
    report = {
        "schema": "rag_eval_regression/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_deterministic",
        "evaluation_only": True,
        "training_eligible": False,
        "production_eligible": False,
        "model_calls": 0,
        "training_steps": 0,
        "manifest": manifest,
        "metrics": {
            "title_retrieval": {**title_eval["summary"], "cases": title_eval["cases"]},
            "semantic_retrieval": {**semantic_eval["summary"], "cases": semantic_eval["cases"]},
            "source_hit_rate": source_metric,
            "unknown_refusal": unknown_metric,
        },
        "summary": {
            "title_retrieval_hit_rate": title_eval["summary"]["hit_rate"],
            "semantic_retrieval_hit_rate": semantic_eval["summary"]["hit_rate"],
            "source_hit_rate": source_metric,
            "unknown_refusal_rate": unknown_metric["refusal_rate"],
        },
    }
    if report_out is not None:
        _write_json(report_out, report)
    return report



if __name__ == "__main__":
    raise SystemExit(main())
