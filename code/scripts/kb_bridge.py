# -*- coding: utf-8 -*-
"""张松知识库桥接：只读检索 `C:\项目\知识库`，输出可追溯引用。

用法：
  python scripts/kb_bridge.py "均值回归" -k 5
  python scripts/kb_bridge.py "walk-forward" --chunks -k 8 --json
"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

KB = Path(r"C:\项目\知识库")
LITE = KB / "40_索引" / "kb_lite_index.json"
CORPUS = KB / "50_RAG" / "rag_index_corpus.json"


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    words |= {text[i:i + 2] for i in range(max(0, len(text) - 1)) if "\u4e00" <= text[i] <= "\u9fff"}
    return words


@lru_cache(maxsize=1)
def _load_docs() -> list[dict]:
    if not LITE.exists():
        return []
    return json.loads(LITE.read_text(encoding="utf-8")).get("docs", [])


@lru_cache(maxsize=1)
def _load_chunks() -> list[dict]:
    if not CORPUS.exists():
        return []
    return json.loads(CORPUS.read_text(encoding="utf-8")).get("records", [])


def search_docs(query: str, k: int = 5) -> list[dict]:
    q = _tokens(query)
    scored = []
    for d in _load_docs():
        text = (d.get("title") or "") + "\n" + (d.get("text") or "")
        t = _tokens(text)
        score = len(q & t) / max(1, len(q))
        if score <= 0:
            continue
        scored.append({
            "score": round(score, 4),
            "title": d.get("title") or "",
            "video_id": d.get("video_id") or "",
            "channel_id": d.get("channel_id") or "",
            "upload_date": d.get("upload_date") or "",
            "kind": d.get("kind") or "",
            "snippet": (d.get("text") or "")[:500],
            "source": str(LITE),
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def search_chunks(query: str, k: int = 8) -> list[dict]:
    q = _tokens(query)
    scored = []
    for r in _load_chunks():
        text = r.get("text") or ""
        t = _tokens(text)
        score = len(q & t) / max(1, len(q))
        if score <= 0:
            continue
        scored.append({
            "score": round(score, 4),
            "source": r.get("source") or "",
            "document_hash": r.get("document_hash") or "",
            "snippet": text[:600],
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--chunks", action="store_true", help="同时检索 RAG 语料块")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = {"query": args.query, "docs": search_docs(args.query, args.k),
           "chunks": search_chunks(args.query, args.k) if args.chunks else []}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print("查询：%s\n" % args.query)
    for i, h in enumerate(out["docs"], 1):
        print("%d. [%.3f] %s (%s) %s" % (i, h["score"], h["title"][:70],
                                          h["video_id"], h["upload_date"]))
        print("   %s\n" % h["snippet"].replace("\n", " ")[:180])
    if out["chunks"]:
        print("--- RAG chunks ---")
        for i, h in enumerate(out["chunks"], 1):
            print("%d. [%.3f] %s" % (i, h["score"], Path(h["source"]).name))
            print("   %s\n" % h["snippet"].replace("\n", " ")[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
