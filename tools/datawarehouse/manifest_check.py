# -*- coding: utf-8 -*-
"""manifest 覆盖核对：4 个 `_manifest.json` 键 ∩ catalog 表名（只读）。

用法：python tools/datawarehouse/manifest_check.py --json
退出码：0=全部符合预期 / 1=覆盖不足 / 2=manifest 缺失
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

# 2026-10-27 实测基线（A 席）；用于判定"是否回退"
BASELINE = {"tdx": 166, "tushare": 30, "xueqiu": 7, "cnopendata": 6}


def manifest_keys(name: str, data: dict) -> set:
    """兼容两种结构：平铺 table→state；或 {version,tables:{...}}。"""
    if not isinstance(data, dict):
        return set()
    if name == "cnopendata" or isinstance(data.get("tables"), dict):
        return set((data.get("tables") or {}).keys())
    return set(data.keys())


def coverage(manifest_keys_set: set, catalog_names: set) -> dict:
    hit = sorted(manifest_keys_set & catalog_names)
    extra = sorted(manifest_keys_set - catalog_names)
    missing = sorted(catalog_names - manifest_keys_set)
    return {"keys": len(manifest_keys_set), "covered": len(hit), "extra": len(extra),
            "missing_count": len(missing), "missing_sample": missing[:10],
            "extra_names": extra[:20]}


def collect() -> dict:
    catalog = common.load_json(common.CATALOG)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), dict):
        return {"tool": "manifest_check", "code": common.EXIT_NOT_READY, "error": "catalog 不可读"}
    catalog_names = set(catalog["tables"])
    out, issues = {}, []
    for name in ("tdx", "tushare", "xueqiu", "cnopendata"):
        p = common.DATA / name / "_manifest.json"
        data = common.load_json(p)
        if data is None:
            out[name] = {"exists": False, "code": common.EXIT_NOT_READY}
            issues.append("%s manifest 缺失" % name)
            continue
        cov = coverage(manifest_keys(name, data), catalog_names)
        out[name] = {"exists": True, **cov, "baseline": BASELINE.get(name)}
        if cov["covered"] < BASELINE.get(name, 0):
            issues.append("%s 覆盖 %d < 基线 %d" % (name, cov["covered"], BASELINE.get(name, 0)))
    return {"tool": "manifest_check",
            "code": common.EXIT_WARN if issues else common.EXIT_OK,
            "manifests": out, "issues": issues}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manifest 覆盖核对（只读）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return common.emit(collect(), as_json=a.json, title="manifest_check")


if __name__ == "__main__":
    raise SystemExit(main())
