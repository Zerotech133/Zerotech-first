# -*- coding: utf-8 -*-
"""annual 批 full_history 1820 单元 → 生产仓正式受控物化器（K-STOCK-211，2026-10-17）。

批准记录：用户 2026-10-17「好的，执行正式物化」（继 history_3y 1744 单元
`73ee5ff` 之后的第二单：full_history 候选，closure 0/1820 的解锁对象）。

候选形态（与 history_3y 集中 manifest 不同）：49 个 annual_*_batch_* 目录
平铺 `ts_<table>_<date>.parquet` + 同名 `.receipt.json`（含 staged_sha256 /
row_count / captured_at_utc / production_write=false / status=candidate_ready）。

表集合：ts_daily_basic 910 + ts_margin_detail 910（各 20230103..20261009），
其中 20230307.. 与已发布 history_3y 重叠（同 sha → 幂等 skip），真实增量 =
20230103..20230306 每表约 40 个早期交易日。

本批发布策略（与 staging_materialize_prod 的差异，依据 2026-10-17 对账）：
  - **只填空不覆盖**：目标不存在 → copy + sha256/行数双回读（协议同前）
  - 目标存在且 sha 相符 → 幂等 skip
  - 目标存在但 sha 不符 → **conflict_preserved 保生产版**（annual 批整体是
    2026-10-11 早期 capture；已核验 2 个冲突单元 d_20261008/09 margin_detail
    的生产版来自更晚的 10-12 capture 且行数更全 4455 vs 2001——早期 capture
    不完整，覆盖是倒退）。双方 sha + captured_at 全量记录留证。

发布锁：与 staging_materialize_prod 相同协议（持有-释放，finally 兜底）。
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
    try:
        import pandas as pd  # type: ignore
        return int(pd.read_parquet(path).shape[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="annual 批 full_history → 生产仓受控物化")
    ap.add_argument("--staging", default=str(Path(__file__).resolve().parent
                                              / "data" / "tushare_staging"))
    ap.add_argument("--prod", default=str(Path(__file__).resolve().parent
                                          / "data" / "tushare"))
    ap.add_argument("--pattern", default="annual_*_batch_*",
                    help="staging 内 receipt 目录 glob（tail_fill 单用 tail_fill/*）")
    ap.add_argument("--report", default="",
                    help="报告输出路径；空 = 默认时间戳文件名（防覆盖）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--allow-overwrite", action="store_true",
                    help="sha 不符时启用 quarantine 覆盖：生产原件移 "
                         "quarantine_prod_overwrite/<table>/ 留证后写新件"
                         "（默认关闭=conflict_preserved 保生产版；用于残缺/"
                         "脏单元修复单，如 index_daily d_20230103 截断、"
                         "margin_detail d_20260105 脏行）")
    args = ap.parse_args()

    staging = Path(args.staging)
    prod = Path(args.prod).resolve()
    lock = prod / ".warehouse-publication.lock"

    if prod.name != "tushare" or not (prod / "_manifest.json").is_file():
        print("[abort] --prod 必须指向生产仓 data/tushare")
        return 2
    receipts = sorted(staging.glob(args.pattern + "/*.receipt.json"))
    if args.limit:
        receipts = receipts[:args.limit]
    if not receipts:
        print(f"[abort] pattern {args.pattern!r} 无 receipt")
        return 2

    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"holder": "staging_materialize_full_history",
                   "acquired_at": _now(), "units": len(receipts),
                   "allow_overwrite": bool(args.allow_overwrite),
                   "approval": "用户 2026-10-17 批准（K-STOCK-211 full_history 单）"},
                  f, ensure_ascii=False, indent=2)

    stats = {"ok": 0, "skipped": 0, "conflict_preserved": 0,
             "overwrite_quarantined": 0,
             "mismatch": 0, "missing": 0}
    rows_ok = 0
    rowcheck_enabled = False
    problems: list[dict] = []
    conflicts: list[dict] = []
    overwrites: list[dict] = []
    per_table: dict[str, dict] = {}

    try:
        for rp in receipts:
            with open(rp, encoding="utf-8") as f:
                e = json.load(f)
            t = e.get("table", "?")
            d = (e.get("request_params") or {}).get("trade_date") or \
                ((e.get("date_values") or [""])[0])
            src = _map_path(e.get("staged_path") or "")
            dir_name = t[3:] if t.startswith("ts_") else t
            dst = prod / dir_name / f"d_{d}.parquet"
            want_sha = e.get("staged_sha256")
            unit = {"table": t, "date": d, "src": str(src), "dst": str(dst)}

            if not src.is_file():
                stats["missing"] += 1
                problems.append({**unit, "kind": "staged_missing"})
                continue

            if dst.is_file():
                got = _sha256(dst)
                if got == want_sha:
                    stats["skipped"] += 1
                    per_table.setdefault(dir_name,
                                         {"ok": 0, "skip": 0,
                                          "conflict": 0})["skip"] += 1
                    continue
                if args.allow_overwrite:
                    # quarantine 覆盖通道：生产原件隔离留证（不删除）后写新件
                    qd = prod / "quarantine_prod_overwrite" / dir_name
                    qd.mkdir(parents=True, exist_ok=True)
                    qf = qd / f"d_{d}__prod_{got[:12]}.parquet"
                    shutil.move(str(dst), qf)
                    shutil.copyfile(src, dst)
                    got2 = _sha256(dst)
                    rc2 = _row_count(dst)
                    if got2 != want_sha or (rc2 is not None
                                            and rc2 != e.get("row_count")):
                        # 覆盖失败回滚：隔离件还原，勿留半态
                        shutil.move(str(qf), str(dst))
                        stats["mismatch"] += 1
                        problems.append({**unit,
                                         "kind": "overwrite_rollback",
                                         "prod_sha256": got,
                                         "new_sha256": want_sha})
                        continue
                    stats["overwrite_quarantined"] += 1
                    overwrites.append({**unit, "kind": "overwrite_quarantined",
                                       "prod_sha256": got,
                                       "quarantined_to": str(qf),
                                       "new_sha256": want_sha,
                                       "new_captured_at":
                                           e.get("captured_at_utc")})
                    per_table.setdefault(dir_name,
                                         {"ok": 0, "skip": 0,
                                          "conflict": 0})["ok"] += 1
                    continue
                # 默认策略：sha 不符 → 保生产版，留证（annual=早期 capture）
                stats["conflict_preserved"] += 1
                conflicts.append({**unit, "kind": "conflict_preserved",
                                  "prod_sha256": got,
                                  "annual_sha256": want_sha,
                                  "annual_captured_at":
                                      e.get("captured_at_utc")})
                per_table.setdefault(dir_name,
                                     {"ok": 0, "skip": 0,
                                      "conflict": 0})["conflict"] += 1
                continue

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
            per_table.setdefault(dir_name,
                                 {"ok": 0, "skip": 0, "conflict": 0})["ok"] += 1
    finally:
        ok = stats["mismatch"] == 0 and stats["missing"] == 0
        with open(lock, "w", encoding="utf-8") as f:
            json.dump({"holder": "staging_materialize_full_history",
                       "released_at": _now(),
                       "result": "ok" if ok else "failed", "stats": stats},
                      f, ensure_ascii=False, indent=2)

    report = {
        "tool": "staging_materialize_full_history",
        "generated_at": _now(),
        "staging_root": str(staging),
        "production_root": str(prod),
        "approval": "用户逐单批准（K-STOCK-211；history_3y 73ee5ff / annual "
                    "9b6aeb5 / tail_fill 第三单 ed1205c 第四单 feee32b）",
        "pattern": args.pattern,
        "entries_total": len(receipts),
        "stats": stats,
        "rows_of_ok_units": rows_ok,
        "rowcheck_enabled": rowcheck_enabled,
        "per_table": per_table,
        "conflicts_preserved": conflicts,
        "overwrites_quarantined": overwrites,
        "problems": problems,
        "protocol": "只填空不覆盖：dst 缺失 → copy+sha256+行数双回读；"
                    "sha 相符 → 幂等 skip；sha 不符 → 默认保生产版"
                    "（conflict_preserved 留证）；--allow-overwrite 时走 "
                    "quarantine 覆盖（生产原件移 quarantine_prod_overwrite/"
                    " 留证后写新件，双回读失败自动回滚）。发布锁持有-释放。",
        "allow_overwrite": bool(args.allow_overwrite),
    }
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    rp_out = staging / f"materialize_full_history_report_{stamp}.json" \
        if not args.report else Path(args.report)  # 默认带时间戳防覆盖（证据保全）
    with open(rp_out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[fh] staging={staging}")
    print(f"[fh] production={prod}")
    print(f"[fh] units: ok={stats['ok']} skipped={stats['skipped']} "
          f"conflict_preserved={stats['conflict_preserved']} "
          f"overwrite_quarantined={stats['overwrite_quarantined']} "
          f"mismatch={stats['mismatch']} missing={stats['missing']} / "
          f"total={len(receipts)}")
    print(f"[fh] rows materialized (ok units): {rows_ok}")
    print(f"[fh] rowcheck: {'pandas ON' if rowcheck_enabled else 'pandas OFF'}")
    for tname, s in sorted(per_table.items()):
        print(f"[fh]   {tname:<14} ok={s['ok']:<4} skip={s['skip']:<4} "
              f"conflict={s['conflict']}")
    if conflicts:
        print(f"[fh] CONFLICTS preserved ({len(conflicts)}):")
        for c in conflicts:
            print(f"    {c['table']} d_{c['date']}: prod {c['prod_sha256'][:12]}"
                  f" vs annual {c['annual_sha256'][:12]}"
                  f" (annual captured {c['annual_captured_at']})")
    if overwrites:
        print(f"[fh] OVERWRITES quarantined ({len(overwrites)}):")
        for o in overwrites:
            print(f"    {o['table']} d_{o['date']}: prod {o['prod_sha256'][:12]}"
                  f" -> quarantine, new {o['new_sha256'][:12]}")
    if problems:
        print(f"[fh] PROBLEMS ({len(problems)}):")
        for p in problems[:10]:
            print(f"    {p['kind']}: {p['table']} {p['date']}")
    print(f"[fh] report -> {rp_out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
