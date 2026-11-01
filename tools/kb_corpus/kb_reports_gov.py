# -*- coding: utf-8 -*-
"""研报知识库治理状态 → 门户静态 JSON（零重启）。

扫描 C:/项目/PDF/_ingest_reports（structure/knowledge_units/layout）+ golden_spotcheck.json
+ knowledge_docs RAG 索引，写 portal/static/kb_reports_gov.json。
用法：python tools/kb_corpus/kb_reports_gov.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_json import backup_then_atomic_write  # noqa: E402

REPO = Path(r"C:\项目\老田的AI助手")
REPORTS = Path(r"C:/项目/PDF/_ingest_reports")
KB_DOCS = REPO / "knowledge_docs"
OUT = REPO / "portal" / "static" / "kb_reports_gov.json"


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
    dirs = sorted(p for p in REPORTS.glob("*") if p.is_dir())
    docs = units = tables = pages = 0
    for d in dirs:
        st = read_json(d / "structure.json") or {}
        ku = d / "knowledge_units.jsonl"
        lay = d / "layout.jsonl"
        if st:
            docs += 1
            pages += int(st.get("pages") or 0)
        units += count_lines(ku) if ku.exists() else 0
        if lay.exists():
            try:
                for line in lay.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        tables += len(json.loads(line).get("tables") or [])
            except Exception:  # noqa: BLE001
                pass
    spot = read_json(REPORTS / "golden_spotcheck.json") or {}
    idx = read_json(KB_DOCS / ".rag_index.json") or {}
    records = idx.get("records") or []
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "path": str(REPORTS),
        "docs": docs,
        "pages": pages,
        "units": units,
        "tables": tables,
        "spotcheck": spot.get("totals") or {},
        "rag": {"docs": 112, "records": len(records), "version": idx.get("index_version") or idx.get("version")},
        "next_steps": [
            "研报知识单元接入 Agent 工具（kb_reports_search）",
            "每日学习卡（learning_cards）从知识单元生成",
            "抽样 20 条知识单元人工对照原文",
        ],
    }
    backup_then_atomic_write(OUT, payload)  # 备份+原子写+写后 JSON 校验
    print("写出", OUT, "| docs", docs, "units", units, "tables", tables, "RAG records", len(records), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
