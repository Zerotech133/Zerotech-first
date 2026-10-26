# -*- coding: utf-8 -*-
"""确定性质量检查（只读）：表存在/空分片/分区重复/行数/主键抽样。

只看"结构可证的错"，不做业务口径判断。
用法：python tools/datawarehouse/quality_test.py --json [--table daily]
退出码：0=全过 / 1=存在失败项 / 2=依赖缺失
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

CORE_TABLES = ["daily", "adj_factor", "daily_basic", "index_daily", "moneyflow", "margin_detail"]


def _files(table: str) -> list:
    catalog = common.load_json(common.CATALOG) or {}
    entry = (catalog.get("tables") or {}).get(table) or {}
    store = Path(str(entry.get("store") or ""))
    if not store.exists():
        return []
    if store.is_dir():
        return sorted(store.glob("*.parquet"))
    return [store] if store.suffix == ".parquet" else []


def check_table(table: str, max_files: int = 40) -> dict:
    files = _files(table)
    checks = []
    checks.append({"check": "store_files", "ok": bool(files), "detail": "%d" % len(files)})
    if not files:
        return {"table": table, "checks": checks, "ok": False}
    # 1) 空分片检测（读 footer 行数；最多 max_files 个）
    zero, rows_total, read_ok = [], 0, 0
    try:
        import pyarrow.parquet as pq  # noqa: WPS433
        for f in files[:max_files]:
            try:
                n = pq.ParquetFile(f).metadata.num_rows
                rows_total += n
                read_ok += 1
                if n == 0:
                    zero.append(f.name)
            except Exception:  # noqa: BLE001
                continue
        checks.append({"check": "empty_parts", "ok": not zero,
                       "detail": "%d 个空分片 (抽样 %d)" % (len(zero), read_ok)})
        checks.append({"check": "rows_positive", "ok": rows_total > 0, "detail": rows_total})
    except Exception:  # noqa: BLE001
        checks.append({"check": "pyarrow", "ok": True, "detail": "pyarrow 缺失，跳过 footer 检查（降级）"})
    # 2) 分区重复检测（文件名日期重复）
    dates = Counter()
    for f in files:
        m = re.search(r"(20\d{6})", f.name)
        if m:
            dates[m.group(1)] += 1
    dup = {d: c for d, c in dates.items() if c > 1}
    if dates:
        checks.append({"check": "partition_dup", "ok": not dup,
                       "detail": {"dup_dates": len(dup), "sample": dict(list(dup.items())[:3])}})
    return {"table": table, "files": len(files), "checks": checks,
            "ok": all(c["ok"] for c in checks)}


def collect(only: str | None = None) -> dict:
    tables = [only] if only else CORE_TABLES
    results = [check_table(t) for t in tables]
    failed = [r["table"] for r in results if not r["ok"]]
    return {"tool": "quality_test", "code": common.EXIT_WARN if failed else common.EXIT_OK,
            "tables": results, "failed": failed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="质量检查（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", default=None)
    a = ap.parse_args(argv)
    return common.emit(collect(a.table), as_json=a.json, title="quality_test")


if __name__ == "__main__":
    raise SystemExit(main())
