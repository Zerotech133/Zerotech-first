from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[3]
ACADEMY = ROOT / "work" / "agent-academy-prototype"
OUT = ACADEMY / "rag-baseline-v0.1"


@dataclass(frozen=True)
class Chunk:
    source_id: str
    path: str
    ordinal: int
    text: str


SOURCE_FILES = (
    ("WB-LEARNING-SUMMARY", ACADEMY / "WorkBuddy三份资料学习整理v1.md"),
    ("WB-RES-001", OUT / "evidence" / "WB-RES-001_券商研究员研究方法论_AI化.md"),
    ("ACADEMY-INTEGRATED-V2", ACADEMY / "学院一体化课程与训练架构v2.md"),
    ("ACADEMY-TRAINING-V1", ACADEMY / "学院课程与可蒸馏训练总体方案v1.md"),
    ("ACADEMY-SOURCE-MANIFEST", ACADEMY / "academy-source-manifest-v1.json"),
)

SOURCE_CHUNK_LIMITS = {"WB-RES-001": 1200}


EVAL_CASES = [
    {
        "case_id": "CORE-101-E01",
        "course_id": "CORE-101",
        "query": "缺证据或者OCR不清晰时应该怎么处理",
        "expected_source_id": "WB-LEARNING-SUMMARY",
        "required_terms": ["OCR 存疑", "拒绝"],
        "split": "evaluation_only",
    },
    {
        "case_id": "CORE-102-E01",
        "course_id": "CORE-102",
        "query": "如何判断资料在决策时点是否可用并处理版本变化",
        "expected_source_id": "ACADEMY-TRAINING-V1",
        "required_terms": ["时间", "版本"],
        "split": "evaluation_only",
    },
    {
        "case_id": "RES-201-E01",
        "course_id": "RES-201",
        "query": "研究员方法论的六步流水线是什么",
        "expected_source_id": "WB-LEARNING-SUMMARY",
        "expected_source_ids": ["WB-LEARNING-SUMMARY", "WB-RES-001"],
        "required_term_groups": [["六个模块", "六步"], ["选题"], ["采集"], ["输出验证", "输出与验证"]],
        "split": "evaluation_only",
    },
    {
        "case_id": "TOOL-301-E01",
        "course_id": "TOOL-301",
        "query": "sph_research如何执行validate baseline和export-quant",
        "expected_source_id": "WB-LEARNING-SUMMARY",
        "expected_source_ids": ["WB-LEARNING-SUMMARY", "ACADEMY-TRAINING-V1"],
        "required_terms": ["validate", "baseline", "export-quant"],
        "split": "evaluation_only",
    },
    {
        "case_id": "TOOL-301-E02",
        "course_id": "TOOL-301",
        "query": "沙箱行情快照为空时chain_heat应该怎么处理",
        "expected_source_id": "WB-LEARNING-SUMMARY",
        "required_terms": ["行情快照", "chain_heat", "重跑"],
        "split": "evaluation_only",
    },
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tokenize(text: str) -> set[str]:
    """Deterministic mixed Chinese/Latin tokens for a small read-only baseline."""
    terms: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9_./-]+", text.lower()):
        terms.add(word)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run)
        terms.update(run[i : i + 2] for i in range(len(run) - 1))
        terms.update(run[i : i + 3] for i in range(len(run) - 2))
    return {term for term in terms if term}


def split_chunks(text: str, max_chars: int = 900) -> Iterable[str]:
    # 先按 Markdown 标题保留证据段边界，再按段落限长；避免 RES-201 的六步表格被
    # 无标题的滑动窗口拆散，导致模型只能引用二手摘要而找不到原始证据。
    sections = [p.strip() for p in re.split(r"(?m)(?=^#{1,6}\s)", text) if p.strip()]
    parts = []
    for section in sections:
        parts.extend(p.strip() for p in re.split(r"\n\s*\n", section) if p.strip())
    current = ""
    for part in parts:
        if current and len(current) + len(part) + 2 > max_chars:
            yield current
            current = ""
        current = f"{current}\n\n{part}".strip()
    if current:
        yield current


