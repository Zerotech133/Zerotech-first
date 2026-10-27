# -*- coding: utf-8 -*-
"""p1_rag_observe.py — P1-2 观察期汇总（只读 grounding_usage.jsonl）。

输出：检索条数、rag_enabled 比例、来源数分布、空检索率、延迟 P50/P95/P99。
不写文件、不联网；供 2026-11-03 观察小结 / M2 决策。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_REL = Path("work") / "p1" / "rag" / "grounding_usage.jsonl"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    rag = sum(1 for r in rows if r.get("rag_enabled"))
    source_counts = [int(r.get("source_count") or 0) for r in rows]
    empty = sum(1 for c in source_counts if c == 0)
    lat = [float(r.get("elapsed_ms") or 0) for r in rows]
    return {
        "total": total,
        "rag_enabled": rag,
        "rag_enabled_rate": round(rag / total, 4) if total else 0,
        "empty_retrieval": empty,
        "empty_rate": round(empty / total, 4) if total else 0,
        "source_count_avg": round(sum(source_counts) / total, 2) if total else 0,
        "source_count_histogram": {str(c): source_counts.count(c) for c in sorted(set(source_counts))},
        "latency_ms": {
            "p50": _percentile(lat, 50),
            "p95": _percentile(lat, 95),
            "p99": _percentile(lat, 99),
            "max": round(max(lat), 1) if lat else 0,
        },
    }


def load(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else (REPO_ROOT / LOG_REL)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1-2 RAG usage observe")
    ap.add_argument("--log", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.log) if args.log else (REPO_ROOT / LOG_REL)
    rows = load(path)
    out = {"log": str(path), "exists": path.is_file(), **summarize(rows)}
    print(json.dumps(out, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
