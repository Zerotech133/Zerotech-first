# -*- coding: utf-8 -*-
"""知识库只读接口 · 参考实现（CLI/JSON，供 Agent 消费）。

契约见 docs/standards/2026-11-01_知识库只读接口规范_v1.md 与 agent_platform/contracts/kb_read_api.v1.yaml。
所有输出为统一信封：
  {"api_version":"1.0","request_id":"...","ok":true,"data":{...},"error":null}
  {"api_version":"1.0","request_id":"...","ok":false,"data":null,"error":{"code":"...","message":"..."}}

子命令：
  status                    知识库状态（文档/单元/RAG/抽检）
  books                     书目清单
  units --book --chapter    知识单元列表（分页）
  search --q --top          检索（返回带出处命中）
  get-unit --id             按稳定 id 取单条
  briefing                  今日研报简报
  schema                    返回本接口的 JSON Schema（自描述）

只读、本地、无网络、无写入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
ROOTS = [(Path(r"C:/项目/PDF/_ingest"), "书籍"), (Path(r"C:/项目/PDF/_ingest_reports"), "研报")]
STATIC = REPO / "portal" / "static"
API_VERSION = "1.0"


def envelope(data, error=None) -> dict:
    return {"api_version": API_VERSION, "request_id": uuid.uuid4().hex[:16],
            "ok": error is None, "data": data, "error": error,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def err(code: str, msg: str) -> dict:
    return {"code": code, "message": msg}


def unit_id(u: dict) -> str:
    raw = f"{u.get('book')}|{u.get('chapter')}|{u.get('page_start')}|{u.get('claim','')[:80]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def load_units() -> list[dict]:
    out = []
    for root, label in ROOTS:
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
                if not u.get("claim"):
                    continue
                item = {"source": label, "book": u.get("book") or d.name,
                        "chapter": u.get("chapter", ""), "page_start": u.get("page_start"),
                        "page_end": u.get("page_end"), "concept": u.get("concept", ""),
                        "claim": u.get("claim", ""), "evidence": u.get("evidence", "")}
                item["id"] = unit_id(item)
                out.append(item)
    return out


def tokenize(s: str) -> list[str]:
    s = (s or "").lower()
    return re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s)


def cmd_status(_):
    import json as _json
    docs = units = 0
    for root, _label in ROOTS:
        if not root.exists():
            continue
        for d in sorted(p for p in root.glob("*") if p.is_dir()):
            if (d / "structure.json").exists():
                docs += 1
            ku = d / "knowledge_units.jsonl"
            if ku.exists():
                units += sum(1 for ln in ku.read_text(encoding="utf-8").splitlines() if ln.strip())
    idx = {}
    p = REPO / "knowledge_docs" / ".rag_index.json"
    if p.exists():
        try:
            j = _json.loads(p.read_text(encoding="utf-8"))
            idx = {"documents": len({(r.get("source") or "") for r in (j.get("records") or [])}),
                   "records": len(j.get("records") or []), "version": j.get("index_version") or j.get("version")}
        except Exception:  # noqa: BLE001
            idx = {}
    spot = {}
    sp = Path(r"C:/项目/PDF/_ingest_reports/golden_spotcheck.json")
    if sp.exists():
        try:
            spot = _json.loads(sp.read_text(encoding="utf-8")).get("totals") or {}
        except Exception:  # noqa: BLE001
            spot = {}
    return envelope({"docs": docs, "units": units, "rag": idx, "spotcheck": spot,
                     "sources": [{"label": lb, "path": str(rt)} for rt, lb in ROOTS]})


def cmd_books(_):
    books = []
    for root, label in ROOTS:
        if not root.exists():
            continue
        for d in sorted(p for p in root.glob("*") if p.is_dir()):
            st = d / "structure.json"
            if not st.exists():
                continue
            try:
                j = json.loads(st.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            ku = d / "knowledge_units.jsonl"
            books.append({"id": d.name, "source": label, "pages": j.get("pages"),
                          "chapters": len(j.get("chapters") or []),
                          "units": sum(1 for ln in ku.read_text(encoding="utf-8").splitlines() if ln.strip()) if ku.exists() else 0})
    return envelope({"books": books, "total": len(books)})


def cmd_units(args):
    units = load_units()
    if args.book:
        units = [u for u in units if args.book in u["book"]]
    if args.chapter:
        units = [u for u in units if args.chapter in (u.get("chapter") or "")]
    total = len(units)
    page = max(1, args.page)
    size = max(1, min(args.size, 200))
    start = (page - 1) * size
    return envelope({"total": total, "page": page, "size": size, "items": units[start:start + size]})


def cmd_search(args):
    q = tokenize(args.q)
    if not q:
        return envelope(None, err("INVALID_ARGUMENT", "q 不能为空"))
    units = load_units()
    df = {}
    for u in units:
        for t in set(tokenize(u["concept"]) + tokenize(u["claim"]) + tokenize(u["evidence"])):
            df[t] = df.get(t, 0) + 1
    n = len(units)
    scored = []
    for u in units:
        tf = {}
        for t in tokenize(u["concept"]) + tokenize(u["claim"]) + tokenize(u["evidence"]):
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q:
            if tf.get(t):
                idf = math.log(1 + n / max(1, df.get(t, 0)))
                w = 2.0 if t in tokenize(u["concept"]) else 1.0
                s += w * (1 + math.log(tf[t])) * idf
        if s > 0:
            scored.append((s, u))
    scored.sort(key=lambda x: -x[0])
    top = max(1, min(args.top, 50))
    return envelope({"q": args.q, "total_hits": len(scored),
                     "items": [{**u, "score": round(s, 3)} for s, u in scored[:top]]})


def cmd_get_unit(args):
    for u in load_units():
        if u["id"] == args.id:
            return envelope(u)
    return envelope(None, err("NOT_FOUND", f"unit {args.id} 不存在"))


def cmd_briefing(_):
    p = STATIC / "kb_daily_briefing.json"
    if not p.exists():
        return envelope(None, err("NOT_READY", "简报尚未生成（daily_briefing.py）"))
    return envelope(json.loads(p.read_text(encoding="utf-8")))


SCHEMA = {
    "envelope": {
        "type": "object", "required": ["api_version", "request_id", "ok", "data", "error"],
        "properties": {"api_version": {"const": "1.0"}, "request_id": {"type": "string"},
                       "ok": {"type": "boolean"}, "data": {}, "error": {"type": ["object", "null"]},
                       "generated_at": {"type": "string"}},
    },
    "unit": {"required": ["id", "source", "book", "chapter", "page_start", "page_end", "concept", "claim", "evidence"],
             "properties": {"id": {"type": "string"}, "source": {"enum": ["书籍", "研报"]},
                            "page_start": {"type": "integer"}, "page_end": {"type": "integer"}}},
    "search_data": {"required": ["q", "total_hits", "items"]},
    "errors": {"INVALID_ARGUMENT": 400, "NOT_FOUND": 404, "NOT_READY": 503, "UPSTREAM_UNAVAILABLE": 502},
}


def cmd_schema(_):
    return envelope({"component": "kb_read_api", "version": API_VERSION, "schema": SCHEMA})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="知识库只读接口（CLI/JSON）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("books", cmd_books), ("units", cmd_units),
                     ("search", cmd_search), ("get-unit", cmd_get_unit), ("briefing", cmd_briefing),
                     ("schema", cmd_schema)):
        p = sub.add_parser(name)
        if name == "units":
            p.add_argument("--book", default="")
            p.add_argument("--chapter", default="")
            p.add_argument("--page", type=int, default=1)
            p.add_argument("--size", type=int, default=20)
        if name == "search":
            p.add_argument("--q", required=True)
            p.add_argument("--top", type=int, default=10)
        if name == "get-unit":
            p.add_argument("--id", required=True)
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    result = args.fn(args)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
