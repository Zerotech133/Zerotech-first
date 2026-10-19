# -*- coding: utf-8 -*-
"""staging → 生产仓 data/tushare 正式受控物化器（K-STOCK-211 任务 5，2026-10-17）。

批准记录：用户 2026-10-17「好的，执行正式物化」。gate 三重锁解禁条件全部
满足——dry-run 对账 0 差异（2026-10-17）、tushare_v2 实战 1744/1744 三元组
全等（staging_materialize_v2 协议同源）、旧写者侦查零重叠（活跃写者
TushareSync/KgDailySync 与目标 6 表交集为空）、生产 Git 基线在位
（34c4ff4 + e75451e，_manifest.json 可 diff 审计、可回滚）。

协议（与 staging_materialize_v2 完全一致，实战验证过）：
  - 每单元 copy 后立即回读：sha256 == manifest.staged_sha256 且
    pandas 可用时行数 == manifest.row_count 才算成功
  - 幂等续传：目标已存在且 sha256 相符 → skip（中断重跑只补缺）
  - 覆盖保护（生产仓特有）：目标存在但 sha 不符 → 原文件先移入
    staging/quarantine_prod_overwrite/<table>/ 留证（含双方 sha），
    再写入 staging 新版；quarantine 内文件不删，留待裁决
  - fail-visible：mismatch 不删不重试，记录待裁决
  - 发布锁：物化期间持有 data/tushare/.warehouse-publication.lock
    （写入持锁 JSON，结束时改写为释放记录，文件保留）

预审结论（staging_materialize_dryrun.py 2026-10-17 重跑）：
  6 表 1744 单元：daily_basic 870 / margin_detail 870（20230307..20261009
  全新文件）+ daily/adj_factor/index_daily/moneyflow 各 1 单元（20230103
  快照，其中 daily 与 adj_factor 为 overwrite=2）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

_LEGACY_PREFIX = "C:\\项目\\老田的AI助手"
_LOCAL_PREFIX = "C:\\aios"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _map_path(raw: str) -> Path:
    if raw and raw.lower().startswith(_LEGACY_PREFIX.lower()):
        return Path(_LOCAL_PREFIX + raw[len(_LEGACY_PREFIX):])
    return Path(raw)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_count(path: Path) -> int | None:
    """读 parquet 行数；无 pandas/pyarrow 时返回 None（降级）。"""
    try:
        import pandas as pd  # type: ignore
        return int(pd.read_parquet(path).shape[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="staging → 生产仓正式受控物化")
    ap.add_argument("--staging", default=str(Path(__file__).resolve().parent
                                              / "data" / "tushare_staging"))
    ap.add_argument("--prod", default=str(Path(__file__).resolve().parent
                                          / "data" / "tushare"))
    ap.add_argument("--limit", type=int, default=0, help="只物化前 N 单元（0=全部）")
    ap.add_argument("--report", default="", help="报告输出路径（默认 staging 内）")
    args = ap.parse_args()

    staging = Path(args.staging)
    prod = Path(args.prod).resolve()
    quarantine = staging / "quarantine_prod_overwrite"
    lock = prod / ".warehouse-publication.lock"

    # 生产仓断言（与 v2 相反：必须指向生产仓，且 staging 结构在位）
    if prod.name != "tushare" or not (prod / "_manifest.json").is_file():
        print("[abort] --prod 必须指向带 _manifest.json 的 data/tushare 生产仓")
        return 2
    if not (staging / "history_3y").is_dir():
        print("[abort] staging history_3y 不存在")
        return 2

    entries: list[dict] = []
    for mf in sorted(staging.glob("history_3y/batch_*/run_manifest.json")):
        with open(mf, encoding="utf-8") as f:
            entries.extend(json.load(f).get("entries", []))
    if args.limit:
        entries = entries[:args.limit]
    if not entries:
        print("[abort] 无 staging entries")
        return 2

    # 发布锁：写入持锁记录
    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"holder": "staging_materialize_prod",
                   "acquired_at": _now(), "units": len(entries),
                   "approval": "用户 2026-10-17 批准（K-STOCK-211 任务 5）"},
                  f, ensure_ascii=False, indent=2)

    stats = {"ok": 0, "skipped": 0, "overwritten": 0, "mismatch": 0, "missing": 0}
    rows_ok = 0
    rowcheck_enabled = False
    problems: list[dict] = []
    overwrites: list[dict] = []
    per_table: dict[str, dict] = {}

    try:
        for e in entries:
            t = e.get("table", "?")
            d = (e.get("request_params") or {}).get("trade_date") or \
                ((e.get("date_values") or [""])[0])
            src = _map_path(e.get("staged_path") or "")
            dir_name = t[3:] if t.startswith("ts_") else t
            dst = prod / dir_name / f"d_{d}.parquet"
            unit = {"table": t, "date": d, "src": str(src), "dst": str(dst)}
            want_sha = e.get("staged_sha256")

            if not src.is_file():
                stats["missing"] += 1
                problems.append({**unit, "kind": "staged_missing"})
                continue

            if dst.is_file():  # 幂等 / 覆盖裁决
                got = _sha256(dst)
                if got == want_sha:
                    stats["skipped"] += 1
                    per_table.setdefault(dir_name, {"ok": 0, "skip": 0,
                                                    "overwrite": 0})["skip"] += 1
                    continue
                # 生产覆盖保护：原件进 quarantine 留证，再写新版
                qdir = quarantine / dir_name
                qdir.mkdir(parents=True, exist_ok=True)
                qdst = qdir / f"d_{d}.parquet"
                shutil.move(str(dst), str(qdst))
                stats["overwritten"] += 1
                overwrites.append({**unit, "kind": "prod_overwrite_quarantined",
                                   "prod_old_sha256": got,
                                   "quarantine_path": str(qdst),
                                   "new_sha256": want_sha})

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            got_sha = _sha256(dst)
            if got_sha != want_sha:
                stats["mismatch"] += 1
                problems.append({**unit, "kind": "sha256_mismatch",
                                 "manifest": want_sha, "actual": got_sha})
                continue
            rc = _row_count(dst)
            if rc is not None:
                rowcheck_enabled = True
                want_rows = e.get("row_count")
                if want_rows is not None and rc != want_rows:
                    stats["mismatch"] += 1
                    problems.append({**unit, "kind": "row_count_mismatch",
                                     "manifest": want_rows, "actual": rc})
                    continue
            stats["ok"] += 1
            rows_ok += e.get("row_count") or 0
            pt = per_table.setdefault(dir_name, {"ok": 0, "skip": 0,
                                                 "overwrite": 0})
            pt["ok"] += 1
            if any(o["dst"] == str(dst) for o in overwrites):
                pt["overwrite"] += 1
    finally:
        # 发布锁：无论成败都写释放记录（失败时标注 failed）
        ok = stats["mismatch"] == 0 and stats["missing"] == 0
        with open(lock, "w", encoding="utf-8") as f:
            json.dump({"holder": "staging_materialize_prod",
                       "released_at": _now(), "result": "ok" if ok else "failed",
                       "stats": stats}, f, ensure_ascii=False, indent=2)

    report = {
        "tool": "staging_materialize_prod",
        "generated_at": _now(),
        "staging_root": str(staging),
        "production_root": str(prod),
        "approval": "用户 2026-10-17 批准（K-STOCK-211 任务 5 正式受控物化）",
        "git_baseline": "34c4ff4 + e75451e",
        "entries_total": len(entries),
        "stats": stats,
        "rows_of_ok_units": rows_ok,
        "rowcheck_enabled": rowcheck_enabled,
        "per_table": per_table,
        "overwrites": overwrites,
        "problems": problems,
        "protocol": "每单元 copy 后回读：sha256 == manifest.staged_sha256；"
                    "pandas 行数 == manifest.row_count；目标已存在且 sha 相符 →"
                    " skip；sha 不符 → 原件 quarantine 留证后覆盖；mismatch 不删"
                    "不重试留裁决。发布锁全程持有-释放。",
    }
    rp = Path(args.report) if args.report else staging / "materialize_prod_report.json"
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[prod] staging={staging}")
    print(f"[prod] production={prod}")
    print(f"[prod] units: ok={stats['ok']} skipped={stats['skipped']} "
          f"overwritten={stats['overwritten']} mismatch={stats['mismatch']} "
          f"missing={stats['missing']} / total={len(entries)}")
    print(f"[prod] rows materialized (ok units): {rows_ok}")
    print(f"[prod] rowcheck: {'pandas ON' if rowcheck_enabled else 'pandas OFF'}")
    for tname, s in sorted(per_table.items()):
        print(f"[prod]   {tname:<14} ok={s['ok']:<4} skip={s['skip']:<3} "
              f"overwrite={s['overwrite']}")
    if overwrites:
        print(f"[prod] OVERWRITES quarantined ({len(overwrites)}):")
        for o in overwrites:
            print(f"    {o['table']} d_{o['date']}: old {o['prod_old_sha256'][:12]}"
                  f" -> {o['new_sha256'][:12]} @ {o['quarantine_path']}")
    if problems:
        print(f"[prod] PROBLEMS ({len(problems)}):")
        for p in problems[:10]:
            print(f"    {p['kind']}: {p['table']} {p['date']}")
    print(f"[prod] report -> {rp}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
