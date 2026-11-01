# -*- coding: utf-8 -*-
"""知识单元全量快照：书籍/研报/策略经典 三源 → portal/static/kb_units_all.json（页面检索用）。

用法：python tools/kb_corpus/kb_units_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"
ROOTS = [("书籍", Path(r"C:/项目/PDF/_ingest")),
         ("研报", Path(r"C:/项目/PDF/_ingest_reports")),
         ("策略经典", Path(r"C:/项目/PDF/_ingest_notes"))]


def main() -> int:
    units = []
    for label, root in ROOTS:
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
                units.append({"source": label, "book": u.get("book") or d.name,
                              "chapter": u.get("chapter", ""),
                              "p1": u.get("page_start"), "p2": u.get("page_end"),
                              "concept": u.get("concept", ""), "claim": u.get("claim", ""),
                              "evidence": u.get("evidence", "")})
    OUT = STATIC / "kb_units_all.json"
    OUT.write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                               "total": len(units), "units": units}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"total": len(units), "by_source": {
        s: sum(1 for u in units if u["source"] == s) for s in ("书籍", "研报", "策略经典")}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