def load_chunks() -> tuple[list[Chunk], list[dict]]:
    chunks: list[Chunk] = []
    snapshots: list[dict] = []
    for source_id, path in SOURCE_FILES:
        if not path.exists():
            raise FileNotFoundError(path)
        raw = path.read_text(encoding="utf-8")
        snapshots.append(
            {
                "source_id": source_id,
                "path": str(path),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
                "source_class": "internal_reviewed_summary",
                "raw_source_available": False,
            }
        )
        chunk_limit = SOURCE_CHUNK_LIMITS.get(source_id, 900)
        for ordinal, text in enumerate(split_chunks(raw, max_chars=chunk_limit), start=1):
            chunks.append(Chunk(source_id, str(path), ordinal, text))
    return chunks, snapshots


def score(query: str, text: str) -> float:
    q = tokenize(query)
    t = tokenize(text)
    if not q or not t:
        return 0.0
    overlap = len(q & t)
    return overlap / (len(q) ** 0.5 * len(t) ** 0.5)


def required_terms_match(text: str, groups: list[list[str]]) -> bool:
    """每组命中一个同义词，避免证据分块因表述差异被误判。"""
    return all(any(term in text for term in group) for group in groups)


def retrieve(chunks: list[Chunk], query: str, top_k: int = 5) -> list[dict]:
    ranked = rank_chunks(chunks, query)
    return [
        {
            "rank": rank,
            "score": round(item_score, 6),
            "source_id": chunk.source_id,
            "chunk": chunk.ordinal,
            "path": chunk.path,
            "text_preview": chunk.text[:360].replace("\n", " "),
            "context_text": chunk.text[:1600],
        }
        for rank, (item_score, chunk) in enumerate(ranked[:top_k], start=1)
    ]


def rank_chunks(chunks: list[Chunk], query: str) -> list[tuple[float, Chunk]]:
    return sorted(
        ((score(query, chunk.text), chunk) for chunk in chunks),
        key=lambda pair: (-pair[0], pair[1].source_id, pair[1].ordinal),
    )


def evaluate(chunks: list[Chunk]) -> dict:
    results = []
    for case in EVAL_CASES:
        ranked = rank_chunks(chunks, case["query"])
        hits = retrieve(chunks, case["query"])
        top_chunks = [chunk for _, chunk in ranked[:5]]
        top_text = " ".join(chunk.text for chunk in top_chunks)
        expected_sources = case.get("expected_source_ids", [case["expected_source_id"]])
        source_hit = any(chunk.source_id in expected_sources for chunk in top_chunks)
        groups = case.get("required_term_groups")
        if groups is None:
            groups = [[term] for term in case["required_terms"]]
        term_hits = [next((term for term in group if term in top_text), None) for group in groups]
        term_hits = [term for term in term_hits if term is not None]
        passed = source_hit and required_terms_match(top_text, groups)
        results.append(
            {
                **case,
                "expected_source_ids": expected_sources,
                "top_k": hits,
                "source_hit": source_hit,
                "term_hits": term_hits,
                "passed": passed,
                "model_calls": 0,
                "training_steps": 0,
            }
        )
    passed = sum(1 for result in results if result["passed"])
    return {
        "schema": "academy_rag_baseline/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "lexical_read_only",
        "production_eligible": False,
        "training_eligible": False,
        "model_calls": 0,
        "training_steps": 0,
        "metric_definition": {
            "retrieval_source_recall_at_5": "expected source appears in top 5",
            "required_term_coverage": "all required terms appear in retrieved previews",
        },
        "cases": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "source_recall_at_5": round(passed / len(results), 4) if results else None,
            "all_terms_covered": passed == len(results),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic read-only Academy RAG baseline")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    chunks, snapshots = load_chunks()
    report = evaluate(chunks)
    (args.out / "source-snapshot.json").write_text(
        json.dumps(
            {
                "schema": "academy_source_snapshot/v1",
                "mode": "read_only",
                "sources": snapshots,
                "chunk_count": len(chunks),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.out / "retrieval-baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "evaluation-cases.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in EVAL_CASES),
        encoding="utf-8",
    )
    print(json.dumps({"chunks": len(chunks), **report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

