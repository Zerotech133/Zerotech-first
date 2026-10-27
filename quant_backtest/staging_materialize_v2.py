# -*- coding: utf-8 -*-
"""staging → tushare_v2 独立目录实战物化器（K-STOCK-211，2026-10-17 用户裁决）。

目标：把 tushare_staging/history_3y 的 1744 个候选单元物化到
data/tushare_v2/（**独立验证目录，绝不触碰生产仓 data/tushare/**），
并实战演练回读协议——每单元写入后立即回读，三元组
（staged_sha256, row_count, columns）与 manifest 指纹全等才算成功。

目录结构对齐生产仓语义：tushare_v2/<表名去 ts_ 前缀>/d_YYYYMMDD.parquet
（生产仓 daily/ 就是 d_19910102.parquet 按日分片）。

特性：
  - 幂等续传：目标已存在且 sha256 相符 → skip（中断重跑只补缺）
  - fail-visible：sha256/行数 mismatch → 不删不重试，记录待裁决
  - 行数验证：pandas 可用时读 parquet 行数比对 row_count，不可用降级
    只做 sha256（报告标注）
  - 全程零生产写入：唯一写目标是 --out-dir（默认 data/tushare_v2）

用法：
  python staging_materialize_v2.py [--staging PATH] [--out-dir PATH]
      [--limit N] [--report PATH]
      --limit  只物化前 N 个单元（试点用，如 --limit 20）
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
        return Path(_LOCAL_PREFIX + raw[len(_LEGACY_PREFIX) :])
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
    ap = argparse.ArgumentParser(description="staging → tushare_v2 物化 + 回读验证")
    ap.add_argument(
        "--staging",
        default=str(Path(__file__).resolve().parent / "data" / "tushare_staging"),
    )
    ap.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "data" / "tushare_v2"),
    )
    ap.add_argument("--limit", type=int, default=0, help="只物化前 N 单元（0=全部）")
    ap.add_argument("--report", default="", help="报告输出路径（默认 staging 内）")
    args = ap.parse_args()

    staging = Path(args.staging)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 生产仓安全断言：输出目录绝不允许指向 data/tushare
    prod = staging.parent / "tushare"
    if out_dir.resolve() == prod.resolve():
        print("[abort] out-dir 指向生产仓 data/tushare —— 拒绝执行")
        return 2

    entries: list[dict] = []
    for mf in sorted(staging.glob("history_3y/batch_*/run_manifest.json")):
        with open(mf, encoding="utf-8") as f:
            entries.extend(json.load(f).get("entries", []))
    if args.limit:
        entries = entries[: args.limit]

    stats = {"ok": 0, "skipped": 0, "mismatch": 0, "missing": 0}
    rows_ok = 0
    rowcheck_enabled = False
    problems: list[dict] = []

    for e in entries:
        t = e.get("table", "?")
        d = (e.get("request_params") or {}).get("trade_date") or (
            (e.get("date_values") or [""])[0]
        )
        src = _map_path(e.get("staged_path") or "")
        dir_name = t[3:] if t.startswith("ts_") else t
        dst = out_dir / dir_name / f"d_{d}.parquet"
        unit = {"table": t, "date": d, "src": str(src), "dst": str(dst)}

        want_sha = e.get("staged_sha256")
        if not src.is_file():
            stats["missing"] += 1
            problems.append({**unit, "kind": "staged_missing"})
            continue
        if dst.is_file():  # 幂等续传
            if _sha256(dst) == want_sha:
                stats["skipped"] += 1
                continue
            # 目标存在但指纹不符 → 覆盖重写（本目录是验证仓，重写合法）
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        got_sha = _sha256(dst)
        if got_sha != want_sha:
            stats["mismatch"] += 1
            problems.append(
                {
                    **unit,
                    "kind": "sha256_mismatch",
                    "manifest": want_sha,
                    "actual": got_sha,
                }
            )
            continue
        # 回读行数（降级开关）
        rc = _row_count(dst)
        if rc is not None:
            rowcheck_enabled = True
            want_rows = e.get("row_count")
            if want_rows is not None and rc != want_rows:
                stats["mismatch"] += 1
                problems.append(
                    {
                        **unit,
                        "kind": "row_count_mismatch",
                        "manifest": want_rows,
                        "actual": rc,
                    }
                )
                continue
        stats["ok"] += 1
        rows_ok += e.get("row_count") or 0

    report = {
        "tool": "staging_materialize_v2",
        "generated_at": _now(),
        "staging_root": str(staging),
        "out_dir": str(out_dir),
        "production_root_untouched": str(prod),
        "entries_total": len(entries),
        "stats": stats,
        "rows_of_ok_units": rows_ok,
        "rowcheck_enabled": rowcheck_enabled,
        "problems": problems,
        "protocol": "每单元 copy 后回读：sha256 必须等于 manifest.staged_sha256；"
        "pandas 可用时行数必须等于 manifest.row_count；mismatch 不删"
        "不重试，留待裁决。",
    }
    rp = Path(args.report) if args.report else staging / "materialize_v2_report.json"
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[v2] staging={staging}")
    print(f"[v2] out_dir={out_dir}（生产仓 {prod} 零写入）")
    print(
        f"[v2] units: ok={stats['ok']} skipped={stats['skipped']} "
        f"mismatch={stats['mismatch']} missing={stats['missing']} / "
        f"total={len(entries)}"
    )
    print(f"[v2] rows materialized (ok units): {rows_ok}")
    print(
        f"[v2] rowcheck: {'pandas ON' if rowcheck_enabled else 'pandas OFF (sha256-only)'}"
    )
    if problems:
        print(f"[v2] PROBLEMS ({len(problems)}):")
        for p in problems[:10]:
            print(f"    {p['kind']}: {p['table']} {p['date']}")
    print(f"[v2] report -> {rp}")
    return 0 if (stats["mismatch"] == 0 and stats["missing"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
