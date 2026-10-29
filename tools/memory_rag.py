# -*- coding: utf-8 -*-
"""memory-rag 最小版：本地 hash-vector 索引 + 判例/文档召回（无外部 Key）。

- 索引来源：deliverables/ docs/ work/seat_brief/（可 --roots 覆盖）
- 向量：中文单字+双字/英文词 → md5 hash 到 512 维，TF-IDF + 余弦
- 增量：按文件 mtime+size 缓存，只重建变更文件
用法：
  python -X utf8 tools/memory_rag.py index [--roots deliverables docs]
  python -X utf8 tools/memory_rag.py search "L2 审批" [--top 5] [--json]
  python -X utf8 tools/memory_rag.py stats [--json]
索引：work/memory_rag/index.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
INDEX = REPO / "work" / "memory_rag" / "index.json"
ROOTS = ("deliverables", "docs", "work/seat_brief")
DIM = 512
MAX_CHUNK = 900
MAX_FILE = 500_000
EXTS = (".md", ".txt", ".json")
EXCLUDE = {".git", ".worktrees", "node_modules", "__pycache__", "work", ".trae"}


def _tokenize(text: str) -> list:
    text = str(text or "").lower()
    toks = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        for i in range(len(run) - 1):
            toks.append(run[i:i + 2])
    return toks


def _vec(tokens: list, idf: dict) -> list:
    v = [0.0] * DIM
    for t in tokens:
        idx = int(hashlib.md5(t.encode(), usedforsecurity=False).hexdigest(), 16) % DIM
        v[idx] += float(idf.get(t, 1.0))
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _cos(a: list, b: list) -> float:
    return float(sum(x * y for x, y in zip(a, b, strict=False)))


def _files(roots):
    seen = set()
    for root in roots:
        p = Path(root)
        if not p.is_absolute():
            p = REPO / p
        if not p.exists():
            continue
        for f in sorted(p.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in EXTS:
                continue
            inside_repo = False
            try:
                rel = f.relative_to(REPO)
                inside_repo = True
            except ValueError:
                rel = f
            if inside_repo and any(part in EXCLUDE for part in Path(rel).parts):
                continue
            try:
                if f.stat().st_size > MAX_FILE:
                    continue
            except OSError:
                continue
            if f not in seen:
                seen.add(f)
                yield f, f.relative_to(REPO).as_posix() if str(f).startswith(str(REPO)) else f.as_posix()


def _chunks(text: str):
    lines = text.splitlines()
    buf, heading = [], ""
    out = []
    for ln in lines:
        if ln.startswith("#"):
            if buf and "".join(buf).strip():
                out.append((heading, "\n".join(buf).strip()))
            buf, heading = [], ln.strip("# ").strip()
            continue
        buf.append(ln)
        if len("\n".join(buf)) >= MAX_CHUNK:
            out.append((heading, "\n".join(buf).strip()))
            buf = []
    if buf and "".join(buf).strip():
        out.append((heading, "\n".join(buf).strip()))
    return [(h, t) for h, t in out if t]


def index(roots=None, index_path: Path | None = None) -> dict:
    roots = list(roots) if roots else list(ROOTS)
    p = Path(index_path) if index_path else INDEX
    old = {}
    try:
        old = json.loads(p.read_text(encoding="utf-8")).get("files", {})
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/memory_rag.py", exc)
    t0 = time.time()
    files, chunks = {}, []
    for f, rel in _files(roots):
        try:
            st = f.stat()
            sig = [st.st_mtime_ns, st.st_size]
        except OSError:
            continue
        hit = old.get(rel)
        if hit and hit.get("sig") == sig:
            new_chunks = hit.get("chunks", [])
        else:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            new_chunks = [{"heading": h, "text": t} for h, t in _chunks(text)]
        files[rel] = {"sig": sig, "chunks": new_chunks}
        for i, c in enumerate(new_chunks):
            chunks.append({"path": rel, "i": i, "heading": c["heading"], "text": c["text"]})
    df = {}
    for c in chunks:
        for t in set(_tokenize(c["text"])):
            df[t] = df.get(t, 0) + 1
    n = max(1, len(chunks))
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    for c in chunks:
        c["vec"] = _vec(_tokenize(c["text"]), idf)
    data = {"schema": "memory_rag/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "roots": roots, "files": files, "chunks": chunks, "idf": idf,
            "dim": DIM, "elapsed_ms": int((time.time() - t0) * 1000)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "files": len(files), "chunks": len(chunks),
            "elapsed_ms": data["elapsed_ms"], "path": str(p)}


def search(query: str, top: int = 5, index_path: Path | None = None) -> dict:
    p = Path(index_path) if index_path else INDEX
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "index_missing: %s" % e}
    idf = data.get("idf") or {}
    qv = _vec(_tokenize(query), idf)
    hits = []
    for c in data.get("chunks") or []:
        score = _cos(qv, c.get("vec") or [0.0] * DIM)
        if score > 0:
            hits.append({"score": round(score, 4), "path": c["path"],
                         "heading": c.get("heading", ""), "text": c["text"][:500]})
    hits.sort(key=lambda x: -x["score"])
    return {"ok": True, "query": query, "total": len(hits), "hits": hits[:max(1, top)]}


def stats(index_path: Path | None = None) -> dict:
    p = Path(index_path) if index_path else INDEX
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"ok": True, "files": len(data.get("files") or {}),
                "chunks": len(data.get("chunks") or []),
                "at": data.get("at"), "path": str(p)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "path": str(p)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 memory-rag（本地 hash-vector 检索）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index")
    i.add_argument("--roots", nargs="*", default=None)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--json", action="store_true")
    st = sub.add_parser("stats")
    st.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "index":
        print(json.dumps(index(args.roots), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "search":
        r = search(args.query, args.top)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            for h in r.get("hits", []):
                print("%.4f %s # %s" % (h["score"], h["path"], h["heading"]))
                print("  " + h["text"][:180].replace("\n", " "))
        return 0
    print(json.dumps(stats(), ensure_ascii=False, indent=1) if args.json else
          json.dumps(stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
