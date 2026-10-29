#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据生命周期覆盖率审计（P2-2，2026-10-30）：catalog lifecycle 字段只读盘点。

四态（治理方案 §5.3）：active / planned / deprecated / archived。
本工具只读 catalog，给出：覆盖率、分布、缺失 backlog（按数据域分组）。

用法：
  python tools/datawarehouse/lifecycle_coverage.py            # 人读摘要
  python tools/datawarehouse/lifecycle_coverage.py --json      # 机器
  python tools/datawarehouse/lifecycle_coverage.py --check      # 覆盖率 <100% → exit 1（月度门禁）
  python tools/datawarehouse/lifecycle_coverage.py --list-missing 30
退出码：0 正常；1 check 未达标。
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import common  # noqa: E402

VALID = ("active", "planned", "deprecated", "archived")


def _table_entry(name: str, entry) -> dict:
    if isinstance(entry, dict):
        return entry
    return {"_invalid": True}


def audit(catalog_path=None) -> dict:
    cat_path = Path(str(catalog_path)) if catalog_path else Path(str(common.CATALOG))
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    tables = cat.get("tables") or {}
    if isinstance(tables, list):
        tables = dict(tables)
    dist = collections.Counter()
    missing = []
    invalid = []
    by_domain = collections.defaultdict(lambda: {"total": 0, "missing": 0})
    for name, raw in tables.items():
        entry = _table_entry(name, raw)
        domain = str(entry.get("source") or name.split("_")[0] or "?")
        by_domain[domain]["total"] += 1
        lc = entry.get("lifecycle")
        if lc in VALID:
            dist[lc] += 1
        elif lc:
            dist["invalid:%s" % lc] += 1
            invalid.append(name)
            by_domain[domain]["missing"] += 1
        else:
            dist["missing"] += 1
            missing.append(name)
            by_domain[domain]["missing"] += 1
    total = len(tables)
    covered = sum(dist[k] for k in VALID)
    return {
        "tool": "lifecycle_coverage",
        "catalog": str(cat_path),
        "catalog_version": cat.get("version"),
        "total": total,
        "covered": covered,
        "coverage_pct": round(covered * 100.0 / total, 1) if total else 0.0,
        "distribution": dict(dist),
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "by_domain": {k: v for k, v in sorted(by_domain.items())},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="catalog lifecycle 覆盖率审计（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="覆盖率<100%% → exit 1")
    ap.add_argument("--list-missing", type=int, default=0, help="列出前 N 个缺失表")
    args = ap.parse_args(argv)

    rep = audit()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("lifecycle 覆盖 %d/%d（%.1f%%）分布=%s"
              % (rep["covered"], rep["total"], rep["coverage_pct"],
                 json.dumps(rep["distribution"], ensure_ascii=False)))
        print("按数据域（缺失/总数）：")
        for d, v in rep["by_domain"].items():
            if v["missing"]:
                print("  %-28s %3d/%3d" % (d, v["missing"], v["total"]))
        if args.list_missing:
            print("缺失样例（前 %d）：" % args.list_missing)
            for n in rep["missing"][: args.list_missing]:
                print("  - %s" % n)
    if args.check and rep["coverage_pct"] < 100.0:
        print("CHECK FAIL：lifecycle 覆盖率 %.1f%% < 100%%" % rep["coverage_pct"],
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
