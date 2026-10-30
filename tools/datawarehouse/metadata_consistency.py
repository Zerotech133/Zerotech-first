# -*- coding: utf-8 -*-
"""元数据一致性检查：catalog × registry × data_lifecycle × 物理 store。

检查项（阻断级）：
1. catalog 版本/表结构可读；registry catalog_sha256 与 catalog 实算一致、计数一致；
2. catalog 声明的本地 store（非 pg://）必须存在 **除非** lifecycle=planned/deprecated/archived；
3. placeholder_empty 资产必须带 lifecycle=planned/deprecated/archived（否则视为登记错误）；
4. manifest_status=missing/open 的表必须声明 manifest 路径或归生命周期（否则登记错误）；
5. registry 资格：lifecycle=planned/deprecated/archived 的表 qualification_status 必须一致（不参与 unknown 欠账）。
告警级：lifecycle 覆盖表中的未知表名、not_applicable 之外的空 manifest 等。

用法：python tools/datawarehouse/metadata_consistency.py [--json]
退出码：0=一致 / 1=不一致 / 2=前置不可读。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

try:
    import lifecycle  # noqa: E402
except Exception:  # noqa: BLE001
    lifecycle = None

SKIP_LIFECYCLE = ("planned", "deprecated", "archived")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check(cat_path: Path | None = None, reg_path: Path | None = None, lifecycle_mod=None) -> dict:
    """可注入路径/生命周期模块（单测用）；默认读生产数据根。"""
    lc = lifecycle_mod if lifecycle_mod is not None else lifecycle
    issues: list[str] = []
    warnings: list[str] = []
    cat_path = Path(cat_path) if cat_path is not None else Path(str(common.CATALOG))
    reg_path = Path(reg_path) if reg_path is not None else Path(str(common.REGISTRY))
    if not cat_path.exists() or not reg_path.exists():
        return {"tool": "metadata_consistency", "code": 2,
                "issues": ["catalog_or_registry_missing"], "warnings": []}
    try:
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"tool": "metadata_consistency", "code": 2,
                "issues": ["json_unreadable: " + str(e)[:100]], "warnings": []}
    tables = cat.get("tables") or {}
    physical = reg.get("physical_tables") or []

    # 1) sha/计数
    real_sha = sha256_file(cat_path)
    if reg.get("catalog_sha256") != real_sha:
        issues.append("registry_catalog_sha_mismatch")
    if int(reg.get("physical_count") or 0) != len(physical):
        issues.append("registry_physical_count_mismatch")
    _dc = reg.get("display_count")
    if (int(_dc) if _dc is not None else -1) != len(reg.get("display_assets") or []):
        issues.append("registry_display_count_mismatch")
    if int(reg.get("physical_count") or 0) != len(tables):
        warnings.append("registry_vs_catalog_table_count_diff:%s/%s" % (reg.get("physical_count"), len(tables)))

    # 2) store 存在性（lifecycle 豁免）
    for name, e in tables.items():
        if not isinstance(e, dict):
            continue
        store = str(e.get("store") or "")
        if not store or store.startswith("pg://"):
            continue
        status = lc.status_for(name) if lc else "active"
        if status in SKIP_LIFECYCLE:
            continue
        if not Path(store).exists():
            issues.append("store_missing:%s:%s" % (name, store))

    # 3) placeholder 必须归生命周期
    for name, e in tables.items():
        if not isinstance(e, dict):
            continue
        if e.get("verification_status") == "placeholder_empty":
            status = lc.status_for(name) if lc else "active"
            if status not in SKIP_LIFECYCLE:
                issues.append("placeholder_without_lifecycle:%s" % name)

    # 4) missing/open 必须有 manifest 声明或生命周期
    for name, e in tables.items():
        if not isinstance(e, dict):
            continue
        if e.get("manifest_status") in ("missing", "open"):
            status = lc.status_for(name) if lc else "active"
            if not e.get("manifest") and status not in SKIP_LIFECYCLE:
                issues.append("manifest_missing_without_concept:%s" % name)

    # 5) registry 资格一致性
    by_name = {}
    for e in physical:
        for t in (e.get("physical_tables") or []):
            by_name[str(t)] = e
    for name, e in tables.items():
        if not isinstance(e, dict):
            continue
        status = lc.status_for(name) if lc else "active"
        if status not in SKIP_LIFECYCLE:
            continue
        ent = by_name.get(name)
        if ent and ent.get("qualification_status") != status:
            issues.append("qualification_not_lifecycle:%s:%s!=%s" % (
                name, ent.get("qualification_status"), status))

    # 告警：lifecycle 覆盖了 catalog 不存在的表
    if lc is not None:
        for name in lc.all_overrides():
            if name not in tables:
                warnings.append("lifecycle_override_unknown_table:%s" % name)

    return {"tool": "metadata_consistency", "code": 1 if issues else 0,
            "catalog_version": cat.get("version"), "tables": len(tables),
            "physical_count": len(physical), "issues": issues, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="catalog/registry/lifecycle 一致性检查")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = check()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("元数据一致性：catalog v%s / %d 表 / registry %d 物理" % (
            res.get("catalog_version"), res.get("tables", 0), res.get("physical_count", 0)))
        for i in res["issues"][:20]:
            print("  FAIL", i)
        for w in res["warnings"][:10]:
            print("  WARN", w)
        print("result:", "PASS" if res["code"] == 0 else "FAIL")
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
