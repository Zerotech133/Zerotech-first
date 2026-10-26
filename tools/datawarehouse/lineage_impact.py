# -*- coding: utf-8 -*-
"""血缘影响分析（只读、静态）：catalog 表 → store → alias → 仓内消费方。

用法：python tools/datawarehouse/lineage_impact.py --table daily --json
退出码：0=已产出影响清单 / 1=表未登记
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCAN_DIRS = ["quant_backtest", "k_stock", "analysis", "portal", "agent_platform", "tools", "news_collector"]
SCAN_EXT = {".py", ".ps1", ".sql"}
SKIP = re.compile(r"\\\.worktrees\\|\\__pycache__\\|\\node_modules\\|\\.venv|\\vendor\\|\\pylibs\\|\\archive\\")


def find_alias(table: str, catalog: dict) -> str | None:
    for alias, spec in (catalog.get("aliases") or {}).items():
        text = str(spec)
        if table in text:
            return alias
    return None


def consumers(needle: str, max_files: int = 8000, max_hits: int = 40) -> list:
    hits, seen = [], 0
    for d in SCAN_DIRS:
        base = common.REPO / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if seen >= max_files or len(hits) >= max_hits:
                return hits
            if not p.is_file() or p.suffix.lower() not in SCAN_EXT:
                continue
            if SKIP.search(str(p)):
                continue
            seen += 1
            try:
                if needle in p.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(common.rel(p))
            except Exception:  # noqa: BLE001
                continue
    return hits


def collect(table: str) -> dict:
    catalog = common.load_json(common.CATALOG) or {}
    entry = (catalog.get("tables") or {}).get(table)
    if not entry:
        return {"tool": "lineage_impact", "code": common.EXIT_WARN,
                "error": "表未在 catalog 登记: %s" % table}
    store = str(entry.get("store") or "")
    store_name = Path(store).name or table
    alias = find_alias(table, catalog)
    needles = [n for n in {table, store_name, alias} if n]
    hit_map = {n: consumers(n) for n in needles}
    return {"tool": "lineage_impact", "code": common.EXIT_OK, "table": table,
            "store": common.rel(store) if store else None, "alias": alias,
            "manifest": common.rel(entry.get("manifest") or "") if entry.get("manifest") else None,
            "consumers": hit_map,
            "note": "静态文本检索，非运行时血缘；精确血缘需 OpenLineage 事件（二期）"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="血缘影响分析（只读）")
    ap.add_argument("--table", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return common.emit(collect(a.table), as_json=a.json, title="lineage_impact")


if __name__ == "__main__":
    raise SystemExit(main())
