# -*- coding: utf-8 -*-
"""PDF 知识入库状态 → 门户静态 JSON（零重启方案）。

扫描 C:/项目/PDF/_ingest/*/（structure.json / knowledge_units.jsonl）+ 聚合 verify.json + knowledge_docs RAG 索引，
写 portal/static/kb_ingest.json；页面 /kb_ingest.html 读取展示。
用法：python tools/kb_corpus/kb_ingest_status.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
INGESTS = [("书籍库", Path(r"C:/项目/PDF/_ingest")), ("研报库", Path(r"C:/项目/PDF/_ingest_reports"))]
KB_DOCS = REPO / "knowledge_docs"
OUT = REPO / "portal" / "static" / "kb_ingest.json"


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def count_lines(p: Path) -> int:
    try:
        return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
    except Exception:  # noqa: BLE001
        return 0


def main() -> int:
    books = []
    verify_books = {}
    verify_ok = True
    pcheck = {}
    for label, ingest in INGESTS:
        if not ingest.exists():
            continue
        for d in sorted(p for p in ingest.glob("*") if p.is_dir()):
            st = read_json(d / "structure.json") or {}
            units = count_lines(d / "knowledge_units.jsonl")
            books.append({
                "book": d.name,
                "source": label,
                "pages": st.get("pages"),
                "chapters": len(st.get("chapters") or []),
                "units": units,
                "has_ocr": (d / "ocr.txt").exists(),
                "has_layout": (d / "layout.jsonl").exists(),
            })
        v = read_json(ingest / "verify.json") or {}
        if v:
            verify_ok = verify_ok and bool(v.get("ok"))
            verify_books.update({k: x.get("checks") for k, x in (v.get("books") or {}).items()})
        pc = read_json(ingest / "pipeline_check.json") or {}
        if pc:
            pcheck = pc
    idx = read_json(KB_DOCS / ".rag_index.json") or {}
    verify = {"ok": verify_ok, "books": verify_books}
    records = idx.get("records") or []
    docs = {r.get("doc") or r.get("source") or r.get("file") for r in records if isinstance(r, dict)}
    docs.discard(None)
    total_pages = sum(b["pages"] or 0 for b in books)
    total_chapters = sum(b["chapters"] for b in books)
    total_units = sum(b["units"] for b in books)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ingest_dirs": [str(x) for _, x in INGESTS],
        "books": books,
        "totals": {"books": len(books), "pages": total_pages, "chapters": total_chapters, "units": total_units},
        "verify": {
            "ok": bool(verify.get("ok")),
            "books": {k: v.get("checks") for k, v in (verify.get("books") or {}).items()},
        },
        "pipeline": {"ok": bool(pcheck.get("ok")), "next_steps": pcheck.get("next_steps") or []},
        "rag": {"docs": len(docs), "records": len(records),
                "version": idx.get("index_version") or idx.get("version"), "updated_at": idx.get("updated_at")},
        "thresholds": {"ocr_chars_per_page_min": 100, "page_coverage_min": 0.95,
                       "chapters_min": 5, "units_min": 10, "citation_rate_min": 1.0},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("写出", OUT, "| books", len(books), "pages", total_pages, "units", total_units,
          "| verify", payload["verify"]["ok"], "| pipeline", payload["pipeline"]["ok"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
