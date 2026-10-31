# -*- coding: utf-8 -*-
"""研报知识单元检索（Agent 工具，只读）：知识单元 + RAG 记录双通道。

用法（Agent 调用）：
  python tools/kb_corpus/kb_reports_search.py --query "盈利预测上修" [--top 8] [--json]
输出：JSON {query, n, matches:[{source,book,chapter,pages,concept,claim,evidence,score}]}
说明：纯本地、无网络、无写入；BM25-lite（词重叠 + 概念/claim 加权）。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
REPORTS = Path(r"C:/项目/PDF/_ingest_reports")
INGEST = Path(r"C:/项目/PDF/_ingest")
KB_DOCS = REPO / "knowledge_docs"


def tokenize(s: str) -> list[str]:
    s = (s or "").lower()
    zh = re.findall(r"[\u4e00-\u9fff]{2}", s)
    en = re.findall(r"[a-z0-9]{2,}", s)
    return zh + en


def iter_units():
    for root in (INGEST, REPORTS):
        if not root.exists():
            continue
        for d in sorted(p for p in root.glob("*") if p.is_dir()):
            ku = d / "knowledge_units.jsonl"
            if not ku.exists():
                continue
            for line in ku.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    u = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                yield {"source": "units", "book": u.get("book") or d.name, "chapter": u.get("chapter", ""),
                       "pages": [u.get("page_start"), u.get("page_end")],
                       "concept": u.get("concept", ""), "claim": u.get("claim", ""),
                       "evidence": u.get("evidence", "")}


def iter_rag():
    p = KB_DOCS / ".rag_index.json"
    if not p.exists():
        return
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for rec in idx.get("records") or []:
        yield {"source": "rag", "book": Path(str(rec.get("source", ""))).stem,
               "chapter": "", "pages": [],
               "concept": "", "claim": str(rec.get("text", ""))[:400],
               "evidence": str(rec.get("source", ""))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    q = tokenize(args.query)
    if not q:
        print(json.dumps({"query": args.query, "n": 0, "matches": []}, ensure_ascii=False))
        return 0
    docs = list(iter_units()) + list(iter_rag())
    if not docs:
        print(json.dumps({"query": args.query, "n": 0, "matches": [], "error": "no data"}, ensure_ascii=False))
        return 1
    df = Counter()
    for d in docs:
        df.update(set(tokenize(d["concept"]) + tokenize(d["claim"]) + tokenize(d["evidence"])))
    n = len(docs)
    out = []
    for d in docs:
        text = " ".join([d["concept"], d["claim"], d["evidence"]])
        tf = Counter(tokenize(text))
        score = 0.0
        for t in q:
            if tf.get(t):
                idf = math.log(1 + n / max(1, df.get(t, 0)))
                weight = 2.0 if t in tokenize(d["concept"]) else 1.0
                score += weight * (1 + math.log(tf[t])) * idf
        if score > 0:
            out.append({**d, "score": round(score, 3)})
    out.sort(key=lambda x: -x["score"])
    res = {"query": args.query, "n": len(out[: args.top]), "matches": out[: args.top]}
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for m in res["matches"]:
            print(f"[{m['score']}] {m['book']} | {m['chapter']} | p{m['pages']} | {m['concept']}: {m['claim'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
