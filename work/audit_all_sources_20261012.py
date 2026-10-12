import json
from pathlib import Path
from collections import Counter

ROOT = Path("quant_backtest/data")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def inventory(name):
    root = ROOT / name
    manifest = load(root / "_manifest.json")
    physical = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")} if root.exists() else set()
    rows = []
    for asset, entry in manifest.items():
        files = list((root / asset).glob("*.parquet")) if (root / asset).exists() else []
        rows.append({"asset": asset, "done": bool(entry.get("done")), "rows": entry.get("rows"),
                     "codes": entry.get("codes"), "parts": len(files),
                     "physical": asset in physical, "probe": asset.endswith("_probe")})
    return {"manifest_total": len(manifest), "physical_dirs": len(physical),
            "done": sum(r["done"] for r in rows),
            "incomplete": [r["asset"] for r in rows if not r["done"]],
            "no_coverage": [r["asset"] for r in rows if r["codes"] == 0],
            "manifest_only": [r["asset"] for r in rows if not r["physical"]],
            "rows": rows}


registry = load(ROOT / "tushare_staging" / "tushare_qualification_candidate.json")
registry_rows = registry.get("source_registry", {}).get("physical_tables", [])
qualification = [{"asset": r.get("asset_id"), "status": r.get("qualification_status"),
                  "pending": r.get("pending_fields", [])}
                 for r in registry_rows]

history_root = ROOT / "tushare_staging" / "history_3y"
receipts = list(history_root.rglob("*.receipt.json")) if history_root.exists() else []
statuses = Counter()
for path in receipts:
    try:
        statuses[load(path).get("status", "invalid")] += 1
    except Exception:
        statuses["invalid"] += 1

health = load("work/all_sources_health_scan_20261012.json")
catalog = load(ROOT / "catalog.json")
manifest_paths = []
for value in catalog.get("tables", {}).values() if isinstance(catalog.get("tables"), dict) else []:
    manifest = value.get("manifest")
    if manifest:
        manifest_paths.append(Path(manifest).exists())

report = {
    "tdx": inventory("tdx"),
    "tushare": inventory("tushare"),
    "three_year_staging": {
        "receipt_count": len(receipts), "statuses": dict(statuses),
        "raw_json_count": len(list(history_root.rglob("*.json"))) - len(list(history_root.rglob("*.receipt.json"))) if history_root.exists() else 0,
        "parquet_count": len(list(history_root.rglob("*.parquet"))) if history_root.exists() else 0,
    },
    "qualification": {
        "global_activation": registry.get("global_activation"),
        "production_write": registry.get("production_write"),
        "tables": qualification,
    },
    "health": {
        "kpi": health.get("kpi", {}), "gaps": health.get("gaps", []),
        "integrity_fail": health.get("integrity", {}).get("fail_count"),
        "integrity_warn": health.get("integrity", {}).get("warn_count"),
    },
    "catalog_manifest_paths": {"count": len(manifest_paths), "existing": sum(manifest_paths),
                                "missing": len(manifest_paths) - sum(manifest_paths)},
    "read_only": True,
}
Path("work/all_sources_audit_20261012.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({
    "tdx": {k: report["tdx"][k] for k in ("manifest_total", "done", "incomplete", "no_coverage", "manifest_only")},
    "tushare": {k: report["tushare"][k] for k in ("manifest_total", "done", "incomplete", "no_coverage", "manifest_only")},
    "three_year_staging": report["three_year_staging"],
    "qualification": report["qualification"],
    "health": report["health"],
    "catalog_manifest_paths": report["catalog_manifest_paths"],
}, ensure_ascii=False))
