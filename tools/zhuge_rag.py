# -*- coding: utf-8 -*-
"""zhuge_rag.py — P1-2 M1：诸葛亮 BM25 v0 检索（纯 stdlib，零依赖）。

- 语料：docs/**/*.md（先只读 docs；deliverables/任务后续再加）
- 切块：Markdown 标题上下文 + 12 行/块；保留 path/line/title，供 [S#] 引用
- 索引：BM25（k1=1.5,b=0.75），索引 JSON 可重建（work/p1/rag/index.json，不入库）
- 开关：zhuge_grounding 仅当 ZHUGE_RAG=1 时用本模块；默认仍走关键词（可回退）

用法：
  python tools/zhuge_rag.py --build
  python tools/zhuge_rag.py --query "诸葛亮当前载体是什么？" --k 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_REL = Path("work") / "p1" / "rag" / "index.json"
DIRS = ("docs",)
CHUNK_LINES = 12
K1 = 1.5
B = 0.75
MAX_CHUNKS = 60_000
_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_ALNUM = re.compile(r"[a-z0-9_./-]{2,}")
# P1-2 同义词扩展（只扩检索词，不改答案；F04 等「请求体/字段」类问法用）
SYNONYMS = {
    "请求体": ["binding", "user", "system", "llm/chat"],
    "字段": ["binding", "user", "system"],
    "载体": ["openclaw", "deepseek", "relay"],
    "质询": ["challenge", "cards.jsonl"],
    "网关": ["gateway", "8795"],
    "沙盘": ["plans", "snapshot", "快照"],
}


def expand_query(query: str) -> str:
    extra = []
    for key, values in SYNONYMS.items():
        if key in (query or ""):
            extra.extend(values)
    return query if not extra else (query + " " + " ".join(dict.fromkeys(extra)))


def _root() -> Path:
    override = os.environ.get("ZHUGE_RAG_REPO")
    return Path(override).resolve() if override else REPO_ROOT


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def tokenize(text: str) -> list[str]:
    low = (text or "").lower()
    terms = _ALNUM.findall(low)
    for run in _CJK.findall(text or ""):
        for n in (2, 3):
            for i in range(0, max(0, len(run) - n + 1)):
                terms.append(run[i:i + n])
    return terms


def _heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:120]
    return fallback


def iter_chunks(root: Path | None = None, dirs=DIRS) -> list[dict]:
    base = Path(root or _root())
    chunks: list[dict] = []
    for rel_dir in dirs:
        base_dir = base / rel_dir
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            title = _heading(text, path.stem)
            for start in range(0, len(lines), CHUNK_LINES):
                block = "\n".join(lines[start:start + CHUNK_LINES]).strip()
                if len(block) < 20:
                    continue
                chunks.append({
                    "id": str(path.relative_to(base)).replace("\\", "/") + "#%d" % (start + 1),
                    "path": str(path.relative_to(base)).replace("\\", "/"),
                    "line": start + 1,
                    "title": _heading(block, title),
                    "text": block[:1500],
                })
                if len(chunks) >= MAX_CHUNKS:
                    return chunks
    return chunks


def build_index(root: Path | None = None, out: Path | None = None,
                dirs=DIRS) -> dict:
    chunks = iter_chunks(root, dirs)
    for chunk in chunks:
        chunk["tokens"] = tokenize(chunk["title"] + " " + chunk["text"])
    df: dict[str, int] = {}
    total_len = 0
    for chunk in chunks:
        total_len += len(chunk["tokens"])
        for term in set(chunk["tokens"]):
            df[term] = df.get(term, 0) + 1
    postings: dict = {}
    lens: list[int] = []
    for ci, chunk in enumerate(chunks):
        toks = chunk.get("tokens") or []
        lens.append(len(toks))
        tf: dict = {}
        for term in toks:
            tf[term] = tf.get(term, 0) + 1
        for term, f in tf.items():
            postings.setdefault(term, []).append([ci, f])
    index = {"built_at": _now(), "chunk_count": len(chunks),
             "avg_len": (total_len / len(chunks)) if chunks else 0,
             "df": df, "chunks": chunks, "dirs": list(dirs),
             "postings": postings, "lens": lens}
    # 2026-10-27 索引瘦身：postings/lens 已持久化，chunks.tokens 不再写盘（可大幅降低加载延迟）
    for chunk in chunks:
        chunk.pop("tokens", None)
    index["tokens_omitted"] = True
    out_path = Path(out) if out else (_root() / INDEX_REL)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, out_path)
    return {"ok": True, "chunks": len(chunks), "index": str(out_path),
            "built_at": index["built_at"]}


_INDEX_CACHE: dict = {"key": None, "index": None}


_POSTINGS: dict = {"key": None, "postings": None, "lens": None}


def _postings_for(index: dict):
    """(term -> [(chunk_idx, tf)]) 倒排 + 长度表；索引内已持久化则直接用。"""
    if isinstance(index.get("postings"), dict) and isinstance(index.get("lens"), list):
        return index["postings"], index["lens"]
    key = (id(index), len(index.get("chunks") or []), index.get("built_at"))
    if _POSTINGS.get("key") == key:
        return _POSTINGS["postings"], _POSTINGS["lens"]
    postings: dict = {}
    lens: list[int] = []
    for ci, chunk in enumerate(index.get("chunks") or []):
        toks = chunk.get("tokens") or []
        lens.append(len(toks))
        tf: dict = {}
        for term in toks:
            tf[term] = tf.get(term, 0) + 1
        for term, f in tf.items():
            postings.setdefault(term, []).append((ci, f))
    _POSTINGS.update(key=key, postings=postings, lens=lens)
    return postings, lens


def ensure_fresh(root: Path | None = None) -> dict:
    """索引不存在或落后于 docs/ 最新 mtime 时重建；返回状态（不抛）。"""
    base = Path(root or _root())
    idx = base / INDEX_REL
    docs = base / "docs"
    try:
        latest = max((p.stat().st_mtime for p in docs.rglob("*.md")), default=0.0)
    except OSError:
        latest = 0.0
    if idx.is_file():
        try:
            if idx.stat().st_mtime >= latest:
                return {"ok": True, "rebuilt": False, "index": str(idx)}
        except OSError:
            pass
    try:
        built = build_index(base, out=idx)
        return {"ok": True, "rebuilt": True, "index": built.get("index"),
                "chunks": built.get("chunks")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rebuilt": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def _load_index(root: Path | None = None, index: dict | None = None) -> dict:
    global _INDEX_CACHE
    if index is not None:
        return index
    path = Path(root or _root()) / INDEX_REL
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0
    except OSError:
        mtime = 0
    key = (str(path), mtime)
    if _INDEX_CACHE.get("key") == key and _INDEX_CACHE.get("index") is not None:
        return _INDEX_CACHE["index"]
    data = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None:
        built = build_index(root)
        try:
            data = json.loads(Path(built["index"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"chunks": [], "df": {}, "avg_len": 1.0}
    _INDEX_CACHE = {"key": key, "index": data}
    return data


def _bm25(query: str, index: dict, k: int) -> list[dict]:
    q_terms = tokenize(query)
    if not q_terms:
        return []
    chunks = index.get("chunks") or []
    df = index.get("df") or {}
    avg_len = index.get("avg_len") or 1.0
    n = len(chunks)
    q_unique = list(dict.fromkeys(q_terms))
    postings, lens = _postings_for(index)
    scores: dict[int, float] = {}
    for term in q_unique:
        idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        for ci, freq in postings.get(term, []):
            dl = lens[ci] or 1
            scores[ci] = scores.get(ci, 0.0) + idf * (freq * (K1 + 1)) / (
                freq + K1 * (1 - B + B * dl / avg_len))
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out = []
    for i, (ci, score) in enumerate(ranked[:max(1, k)], 1):
        chunk = chunks[ci]
        out.append({"id": "S%d" % i, "path": chunk["path"], "line": chunk["line"],
                    "title": chunk.get("title") or "", "snippet": chunk["text"][:240],
                    "score": round(score, 4)})
    return out


def search(query: str, k: int = 3, root: Path | None = None,
           index: dict | None = None) -> list[dict]:
    index = _load_index(root, index)
    return _bm25(expand_query(query), index, k)


def check(root: Path | None = None) -> dict:
    path = Path(root or _root()) / INDEX_REL
    if not path.is_file():
        return {"ok": True, "exists": False, "stale": True}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}
    built_at = index.get("built_at", "")
    return {"ok": True, "exists": True, "built_at": built_at,
            "chunk_count": index.get("chunk_count", 0), "path": str(path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="zhuge BM25 RAG v0")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query", default="")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.build:
        out = build_index()
    elif args.query:
        out = {"query": args.query, "sources": search(args.query, args.k)}
    else:
        out = check()
    print(json.dumps(out, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
