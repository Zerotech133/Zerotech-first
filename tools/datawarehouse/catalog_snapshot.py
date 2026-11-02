# -*- coding: utf-8 -*-
"""目录快照：catalog（当前 v12）与来源资格 registry 一致性 + 物理漂移抽样（只读）。

用法：
    python tools/datawarehouse/catalog_snapshot.py --json
退出码：0=一致 / 1=发现漂移或登记陈旧 / 2=登记文件缺失

漂移口径（K-DATA-20261027-SMALL3）：
- 文件数按 catalog `layout` 选扩展名：parquet/part/by_code/by_date/by_code_month/
  by_period/single → `*.parquet`；jsonl → `*.jsonl`；sqlite/pg 等库型不做文件数比对（files_na）；
- 差异分级：|diff|≤1 → drift_info（登记时点快照 vs 实时写，不计 issue）；
  |diff|>1 → drift_warn（计 issue）；登记 files 为 null/0 而磁盘>0 → drift_info；
- store 缺失始终报（missing_stores，计 issue）。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SAMPLE_TABLES = 25

# layout → 物理文件扩展名；库型（sqlite/pg）与未知 layout 不做文件数比对
LAYOUT_EXT = {
    "parquet": ".parquet",
    "part": ".parquet",
    "by_code": ".parquet",
    "by_date": ".parquet",
    "by_code_month": ".parquet",
    "by_period": ".parquet",
    "single": ".parquet",
    "jsonl": ".jsonl",
}


def compare_sha(catalog_sha: str | None, registry: dict) -> dict:
    want = (registry or {}).get("catalog_sha256")
    return {"catalog_sha256": catalog_sha, "registry_catalog_sha256": want,
            "match": bool(catalog_sha and want and catalog_sha == want)}


def _count_files(store: Path, ext: str) -> int:
    """只数文件不读内容；store 本身是文件（single 布局）按 1 个算。"""
    if store.is_dir():
        return len(list(store.glob("*" + ext)))
    return 1


def drift_sample(catalog: dict, limit: int = SAMPLE_TABLES) -> list:
    """按 catalog layout 抽样比对 store 存在性与物理文件数（只读、不读内容）。

    每行 file_check 取值：ok / info（|diff|≤1 或登记值缺失）/ warn（|diff|>1）/
    na（sqlite/pg 等库型跳过）/ store_missing（store 不存在，始终报）。
    """
    rows = []
    tables = (catalog or {}).get("tables") or {}
    for name in sorted(tables)[:limit]:
        e = tables[name] or {}
        layout = str(e.get("layout") or "").strip().lower()
        raw_store = str(e.get("store") or "")
        store = Path(raw_store)
        if raw_store and not store.is_absolute():
            store = Path(common.REPO) / store  # task-77：相对 store 按主仓根解析（不依赖 cwd）
        reg = e.get("files")
        item = {"table": name, "layout": layout or None,
                "store_exists": bool(raw_store) and store.exists(),
                "files_registered": reg}
        if not item["store_exists"]:
            item["file_check"] = "store_missing"
            rows.append(item)
            continue
        ext = LAYOUT_EXT.get(layout)
        if ext is None:
            # sqlite/pg（库内表，没有分片文件）或未知 layout：跳过文件数比对，不制造误报
            item["files_na"] = True
            item["file_check"] = "na"
            item["na_reason"] = "layout=%s 不做文件数比对" % (layout or "?")
            rows.append(item)
            continue
        try:
            files = _count_files(store, ext)
        except OSError:
            files = -1
        item["files_on_disk"] = files
        item["pattern"] = "*" + ext
        if files < 0:
            item["files_na"] = True
            item["file_check"] = "na"
            item["na_reason"] = "文件盘点失败"
            rows.append(item)
            continue
        if not isinstance(reg, int) or isinstance(reg, bool) or reg < 0:
            # 登记值不可比：缺登记 ≠ 漂移，归 info 提示补登记
            item["diff"] = None
            item["file_check"] = "info"
            item["info_reason"] = "登记 files 不可比（%r）" % (reg,)
            rows.append(item)
            continue
        diff = files - reg
        item["diff"] = diff
        if reg == 0 and files > 0:
            item["file_check"] = "info"
            item["info_reason"] = "登记 files=0 而磁盘 %d 个，需补登记" % files
        elif abs(diff) > 1:
            item["file_check"] = "warn"
        elif abs(diff) == 1:
            item["file_check"] = "info"
            item["info_reason"] = "登记时点快照 vs 实时写（diff=%+d）" % diff
        else:
            item["file_check"] = "ok"
        rows.append(item)
    return rows


def _drift_brief(row: dict) -> dict:
    """info/warn 明细的紧凑投影（输出不膨胀）。"""
    return {"table": row.get("table"), "layout": row.get("layout"),
            "files_registered": row.get("files_registered"),
            "files_on_disk": row.get("files_on_disk"), "diff": row.get("diff"),
            "reason": row.get("info_reason")}


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
    warns = [s for s in sample if s.get("file_check") == "warn"]
    infos = [s for s in sample if s.get("file_check") == "info"]
    nas = [s for s in sample if s.get("file_check") == "na"]
    missing_stores = [s["table"] for s in sample if not s.get("store_exists")]

    issues = []
    if not sha_cmp["match"]:
        issues.append("registry.catalog_sha256 与当前 catalog 不一致（登记陈旧）")
    if missing_stores:
        issues.append("抽样中 %d 张表 store 不存在" % len(missing_stores))
    if warns:
        issues.append("抽样中 %d 张表文件数与登记差异 >1（drift_warn）" % len(warns))
    # task-77：generated_at 可能为 None/非字符串（旧 registry/坏 JSON），不能直接字符串比较
    regen = registry.get("generated_at")
    if not isinstance(regen, str) or not regen:
        issues.append("registry generated_at 缺失/非字符串（%r），需重生成" % (regen,))
    elif regen < "2026-10-20":
        issues.append("registry generated_at=%s（早于 10-20，需重生成）" % regen)

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
                         "file_count_mismatch": [s["table"] for s in warns],
                         "drift_warn": [_drift_brief(s) for s in warns],
                         "drift_info": [_drift_brief(s) for s in infos],
                         "files_na": [s["table"] for s in nas]},
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
