# -*- coding: utf-8 -*-
"""目录快照：catalog v11 与来源资格 registry 一致性 + 物理漂移抽样（只读）。

用法：
    python tools/datawarehouse/catalog_snapshot.py --json
退出码：0=一致 / 1=发现漂移或登记陈旧 / 2=登记文件缺失
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SAMPLE_TABLES = 25


def compare_sha(catalog_sha: str | None, registry: dict) -> dict:
    want = (registry or {}).get("catalog_sha256")
    return {"catalog_sha256": catalog_sha, "registry_catalog_sha256": want,
            "match": bool(catalog_sha and want and catalog_sha == want)}


def drift_sample(catalog: dict, limit: int = SAMPLE_TABLES) -> list:
    """对 store 存在性与 parquet 文件数做抽样比对（不读内容）。"""
    rows = []
    tables = (catalog or {}).get("tables") or {}
    for name in sorted(tables)[:limit]:
        e = tables[name]
        store = Path(str(e.get("store") or ""))
        item = {"table": name, "store_exists": store.exists() and bool(str(e.get("store")))}
        if item["store_exists"]:
            try:
                files = len(list(store.glob("*.parquet"))) if store.is_dir() else 1
            except Exception:  # noqa: BLE001
                files = -1
            reg = e.get("files")
            item["files_on_disk"] = files
            item["files_registered"] = reg
            if isinstance(reg, int) and reg >= 0 and files >= 0:
                item["mismatch"] = files != reg
        rows.append(item)
    return rows


def collect(limit: int = SAMPLE_TABLES) -> dict:
    catalog = common.load_json(common.CATALOG)
    registry = common.load_json(common.REGISTRY)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), dict):
        return {"tool": "catalog_snapshot", "code": common.EXIT_NOT_READY,
                "error": "catalog.json 不可读", "path": common.rel(common.CATALOG)}
    if not isinstance(registry, dict):
        return {"tool": "catalog_snapshot", "code": common.EXIT_NOT_READY,
                "error": "warehouse_registry.json 不可读", "path": common.rel(common.REGISTRY)}

    tables = catalog["tables"]
    cat_sha = common.sha256_file(common.CATALOG)
    sha_cmp = compare_sha(cat_sha, registry)
    src = Counter(v.get("source") for v in tables.values())
    layout = Counter(v.get("layout") for v in tables.values())
    vstatus = Counter(v.get("verification_status") for v in tables.values())
    mstatus = Counter(v.get("manifest_status") for v in tables.values())
    qstatus = Counter((r or {}).get("qualification_status") for r in registry.get("physical_tables") or [])
    sample = drift_sample(catalog, limit=limit)
    mismatches = [s for s in sample if s.get("mismatch")]
    missing_stores = [s["table"] for s in sample if not s.get("store_exists")]

    issues = []
    if not sha_cmp["match"]:
        issues.append("registry.catalog_sha256 与当前 catalog 不一致（登记陈旧）")
    if missing_stores:
        issues.append("抽样中 %d 张表 store 不存在" % len(missing_stores))
    if mismatches:
        issues.append("抽样中 %d 张表 parquet 文件数与登记不符" % len(mismatches))
    if registry.get("generated_at", "") < "2026-10-20":
        issues.append("registry generated_at=%s（早于 10-20，需重生成）" % registry.get("generated_at"))

    return {
        "tool": "catalog_snapshot",
        "code": common.EXIT_WARN if issues else common.EXIT_OK,
        "catalog": {
            "version": catalog.get("version"), "updated": catalog.get("updated"),
            "tables": len(tables), "aliases": len(catalog.get("aliases") or {}),
            "sha256": cat_sha, "by_source": dict(src), "by_layout": dict(layout),
            "verification_status": dict(vstatus), "manifest_status": dict(mstatus),
        },
        "registry": {
            "generated_at": registry.get("generated_at"),
            "physical_count": registry.get("physical_count"),
            "display_count": registry.get("display_count"),
            "catalog_sha256_match": sha_cmp["match"],
            "qualification_status": dict(qstatus),
        },
        "drift_sample": {"tables": len(sample), "missing_stores": missing_stores,
                         "file_count_mismatch": [m["table"] for m in mismatches]},
        "issues": issues,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="目录快照（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=SAMPLE_TABLES)
    a = ap.parse_args(argv)
    return common.emit(collect(max(1, int(a.limit))), as_json=a.json, title="catalog_snapshot")


if __name__ == "__main__":
    raise SystemExit(main())
