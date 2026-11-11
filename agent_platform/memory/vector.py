# -*- coding: utf-8 -*-
"""记忆 v2 向量升级（可选）：fastembed 语义重排 + sqlite-vec 索引，失败自动降级。

设计：
  · **可选依赖**：fastembed / sqlite-vec 未安装、模型下载失败 → available()=False，
    store.search 自动退回 FTS5+bigram，不影响主链路；
  · 默认模型 `BAAI/bge-small-zh-v1.5`（中文友好），可用 ZHAOYUN_EMBED_MODEL 覆盖；
  · 环境开关：ZHAOYUN_VECTOR=on 启用混合检索（默认 off，避免冷启动下载卡页面）；
  · 提供 index()/knn()/eval_hits() 与 CLI；不写任何真源，仅内存库向量表。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
import logging

PLATFORM = Path(__file__).resolve().parents[1]
def _main_repo() -> Path:
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-C", str(PLATFORM), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (PLATFORM / common).resolve()
            return common.parent
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/memory/vector.py", exc)
    return PLATFORM.parent


REPO = _main_repo()
DB = REPO / "agent_platform" / "data" / "memory" / "memory.sqlite"
EVAL_PATH = PLATFORM / "memory" / "eval_queries.json"
MODEL_NAME = os.environ.get("ZHAOYUN_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
_MODEL = None
_STATUS = {"checked": False, "ok": False, "reason": ""}


class VectorUnavailable(RuntimeError):
    pass


def available(force: bool = False) -> bool:
    if _STATUS["checked"] and not force:
        return _STATUS["ok"]
    reason = ""
    try:
        import fastembed  # noqa: F401
        from fastembed import TextEmbedding  # noqa: F401
    except Exception as e:  # noqa: BLE001
        reason = "fastembed: %s" % e
    try:
        import sqlite_vec  # noqa: F401
    except Exception as e:  # noqa: BLE001
        reason = (reason + " | " if reason else "") + "sqlite_vec: %s" % e
    _STATUS.update(checked=True, ok=not reason, reason=reason)
    return _STATUS["ok"]


def _model():
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding(model_name=MODEL_NAME)
    return _MODEL


def embed(texts: list[str]) -> list[list[float]]:
    if not available():
        raise VectorUnavailable(_STATUS["reason"])
    return [list(map(float, v)) for v in _model().embed([str(t) for t in texts])]


def hash_embed(texts: list[str], dim: int = 256) -> list[list[float]]:
    """纯 Python 轻量向量：字符 1~3-gram 哈希到 dim 桶 + L2 归一（无依赖）。

    语义能力弱于 fastembed，但零依赖、毫秒级；用于 8796(py3.13) 等装不上
    fastembed 的环境做"轻量向量"兜底。
    """
    import math
    import zlib
    out = []
    for text in texts:
        s = str(text or "").lower()
        vec = [0.0] * dim
        n = len(s)
        for i in range(n):
            for k in (1, 2, 3):
                if i + k <= n:
                    gram = s[i:i + k]
                    vec[zlib.crc32(gram.encode("utf-8")) % dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / norm for x in vec])
    return out


def embed_any(texts: list[str]) -> tuple[list[list[float]], str]:
    """优先 fastembed（若可用），否则纯 Python 哈希向量。"""
    if available():
        try:
            return embed(texts), "fastembed:" + MODEL_NAME
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/memory/vector.py", exc)
    return hash_embed(texts), "hash-ngram-256"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def rerank(query: str, items: list, limit: int | None = None) -> list:
    """对候选做语义重排；失败抛 VectorUnavailable（调用方降级）。"""
    if not items:
        return []
    vecs, engine = embed_any(
        [query] + [str(it.get("cue", "")) + " " + str(it.get("content", ""))
                   for it in items])
    q, docs = vecs[0], vecs[1:]
    out = []
    for it, dv in zip(items, docs):
        row = dict(it)
        row["vector_score"] = round(cosine(q, dv), 4)
        row["vector_engine"] = engine
        out.append(row)
    out.sort(key=lambda x: -x["vector_score"])
    return out[:limit] if limit else out


def _serialize(vec: list[float]) -> bytes:
    import sqlite_vec
    return sqlite_vec.serialize_float32(vec)


def _connect_plain(db: Path | None = None):
    """纯 sqlite 连接（不加载 vec 扩展；单测可注入）。"""
    p = Path(db) if db else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _connect(db: Path | None = None):
    import sqlite_vec
    p = Path(db) if db else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.enable_load_extension(True)
    conn.row_factory = sqlite3.Row
    sqlite_vec.load(conn)
    return conn


def index(db: Path | None = None) -> dict:
    """把 memories 全量写入 sqlite-vec 虚拟表（幂等重建）。"""
    if not available():
        return {"ok": False, "error": "vector_unavailable", "detail": _STATUS["reason"]}
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT id, cue, content FROM memories").fetchall()
        if not rows:
            return {"ok": True, "indexed": 0, "note": "memories 为空"}
        vecs = embed([str(r["cue"]) + " " + str(r["content"]) for r in rows])
        dim = len(vecs[0])
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0("
                     "memory_id TEXT PRIMARY KEY, embedding float[%d])" % dim)
        for r, v in zip(rows, vecs):
            conn.execute("INSERT INTO vec_memories(memory_id, embedding) VALUES(?, ?)",
                         (r["id"], _serialize(v)))
        conn.commit()
        return {"ok": True, "indexed": len(rows), "dim": dim,
                "model": MODEL_NAME, "db": str(db or DB)}
    finally:
        conn.close()


def knn(query: str, k: int = 10, db: Path | None = None) -> dict:
    if not available():
        return {"ok": False, "error": "vector_unavailable"}
    try:
        qv = _serialize(embed([query])[0])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "embed_failed", "detail": str(e)[:160]}
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT memory_id, distance FROM vec_memories "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (qv, max(1, min(int(k), 50)))).fetchall()
        return {"ok": True, "query": query, "count": len(rows),
                "items": [{"memory_id": r["memory_id"],
                           "distance": round(float(r["distance"]), 4)} for r in rows]}
    except sqlite3.Error as e:
        return {"ok": False, "error": "vec_query_failed", "detail": str(e)[:160]}
    finally:
        conn.close()


def eval_hits(db: Path | None = None, limit: int = 3) -> dict:
    """用 embedding 对 golden 查询排序，输出 hit@1/hit@3/MRR（与 FTS 版本对比）。"""
    if not available():
        return {"ok": False, "error": "vector_unavailable", "detail": _STATUS["reason"]}
    try:
        data = json.loads(EVAL_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "queries_unreadable", "detail": str(e)[:120]}
    conn = _connect_plain(db)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, cue, content FROM memories").fetchall()]
    finally:
        conn.close()
    if not rows:
        return {"ok": True, "total": 0, "hit@1": 0, "hit@3": 0, "mrr": 0.0}
    docs, engine = embed_any(
        [str(r["cue"]) + " " + str(r["content"]) for r in rows])
    queries = [str(q.get("query") or "") for q in data.get("queries") or []]
    qvecs, _ = embed_any(queries)
    hit1 = hit3 = 0
    rr = 0.0
    details = []
    for q, expected_any, qv in zip(
            queries, [q.get("expected_any") or [] for q in data.get("queries") or []],
            qvecs):
        ranked = sorted(((cosine(qv, dv), i) for i, dv in enumerate(docs)),
                        key=lambda x: -x[0])
        rank = 0
        for pos, (_score, idx) in enumerate(ranked[:limit], 1):
            hay = rows[idx]["cue"] + " " + rows[idx]["content"]
            if any(str(e) in hay for e in expected_any):
                rank = pos
                break
        if rank == 1:
            hit1 += 1
        if rank:
            hit3 += 1
        rr += (1.0 / rank) if rank else 0.0
        details.append({"query": q, "rank": rank})
    total = len(queries)
    return {"ok": True, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": engine, "total": total, "hit@1": hit1, "hit@3": hit3,
            "hit1_rate": round(hit1 / total, 3) if total else 0,
            "hit3_rate": round(hit3 / total, 3) if total else 0,
            "mrr": round(rr / total, 3) if total else 0, "details": details}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="记忆向量升级")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--search", default="")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args(argv)
    if args.index:
        rep = index()
    elif args.search:
        rep = knn(args.search, args.k)
    elif args.eval:
        rep = eval_hits()
    else:
        rep = {"ok": True, "available": available(),
               "model": MODEL_NAME, "status": dict(_STATUS)}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
