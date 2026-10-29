#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zhuge_memory_seed：把 P1-4 决策账本中的最终决策写入长期记忆。

默认 dry-run；--apply 才写 work/p1/memory/memory.jsonl。
只写决策类记忆，不写原始质询全文，不写用户隐私。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DECISIONS = REPO / "work" / "p1" / "decisions" / "decisions.jsonl"


def load_records(path: Path) -> list[dict]:
    records: dict[str, dict] = {}
    if not path.is_file():
        return []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        rid = str(ev.get("id") or "")
        if ev.get("event") == "created" and isinstance(ev.get("record"), dict):
            records[rid] = dict(ev["record"])
            continue
        if rid in records:
            for key in ("status", "decided_by", "decision_note", "outcome"):
                if ev.get(key) is not None:
                    records[rid][key] = ev[key]
    return list(records.values())


def candidates(records: list[dict]) -> list[dict]:
    out = []
    for rec in records:
        if str(rec.get("status") or "").lower() not in ("approved", "implemented"):
            continue
        title = str(rec.get("title") or "").strip()
        rec_text = str(rec.get("recommendation") or "").strip()
        if not title or not rec_text:
            continue
        out.append({
            "id": rec.get("id"),
            "text": "%s：%s" % (title[:120], rec_text[:500]),
            "tags": [str(rec.get("id") or ""), "decision"],
            "source": "p1-decisions",
            "confidence": "high" if rec.get("decided_by") else "medium",
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛亮长期记忆：决策账本种子")
    ap.add_argument("--decisions", default=str(DECISIONS))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    rows = candidates(load_records(Path(args.decisions)))
    if not args.apply:
        print(json.dumps({"ok": True, "mode": "dry-run", "candidates": len(rows),
                          "ids": [r["id"] for r in rows]}, ensure_ascii=False))
        return 0
    sys.path.insert(0, str(HERE))
    import zhuge_memory

    added = duplicates = 0
    for row in rows:
        res = zhuge_memory.remember(
            "decision", row["text"], tags=row["tags"], source=row["source"],
            confidence=row["confidence"],
        )
        if res.get("ok") and res.get("duplicate"):
            duplicates += 1
        elif res.get("ok"):
            added += 1
    print(json.dumps({"ok": True, "mode": "apply", "candidates": len(rows),
                      "added": added, "duplicates": duplicates}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
