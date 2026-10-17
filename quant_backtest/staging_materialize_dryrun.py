# -*- coding: utf-8 -*-
"""staging 物化 dry-run 计划器（K-STOCK-211 registry 重发布准备，2026-10-17）。

只读工具：读 tushare_staging 候选资产 + 只读对照生产仓（data/tushare），
输出「若批准物化将写哪些生产文件」的计划报告。**零生产写入**（本工具
没有任何写生产仓的代码路径；唯一输出是 staging 内的报告 JSON）。

数据源（staging）：
  history_3y/batch_*/run_manifest.json —— 9 批，每 entry 自带三层指纹
    （body_sha256 / raw_sha256 / staged_sha256）+ row_count + columns
    + duplicate_keys + null_primary_key，capture 侧 production_write=false
  annual_20XX_batch_XXX_summary.json —— 52 份年报批次登记
  history/recent30/*.parquet + recent30_summary.json —— 近 30 天快照
  runstore_candidate_closure.json —— run 3f60ce6b 总账（blocked 状态）
  tushare_qualification_candidate.json —— qualification_control gate

指纹/回读协议（为未来正式物化设计，本工具实现前两步）：
  1. 指纹 = (staged_sha256, row_count, columns 签名)。capture 时已算，
     dry-run 抽样重算 sha256 对账 → 检测 staging 文件损坏/篡改。
  2. 写前对照 = 生产 d_YYYYMMDD.parquet 已存在则计为「覆盖写」，否则
     「新增写」——覆盖单元单独列出供人工复核。
  3. 回读验证（正式物化时执行，本工具不写故不执行）：写入后立即读回
     parquet，重算 sha256 + 行数，与 staging 指纹三元组全等才算成功；
     不等 → 标记 mismatch 待裁决，不自动重试。

用法：
  python staging_materialize_dryrun.py [--staging PATH] [--verify-all]
      --staging   staging 根目录（默认 data/tushare_staging）
      --verify-all  对全部 staged parquet 重算 sha256（默认每表抽 2 个）

旧机路径映射：manifest 里 staged_path 是 E:\\老田的AI助手\\... 旧机绝对路径，
本机等价物为 c:\\aios\\...（junction C:\\aios -> C:\\项目\\老田的AI助手）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

_LEGACY_PREFIX = "E:\\老田的AI助手"
_LOCAL_PREFIX = "C:\\aios"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _map_path(raw: str) -> Path:
    """旧机 E:\\ 绝对路径 → 本机路径；其余原样。"""
    if raw and raw.lower().startswith(_LEGACY_PREFIX.lower()):
        return Path(_LOCAL_PREFIX + raw[len(_LEGACY_PREFIX):])
    return Path(raw)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifests(staging: Path) -> list[dict]:
    """收集 history_3y/batch_*/run_manifest.json 的 entries（带批次号）。"""
    entries: list[dict] = []
    for mf in sorted(staging.glob("history_3y/batch_*/run_manifest.json")):
        batch = mf.parent.name
        with open(mf, encoding="utf-8") as f:
            data = json.load(f)
        for e in data.get("entries", []):
            e["_batch"] = batch
            entries.append(e)
    return entries


def _production_target(prod_root: Path, table: str, trade_date: str) -> Path:
    """staging 表名 ts_daily + 交易日 → 生产 daily/d_YYYYMMDD.parquet。"""
    dir_name = table[3:] if table.startswith("ts_") else table
    return prod_root / dir_name / f"d_{trade_date}.parquet"


def main() -> int:
    ap = argparse.ArgumentParser(description="staging 物化 dry-run（零生产写入）")
    ap.add_argument("--staging", default=str(Path(__file__).resolve().parent
                                              / "data" / "tushare_staging"))
    ap.add_argument("--verify-all", action="store_true",
                    help="全量重算 staged parquet sha256（默认每表抽 2 个）")
    args = ap.parse_args()

    staging = Path(args.staging)
    prod_root = staging.parent / "tushare"  # data/tushare（只读对照）
    report: dict = {
        "tool": "staging_materialize_dryrun",
        "generated_at": _now(),
        "staging_root": str(staging),
        "production_root_readonly": str(prod_root),
        "production_write": False,
    }

    # ── gate 状态（只登记，不代跑裁决）──
    gate: dict = {}
    qc_path = staging / "tushare_qualification_candidate.json"
    if qc_path.is_file():
        with open(qc_path, encoding="utf-8") as f:
            qc = json.load(f)
        gate = {
            "qualification_control": qc.get("qualification_control"),
            "review_required": qc.get("review_required"),
            "global_activation": qc.get("global_activation"),
            "production_write": qc.get("production_write"),
        }
    closure: dict = {}
    cl_path = staging / "runstore_candidate_closure.json"
    if cl_path.is_file():
        with open(cl_path, encoding="utf-8") as f:
            cl = json.load(f)
        closure = {
            "run_id": cl.get("run_id"),
            "total_units": cl.get("total_units"),
            "completed_units": cl.get("completed_units"),
            "blocked_reason": cl.get("blocked_reason"),
        }
    report["gate"] = gate
    report["closure"] = closure

    # ── history_3y manifest 聚合 ──
    entries = _load_manifests(staging)
    tables: dict[str, dict] = {}
    hash_fail: list[dict] = []
    missing_files: list[str] = []
    sample_by_table: dict[str, list[str]] = defaultdict(list)

    for e in entries:
        t = e.get("table", "?")
        tbl = tables.setdefault(t, {
            "units": 0, "rows": 0, "dup_keys_total": 0,
            "null_pk_total": 0, "dates": [], "columns": None,
            "staged_missing": 0, "prod_already": 0, "prod_new": 0,
            "prod_new_rows": 0,
        })
        tbl["units"] += 1
        tbl["rows"] += e.get("row_count") or 0
        tbl["dup_keys_total"] += e.get("duplicate_keys") or 0
        npk = e.get("null_primary_key") or {}
        tbl["null_pk_total"] += sum(v for v in npk.values() if isinstance(v, int))
        d = (e.get("request_params") or {}).get("trade_date") or \
            ((e.get("date_values") or [""])[0])
        if d:
            tbl["dates"].append(d)
        if tbl["columns"] is None:
            tbl["columns"] = e.get("columns") or []
        elif e.get("columns") and e["columns"] != tbl["columns"]:
            tbl.setdefault("column_drift", []).append(e["_batch"])

        sp = _map_path(e.get("staged_path") or "")
        if not sp.is_file():
            tbl["staged_missing"] += 1
            missing_files.append(str(sp))
            continue
        # 抽样/全量 sha256 对账
        want = e.get("staged_sha256")
        if want:
            if args.verify_all or len(sample_by_table[t]) < 2:
                got = _sha256(sp)
                sample_by_table[t].append(str(sp))
                if got != want:
                    hash_fail.append({"path": str(sp), "manifest": want,
                                      "actual": got})
        # 生产对照（只读）
        target = _production_target(prod_root, t, d) if d else None
        if target is not None:
            if target.is_file():
                tbl["prod_already"] += 1
            else:
                tbl["prod_new"] += 1
                tbl["prod_new_rows"] += e.get("row_count") or 0

    for t, tbl in tables.items():
        ds = sorted(set(tbl.pop("dates")))
        tbl["date_min"] = ds[0] if ds else None
        tbl["date_max"] = ds[-1] if ds else None
        tbl["distinct_dates"] = len(ds)
        tbl["hash_sampled"] = len(sample_by_table.get(t, []))
    report["history_3y"] = {
        "entries": len(entries),
        "tables": dict(sorted(tables.items())),
        "sha256_mismatch": hash_fail,
        "staged_file_missing_count": len(missing_files),
        "staged_file_missing_sample": missing_files[:10],
    }

    # ── annual / recent30 辅助登记（轻量）──
    annuals = sorted(staging.glob("annual_*_batch_*_summary.json"))
    ann_info = {"files": len(annuals), "production_write_false": 0,
                "dates_total": 0, "tables": set()}
    for af in annuals:
        with open(af, encoding="utf-8") as f:
            a = json.load(f)
        if a.get("production_write") is False:
            ann_info["production_write_false"] += 1
        ann_info["dates_total"] += len(a.get("dates") or [])
        for k in (a.get("tables") or {}):
            ann_info["tables"].add(k if isinstance(k, str) else str(k))
    ann_info["tables"] = sorted(ann_info["tables"])
    report["annual_summaries"] = ann_info

    r30_dir = staging / "history" / "recent30"
    r30_files = sorted(r30_dir.glob("*.parquet")) if r30_dir.is_dir() else []
    report["recent30"] = {"parquet_files": len(r30_files)}

    # ── 计划汇总（若批准物化的写入面）──
    new_units = sum(t["prod_new"] for t in tables.values())
    new_rows = sum(t["prod_new_rows"] for t in tables.values())
    overwrite_units = sum(t["prod_already"] for t in tables.values())
    report["plan_if_approved"] = {
        "new_files": new_units, "new_rows": new_rows,
        "overwrite_files": overwrite_units,
        "note": "overwrite = 生产 d_YYYYMMDD.parquet 已存在的单元，需人工复核"
                "替换语义（增量合并 vs 整文件替换）后方可执行",
    }
    report["readback_protocol"] = (
        "正式物化时：写入后立即读回 parquet，重算 sha256+行数，与 staging 指纹"
        "（staged_sha256, row_count, columns）三元组全等才算成功；不等 → 标记 "
        "mismatch 待裁决，不自动重试。本 dry-run 已完成写前指纹对账（抽样）。")

    out = staging / "materialize_dryrun_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ── 控制台摘要 ──
    print(f"[dry-run] staging: {staging}")
    print(f"[dry-run] gate: control={gate.get('qualification_control')} "
          f"review_required={gate.get('review_required')}")
    print(f"[dry-run] closure: run={closure.get('run_id')} "
          f"units={closure.get('completed_units')}/{closure.get('total_units')} "
          f"blocked={closure.get('blocked_reason')}")
    print(f"[dry-run] history_3y: entries={len(entries)} "
          f"tables={len(tables)} sha256_mismatch={len(hash_fail)} "
          f"staged_missing={len(missing_files)}")
    for t, tbl in sorted(tables.items()):
        print(f"    {t:24s} units={tbl['units']:4d} rows={tbl['rows']:8d} "
              f"dates={tbl['date_min']}..{tbl['date_max']} "
              f"prod_new={tbl['prod_new']:4d} overwrite={tbl['prod_already']:4d} "
              f"dup={tbl['dup_keys_total']} nullpk={tbl['null_pk_total']}")
    print(f"[dry-run] annual: {ann_info['files']} summaries, "
          f"pw_false={ann_info['production_write_false']}, "
          f"dates={ann_info['dates_total']} | recent30 parquet: "
          f"{len(r30_files)}")
    print(f"[dry-run] plan_if_approved: new_files={new_units} "
          f"new_rows={new_rows} overwrite_files={overwrite_units}")
    print(f"[dry-run] report -> {out}")
    print("[dry-run] 生产仓零写入（production_write=False 全程）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
