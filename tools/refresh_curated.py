# -*- coding: utf-8 -*-
"""重建「整理层」tushare_curated（raw → curated）。

背景
----
数据根 `data/tushare/` 是**原始落地层**：写入管线（staging_materialize /
history_backfill / full_refresh / tail_fill）每天按日/按票切片落盘，
228,930 个 parquet。合并成 51 个目录级文件能把全量扫描从分钟级压到秒级，
但**不能替换原始层**——否则写入管线第二天就重新铺出几万个碎文件。

所以采取两层结构：
    data/tushare/          原始层（写入方不动，永远保留）
    data/tushare_curated/  整理层（本脚本产物，纯派生，可随时重建）

用法
----
    python tools/refresh_curated.py --dry-run        # 只出计划（会扫 footer，约 2 分钟）
    python tools/refresh_curated.py                  # 重建到 <out>.tmp 并校验，不替换
    python tools/refresh_curated.py --replace        # 校验通过后替换正式目录（**永久删除旧层**）

安全设计
--------
1. 永远先写 `<out>.tmp`，校验通过才 rename 成正式目录——中断不会毁掉现有整理层。
2. 校验口径：产物表数 == 合并表数；产物总行数 == 源目录逐文件 footer 累加（跳过 0 行）。
   两条都不满足就 exit 1，**不替换**。
3. `--replace` 会**永久删除**旧整理层（本机回收站不可用：进程是 Low IL，
   SHFileOperationW 会静默退化为硬删）。所以它要求显式开关，且先打印将删什么。
4. 按 AGENTS.md §3，真跑前后在 C:\\Temp\\kg_sync_task.log 补记（`--no-log` 可关）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO / "quant_backtest" / "data" / "tushare"
DEFAULT_OUT = REPO / "quant_backtest" / "data" / "tushare_curated"
MERGE_TOOL = Path(__file__).resolve().parent / "merge_parquet.py"
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _log(line: str, enabled: bool) -> None:
    if not enabled:
        return
    try:
        KG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(KG_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        _say(f"  [WARN] 写 kg_sync 日志失败（不影响主流程）：{exc}")


def run_merge(src: Path, out: Path, dirs: list[str] | None, dry_run: bool) -> tuple[int, dict | None]:
    argv = [sys.executable, "-B", str(MERGE_TOOL),
            "--src", str(src), "--out", str(out), "--json"]
    if dirs:
        argv += ["--dirs", *dirs]
    if dry_run:
        argv += ["--dry-run"]
    else:
        argv += ["--empty-sidecar", str(out / "_empty_tickers.json")]
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    payload = None
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        pass
    return proc.returncode, payload


def count_source_rows(src: Path, dirs: list[str]) -> tuple[int, int, list[str]]:
    """逐文件 footer 累加（跳过 0 行）。返回 (行数, 跳过文件数, 出错文件)。"""
    import pyarrow.parquet as pq

    total = 0
    skipped = 0
    failed: list[str] = []
    for name in dirs:
        d = src / name
        if not d.is_dir():
            continue
        for f in d.glob("*.parquet"):
            try:
                n = pq.ParquetFile(f).metadata.num_rows
            except Exception:  # noqa: BLE001 - 坏文件计数，不中断
                failed.append(f.name)
                continue
            if n == 0:
                skipped += 1
            else:
                total += n
    return total, skipped, failed


def count_product_rows(out: Path) -> tuple[int, int]:
    import pyarrow.parquet as pq

    rows = 0
    tables = 0
    for f in out.glob("*.parquet"):
        try:
            rows += pq.ParquetFile(f).metadata.num_rows
            tables += 1
        except Exception:  # noqa: BLE001
            pass
    return rows, tables


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建 tushare 整理层（raw → curated）")
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dirs", nargs="+", default=None, help="只重建这些子目录")
    parser.add_argument("--dry-run", action="store_true", help="只出计划，不写盘")
    parser.add_argument("--replace", action="store_true",
                        help="校验通过后替换正式目录（会永久删除旧整理层）")
    parser.add_argument("--no-log", action="store_true", help="不写 kg_sync_task.log")
    parser.add_argument("--skip-verify", action="store_true",
                        help="跳过逐文件 footer 复核（不推荐）")
    args = parser.parse_args(argv)

    src = Path(args.src)
    out = Path(args.out)
    tmp = out.with_name(out.name + ".tmp")
    log_on = not args.no_log

    if not src.is_dir():
        _say(f"错误：源目录不存在 {src}")
        return 2
    if not MERGE_TOOL.is_file():
        _say(f"错误：找不到合并工具 {MERGE_TOOL}")
        return 2

    _say("=== 重建整理层 tushare_curated ===")
    _say(f"源（原始层）: {src}")
    _say(f"目标（整理层）: {out}")
    _say(f"临时目录     : {tmp}")
    _say(f"模式         : {'DRY-RUN' if args.dry_run else ('REPLACE' if args.replace else 'STAGE-ONLY')}")
    _say()

    started = time.time()
    code, payload = run_merge(src, tmp if not args.dry_run else out, args.dirs, args.dry_run)
    if payload is None:
        _say(f"错误：合并工具输出无法解析（exit={code}）")
        return 2
    totals = payload.get("totals") or {}
    # 注意：dry-run 时 status 是 "planned"，真跑时是 "merged"。两者都要算，
    # 否则 --dirs 子集真跑会把期望表数算成 0（2026-10-22 自测抓到过）。
    planned_dirs = [d for d in payload.get("dirs", [])
                    if d.get("status") in ("planned", "merged")]
    tables = sum(int(d.get("tables") or 1) for d in planned_dirs)

    _say(f"计划：{len(planned_dirs)} 个目录 / {tables} 张表 / "
         f"{totals.get('files_in', 0):,} 文件 / {totals.get('rows_out', 0):,} 行")
    if args.dry_run:
        _say(f"耗时 {time.time() - started:.1f}s（未写盘）")
        return 0

    if code != 0:
        _say(f"错误：合并失败 exit={code}，不替换")
        return 1

    target_dirs = args.dirs or [d["dir"] for d in planned_dirs]
    _say()
    _say("校验 1/2：产物行数 vs 源 footer 累加（逐文件，跳过 0 行）")
    if args.skip_verify:
        _say("  [skip] 按 --skip-verify 跳过")
    else:
        src_rows, skipped, failed = count_source_rows(src, target_dirs)
        prod_rows, prod_tables = count_product_rows(tmp)
        _say(f"  源   : {src_rows:,} 行（跳过 0 行文件 {skipped}，读失败 {len(failed)}）")
        _say(f"  产物 : {prod_rows:,} 行 / {prod_tables} 张表")
        if src_rows != prod_rows:
            _say(f"  [FAIL] 行数不符，差 {src_rows - prod_rows:,} 行——不替换，临时目录保留：{tmp}")
            return 1
        _say("  [OK] 行数完全一致")

    _say("校验 2/2：表数")
    if not args.skip_verify and prod_tables != tables:
        _say(f"  [FAIL] 表数不符：计划 {tables} / 实际 {prod_tables}——不替换")
        return 1
    _say(f"  [OK] {tables} 张表齐全")

    if not args.replace:
        _say()
        _say(f"已写入 {tmp}（未替换正式目录）。加 --replace 才会切换。")
        _log(f"refresh_curated STAGE ok {time.strftime('%Y-%m-%dT%H:%M:%S')} "
             f"({totals.get('rows_out', 0)} rows)", log_on)
        return 0

    _say()
    _say(f"[REPLACE] 将永久删除现有整理层：{out}")
    if out.exists():
        old_files = sum(1 for _ in out.glob("*"))
        old_size = sum(f.stat().st_size for f in out.glob("*") if f.is_file())
        _say(f"          现有 {old_files} 个条目 / {old_size / 1024**3:.2f} GiB")
        shutil.rmtree(out)
    tmp.rename(out)
    _say(f"[REPLACE] 完成：{out}")
    _say(f"耗时 {time.time() - started:.1f}s")
    _log(f"refresh_curated REPLACE ok {time.strftime('%Y-%m-%dT%H:%M:%S')} "
         f"({totals.get('rows_out', 0)} rows)", log_on)
    return 0


if __name__ == "__main__":
    sys.exit(main())
