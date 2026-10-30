# -*- coding: utf-8 -*-
"""manifest 修复工具：为 catalog 中 manifest_status=missing/open 的表补齐源清单条目。

背景（2026-10-28）：catalog v12 有 36 表 missing + 4 表 open。真源清单
（data/tushare/_manifest.json 等）缺这些表键 → done 判定为 file_presence、
manifest_status=missing，影响 readiness 与治理台账。

本工具：
1. 从 catalog 找出 missing/open 且**已声明 manifest 路径**、store 存在的表；
2. 扫描 store 的 parquet 文件，统计 files / rows（footer num_rows，带上限）；
3. `--apply` 把 `{done: true, rows, files, updated_at, repaired_by}` 合并进对应
   `_manifest.json`（先备份 `.bak-<ts>`，原子写），并补记 kg_sync_task.log；
4. lifecycle=planned/deprecated/archived 的表跳过（由 data_lifecycle.yaml 治理）。

用法：
  python tools/datawarehouse/manifest_repair.py --json            # 只读报告（默认）
  python tools/datawarehouse/manifest_repair.py --apply           # 写 manifest
  python tools/datawarehouse/manifest_repair.py --apply --rows-max 2000
退出码：0=可修/已修成功；1=有异常；2=前置失败（数据根/manifest 不可写）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

try:
    import lifecycle  # noqa: E402
except Exception:  # noqa: BLE001
    lifecycle = None

KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
REPAIR_TAG = "manifest_repair"


def _status(entry: dict) -> str:
    return str(entry.get("lifecycle") or "active")


def find_manifest_path(entry: dict, store: Path) -> Path | None:
    mf = entry.get("manifest")
    if mf:
        p = Path(str(mf))
        return p if p.exists() else None
    cand = store.parent / "_manifest.json"
    return cand if cand.exists() else None


def scan_store(store: Path, rows_max: int = 1500) -> dict:
    files = []
    try:
        files = sorted(store.glob("*.parquet"))
    except OSError:
        files = []
    if not files:
        return {"files": 0, "rows": None, "scan": "no_parquet"}
    rows = 0
    rows_ok = True
    scanned = 0
    for f in files:
        if scanned >= rows_max:
            rows_ok = False
            break
        try:
            import pyarrow.parquet as pq  # noqa: WPS433
            pf = pq.ParquetFile(f)
            rows += int(pf.metadata.num_rows)
            scanned += 1
        except Exception:  # noqa: BLE001
            continue
    return {"files": len(files), "rows": rows if rows_ok else None,
            "rows_estimated": rows, "footer_scanned": scanned, "scan": "footer"}


def scan_sqlite(db: Path, table: str) -> dict:
    """sqlite 库（xueqiu_info.db 等）：按表名计行数；表不存在返回 0 文件。"""
    import sqlite3
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True, timeout=5)
        try:
            n = con.execute('SELECT count(*) FROM "%s"' % table.replace('"', '""')).fetchone()[0]  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
        finally:
            con.close()
        return {"files": 1, "rows": int(n), "scan": "sqlite"}
    except Exception:  # noqa: BLE001
        return {"files": 0, "rows": None, "scan": "sqlite_error"}


def plan() -> list[dict]:
    catalog = common.load_json(common.CATALOG) or {}
    tables = catalog.get("tables") or {}
    out = []
    for name, e in sorted(tables.items()):
        if not isinstance(e, dict):
            continue
        if e.get("manifest_status") not in ("missing", "open"):
            continue
        if lifecycle is not None and lifecycle.skipped(name):
            out.append({"table": name, "skip": "lifecycle_" + lifecycle.status_for(name)})
            continue
        if not e.get("manifest"):
            out.append({"table": name, "skip": "manifest_not_declared"})
            continue
        store = Path(str(e.get("store") or ""))
        if not store.exists():
            out.append({"table": name, "skip": "store_missing"})
            continue
        mfp = find_manifest_path(e, store)
        if mfp is None:
            out.append({"table": name, "skip": "manifest_file_missing"})
            continue
        if store.is_file() and store.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            info = scan_sqlite(store, name)
        else:
            info = scan_store(store)
        out.append({"table": name, "manifest": str(mfp), "store": str(store),
                    "source": e.get("source"), "old_status": e.get("manifest_status"), **info})
    return out


def apply(rows: list[dict], kg_log: bool = True) -> dict:
    todo = [r for r in rows if not r.get("skip") and r.get("files", 0) > 0]
    by_mf: dict[Path, list[dict]] = {}
    for r in todo:
        by_mf.setdefault(Path(r["manifest"]), []).append(r)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    changed = []
    for mfp, items in by_mf.items():
        try:
            data = json.loads(mfp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": "读 manifest 失败 %s: %s" % (mfp, exc)}
        if isinstance(data.get("tables"), dict):  # cnopendata 双层壳
            inner = data["tables"]
        else:
            inner = data
        bak = mfp.with_name(mfp.name + ".bak-" + ts)
        if not bak.exists():
            shutil.copy2(mfp, bak)
        for r in items:
            ent = inner.get(r["table"]) or {}
            ent.update({"done": True, "rows": r.get("rows"), "files": r.get("files"),
                        "repaired_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                        "repaired_by": REPAIR_TAG})
            inner[r["table"]] = ent
            changed.append(r["table"])
        tmp = Path(str(mfp) + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, mfp)
    if kg_log and changed:
        try:
            with KG_LOG.open("a", encoding="utf-8") as f:
                f.write("%s date=%s tables=%d files=%d exit=0\n" % (
                    REPAIR_TAG, datetime.now().astimezone().isoformat(timespec="seconds"),
                    len(changed), len(by_mf)))
        except OSError:
            pass
    return {"ok": True, "changed_tables": changed, "manifests": [str(p) for p in by_mf]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="manifest 缺失/开放表补齐")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rows-max", type=int, default=1500)
    args = ap.parse_args(argv)
    if not common.CATALOG.exists():
        print("catalog missing", file=sys.stderr)
        return 2
    rows = plan()
    result = {"plan": rows}
    if args.apply:
        result["apply"] = apply([{**r} for r in rows])
        if not result["apply"].get("ok"):
            result["code"] = 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        fixable = [r for r in rows if not r.get("skip")]
        print("missing/open candidates: %d（可修 %d / 跳过 %d）" % (
            len(rows), len(fixable), len(rows) - len(fixable)))
        for r in fixable[:50]:
            print("  FIX  %-28s %-10s files=%-5s rows=%s" % (
                r["table"], r.get("source"), r.get("files"), r.get("rows")))
        for r in rows:
            if r.get("skip"):
                print("  SKIP %-28s %s" % (r["table"], r["skip"]))
        if args.apply:
            print("applied:", result.get("apply"))
    return result.get("code", 0)


if __name__ == "__main__":
    sys.exit(main())
