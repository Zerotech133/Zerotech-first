# -*- coding: utf-8 -*-
"""知识单元全量快照：书籍/研报/策略经典 三源 → portal/static/kb_units_all.json（页面检索用）。

用法：python tools/kb_corpus/kb_units_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_json import SafeJsonError, backup_then_atomic_write  # noqa: E402

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"
ROOTS = [("书籍", Path(r"C:/项目/PDF/_ingest")),
         ("研报", Path(r"C:/项目/PDF/_ingest_reports")),
         ("策略经典", Path(r"C:/项目/PDF/_ingest_notes"))]


def validate_units_payload(parsed, existing: dict | None = None) -> None:
    """schema 校验：dict/total=int==len(units)/unit 必填字段；空快照不覆盖已有非空快照。"""
    if not isinstance(parsed, dict):
        raise ValueError("payload 不是 dict")
    units = parsed.get("units")
    if not isinstance(units, list):
        raise ValueError("payload.units 不是 list")
    if not isinstance(parsed.get("total"), int) or parsed["total"] != len(units):
        raise ValueError(f"total 与 units 数不一致：{parsed.get('total')} vs {len(units)}")
    required = ("source", "book", "claim")
    for i, u in enumerate(units):
        if not isinstance(u, dict):
            raise ValueError(f"units[{i}] 不是 dict")
        for key in required:
            if not str(u.get(key) or "").strip():
                raise ValueError(f"units[{i}] 缺字段 {key}")
    if not units and isinstance(existing, dict) and (existing.get("units") or []):
        raise ValueError("拒绝空快照覆盖已有非空快照（源目录缺失？）")
    return None


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
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "total": len(units), "units": units}
    existing = None
    if OUT.is_file():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
    try:
        backup_then_atomic_write(OUT, payload, validator=lambda parsed: validate_units_payload(parsed, existing))
    except SafeJsonError as exc:
        print(f"写盘失败（保留旧快照）：{exc}", flush=True)
        return 1
    print(json.dumps({"total": len(units), "by_source": {
        s: sum(1 for u in units if u["source"] == s) for s in ("书籍", "研报", "策略经典")}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
