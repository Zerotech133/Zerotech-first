# -*- coding: utf-8 -*-
"""语料知识库只读接口（YouTube 量化语料，portal 独立 handler 文件）。

GET /api/kb_corpus/stats        → 索引规模/更新时间
GET /api/kb_corpus/ask?q=...    → 检索 top5 + 本地 qwen3.5 生成答案（带 [S#] 引用）

数据：C:\\项目\\知识库\\50_RAG\\packed\\{docs.json, emb.f32, meta.json}
模型：Ollama bge-m3（embedding）+ qwen3.5:9b（生成，think=false）
只读：不写任何业务数据；无外部网络。
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

PACKED = Path(r"C:\项目\知识库\50_RAG\packed")
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"
CHAT_MODEL = "qwen3.5:9b"
TOP_K = 5
_LOCK = threading.RLock()
_CACHE: dict = {}


def _load() -> dict:
    with _LOCK:
        if _CACHE.get("docs"):
            return _CACHE
        docs = json.loads((PACKED / "docs.json").read_text(encoding="utf-8"))
        meta = json.loads((PACKED / "meta.json").read_text(encoding="utf-8"))
        dim = int(meta["dim"])
        rows = len(docs)
        emb = np.fromfile(str(PACKED / "emb.f32"), dtype="<f4").reshape(rows, dim)
        emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
        _CACHE.update({"docs": docs, "emb": emb, "meta": meta})
        return _CACHE


def _post(path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _embed(text: str) -> np.ndarray:
    r = _post("/api/embed", {"model": EMBED_MODEL, "input": text}, timeout=120)
    return np.array(r["embeddings"][0], dtype=np.float32)


def _retrieve(question: str, k: int = TOP_K) -> list[dict]:
    idx = _load()
    q = _embed(question)
    q = q / (np.linalg.norm(q) + 1e-9)
    scores = idx["emb"] @ q
    order = np.argsort(-scores)[:k]
    out = []
    for i in order:
        d = idx["docs"][int(i)]
        out.append({"doc_id": d["id"], "source": d["source"], "text": d["text"][:800],
                    "score": round(float(scores[int(i)]), 4)})
    return out


def _answer(question: str) -> dict:
    hits = _retrieve(question)
    ctx = []
    for n, h in enumerate(hits, 1):
        ctx.append("[S%d] source=%s\n%s" % (n, h["source"], h["text"][:600]))
    prompt = ("你是量化研究助理。仅根据下面资料回答；资料不足时明确说“资料未覆盖”。"
              "用中文，300 字内；关键结论后标注 [S1]/[S2] 来源。\n\n资料：\n"
              + "\n\n".join(ctx) + "\n\n问题：" + question + "\n回答：")
    r = _post("/api/chat", {"model": CHAT_MODEL, "stream": False, "think": False,
                            "messages": [{"role": "user", "content": prompt}],
                            "options": {"temperature": 0.2, "num_predict": 500}}, timeout=300)
    answer = ((r.get("message") or {}).get("content") or "").strip()
    return {"ok": True, "question": question, "answer": answer,
            "sources": [{"sid": "S%d" % (i + 1), "source": h["source"],
                         "score": h["score"], "text": h["text"][:300]} for i, h in enumerate(hits)]}


def handle_get(handler, path: str, qs: str) -> None:
    try:
        if path == "/api/kb_corpus/stats":
            meta = _load()["meta"]
            handler._json({"ok": True, "rows": meta.get("rows"), "dim": meta.get("dim"),
                           "generated_at": meta.get("generated_at"),
                           "index": "youtube_quant_corpus/50_RAG",
                           "embed_model": EMBED_MODEL, "chat_model": CHAT_MODEL})
            return
        if path == "/api/kb_corpus/ask":
            q = (urllib.parse.parse_qs(qs or "").get("q") or [""])[0].strip()
            if not q:
                handler._json({"ok": False, "error": "q 必填"}, 400)
                return
            handler._json(_answer(q))
            return
        handler._json({"ok": False, "error": "not found"}, 404)
    except Exception as exc:  # noqa: BLE001 — fail-visible：错误回给请求方
        handler._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 503)
