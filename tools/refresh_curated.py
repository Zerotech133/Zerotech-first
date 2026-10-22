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
1. 永远先写 `<out>.tmp`，校验通过才切换——中断不会毁掉现有整理层。
2. 校验口径：产物表数 == 合并表数；产物总行数 == 源目录逐文件 footer 累加（跳过 0 行）；
   **且两侧都不能有读不出的文件**（源侧 footer 读失败、产物侧 footer 读失败、merge 报的
   `totals.files_failed` 三者都必须为 0）。任一条不满足就 exit 1，**不替换**。
   （只比两侧总数时，坏文件在两边同时消失会判成 OK——审查 F5。）
3. 路径围栏：`--out` 与 `<out>.tmp` 都对 `--src` 做双向 realpath containment——
   等于 src / 是 src 的祖先 / 在 src 之内，三种都显式报错并 exit 2，不写不删。
   没有这条时 `--out .../quant_backtest/data` 能通过校验，替换时把整个数据根删掉。
4. `--dirs` 与 `--replace` **互斥**：子集重建只写出那几张表，拿去替换整层会把其余表
   全删（51 张变 1 张且校验仍打印 OK），运行期直接 `parser.error` 拒绝。
5. `--replace` 两阶段切换：`out → out.old` → `tmp → out` → 成功后删 `out.old`；
   任何一步失败都把 `out.old` 改回 `out` 并 exit 1（Windows 上目录 rename 会因目录内
   文件被占用而 PermissionError，所以不能先 rmtree 再 rename）。
6. `--replace` 会**永久删除**旧整理层（本机回收站不可用：进程是 Low IL，
   SHFileOperationW 会静默退化为硬删）。所以它要求显式开关，且先打印将删什么。
7. 按 AGENTS.md §3，真跑前后在 C:\\Temp\\kg_sync_task.log 补记（`--no-log` 可关）。
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

# 全量重建时必须排除的目录（否则 exit 1，永远走不到 --replace）：
# `stk_pledge` 一个目录放了两张表（<code>.parquet 7 列 stat / <code>_detail.parquet
# 14 列 detail），merge 判 multi_table 拒绝合并。它总共 10 个文件 / 104 KB，而下游
# 有 6 处按"一只票一个文件"读它，拆表属跨仓重构——所以整理层从一开始就不含它。
# 不排除的话：全量 refresh → dirs_skipped=1 → exit 1 → 无法 --replace，
# 每周 routine 永远是"能暂存、不能切换"的半截状态（2026-10-22 实测）。
DEFAULT_EXCLUDES = ("stk_pledge",)


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


def _tail(text: str, limit: int = 20) -> str:
    """取文本末尾若干行（诊断「输出无法解析」时唯一的线索来源）。"""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join(lines[-limit:])


def _dump_stderr(stderr: str) -> None:
    """把 merge 的 stderr 尾部打出来。

    以前 subprocess 的 stderr 被整包丢弃，失败时只有一句「输出无法解析」，
    现场什么线索都没有（审查 M2）。
    """
    tail = _tail(stderr)
    if not tail:
        _say("  merge stderr 为空（没有更多线索）")
        return
    _say("  merge stderr 尾部：")
    for line in tail.splitlines():
        _say(f"    {line}")


def run_merge(src: Path, out: Path, dirs: list[str] | None,
              dry_run: bool) -> tuple[int, dict | None, str]:
    """调 merge_parquet。返回 (退出码, JSON payload, stderr 全文)。

    ``--overwrite`` 是必须的：默认跑失败后 tmp 里留着上一轮产物，再跑会被
    ``skipped_out_exists`` 挡住，等于永远没法重试（审查 M1）。
    """
    argv = [sys.executable, "-B", str(MERGE_TOOL),
            "--src", str(src), "--out", str(out), "--json"]
    for name in DEFAULT_EXCLUDES:
        argv += ["--exclude", name]
    if dirs:
        argv += ["--dirs", *dirs]
    if dry_run:
        argv += ["--dry-run"]
    else:
        argv += ["--empty-sidecar", str(out / "_empty_tickers.json")]
        argv += ["--overwrite"]
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    payload = None
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        pass
    return proc.returncode, payload, proc.stderr or ""


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


def count_product_rows(out: Path) -> tuple[int, int, list[str]]:
    """产物侧 footer 累加。返回 (行数, 表数, 读失败文件)。

    这里以前把异常直接吞掉：产物里若有读不出的文件，它既不计行也不计表，可能凑巧
    让两侧总数相等而判 OK。现在把失败文件名带出来，由调用方硬失败（审查 F5）。
    """
    import pyarrow.parquet as pq

    rows = 0
    tables = 0
    failed: list[str] = []
    for f in out.glob("*.parquet"):
        try:
            n = pq.ParquetFile(f).metadata.num_rows
        except Exception:  # noqa: BLE001 - 抽出失败清单，交给调用方判死
            failed.append(f.name)
            continue
        rows += n
        tables += 1
    return rows, tables, failed


def _real(path: Path) -> str:
    """规范化 realpath（大小写不敏感比较，Windows 用）。"""
    return os.path.normcase(os.path.realpath(str(path)))


def _is_within(child: Path, parent: Path) -> bool:
    """child 的 realpath 是否落在 parent 之内（跨盘符返回 False）。"""
    try:
        rp_child, rp_parent = _real(child), _real(parent)
        return os.path.commonpath([rp_child, rp_parent]) == rp_parent
    except ValueError:
        return False


def containment_error(label: str, path: Path, src: Path) -> str | None:
    """`path` 与 `src` 的 containment 校验。安全返回 None，否则返回人话。

    三种情况都必须拒绝，且各有各的话（审查 F4）：
      1. path == src            → 直接顶替原始层；
      2. path 是 src 的祖先     → 替换时 rmtree 会把整个数据根连 raw/staging/catalog 删掉；
      3. path 在 src 之内       → 产物混进原始层，污染后续写入管线。
    """
    rp_path, rp_src = _real(path), _real(src)
    if rp_path == rp_src:
        return "%s 与 --src 是同一个目录（%s），会直接覆盖原始层" % (label, src)
    if _is_within(src, path):
        return ("%s 是 --src 的祖先目录（%s 包含 %s）；替换时会把整个数据根"
                "（raw/写入区）一起删掉" % (label, path, src))
    if _is_within(path, src):
        return "%s 落在 --src 之内（%s 在 %s 里）；产物会混进原始层" % (label, path, src)
    return None


def swap_out(out: Path, tmp: Path) -> int:
    """两阶段切换整理层：out → out.old，tmp → out，成功后删 out.old。

    返回 0 成功 / 1 失败（失败时保证 out 仍是替换前的旧层，除非回滚本身也失败）。
    Windows 上目录 rename 会因「目录内有文件被占用」抛 PermissionError，所以
    「rmtree 旧层 + rename 新层」的老写法一旦失手就是旧层已毁、新层未就位（审查 F3）。
    """
    old = out.with_name(out.name + ".old")
    if not tmp.is_dir():
        _say(f"错误：临时目录不存在，拒绝替换：{tmp}")
        return 1
    if old.exists():
        # 上次切换的残骸：不敢自动删（可能是唯一的一份），要人工确认后再跑
        _say(f"错误：残留目录 {old} 已存在，请先人工确认并清掉它，本次不替换")
        return 1

    moved_old = False
    if out.exists():
        try:
            out.rename(old)
        except OSError as exc:
            _say(f"错误：旧层改名为 {old.name} 失败（{type(exc).__name__}: {exc}）")
            _say(f"      整理层原样未动：{out}")
            return 1
        moved_old = True

    try:
        tmp.rename(out)
    except OSError as exc:
        _say(f"错误：新层就位失败（{type(exc).__name__}: {exc}），开始回滚")
        if moved_old:
            try:
                old.rename(out)
                _say(f"  已回滚：{out} 仍是替换前的旧层")
            except OSError as exc2:
                _say(f"  [FAIL] 回滚也失败（{type(exc2).__name__}: {exc2}）！"
                     f"旧层在 {old}，新层在 {tmp}，请人工改名恢复")
                return 1
        return 1

    if moved_old:
        try:
            shutil.rmtree(old)
            _say(f"[REPLACE] 已删除旧层 {old}")
        except OSError as exc:
            _say(f"  [WARN] 旧层已切走但删除失败（{type(exc).__name__}: {exc}）：{old}"
                 f"（不影响新层使用，但下次替换前需人工清掉它）")
    return 0


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
    parser.add_argument("--guard-minutes", type=float, default=10.0,
                        help="kg_sync 日志在这么多分钟内有写入就拒绝开工（默认 10；0 关闭）")
    parser.add_argument("--force", action="store_true",
                        help="无视 kg_sync 活跃守卫（--guard-minutes 0 的显式替代）")
    args = parser.parse_args(argv)

    src = Path(args.src)
    out = Path(args.out)
    tmp = out.with_name(out.name + ".tmp")
    log_on = not args.no_log

    # ---- 硬约束 1（审查 F2）：--dirs 是「子集重建」，--replace 是「整层替换」。
    # 两者合用会把「只有这几张表」的产物顶替整层，其余表被永久删除（51 张 → 1 张），
    # 而校验还照样打印 OK。它们不是同义/互斥的 argparse 选项对，只能运行期拒绝。
    if args.dirs and args.replace:
        parser.error("--dirs 与 --replace 不能同时使用：--dirs 只重建子集（%s），"
                     "--replace 会把整层替换成这个子集、其余表被永久删除。"
                     "整层重建请去掉 --dirs；只想先看子集产物请去掉 --replace。"
                     % " ".join(args.dirs))

    if not src.is_dir():
        _say(f"错误：源目录不存在 {src}")
        return 2

    # ---- 开工守卫（每周 routine 的关键闸门）：kg_sync 正在写原始层时重建，
    # 会读到写了一半的数据集（写一半的 parquet 读不出 footer → F5 会拦，但那时
    # 已经浪费 52 分钟）。所以用「kg_sync 日志最近有写入」作为活跃信号。
    if args.guard_minutes > 0 and not args.force and KG_LOG.is_file():
        try:
            idle = (time.time() - KG_LOG.stat().st_mtime) / 60.0
        except OSError:
            idle = None
        if idle is not None and idle < args.guard_minutes:
            _say(f"拒绝开工：{KG_LOG} 在 {idle:.1f} 分钟前还有写入"
                 f"（阈值 {args.guard_minutes:g} 分钟），kg_sync 可能正在写原始层。")
            _say("  等同步结束后重跑；确认无风险可加 --force（或 --guard-minutes 0）。")
            return 2

    # ---- 硬约束 2（审查 F4）：out / tmp 与 src 的三种重叠关系逐一显式拒绝。
    # 没这条时 `--out ...\quant_backtest\data` 能通过全部校验，替换时 rmtree 会把
    # 整个数据根（raw / staging / catalog）连根删掉。
    for _label, _path in ((f"--out {out}", out), (f"临时目录 {tmp}", tmp)):
        _err = containment_error(_label, _path, src)
        if _err:
            _say(f"错误：{_err}")
            _say("      拒绝执行（exit 2）：未写入、未删除任何文件")
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
    code, payload, merge_stderr = run_merge(
        src, tmp if not args.dry_run else out, args.dirs, args.dry_run)
    if payload is None:
        _say(f"错误：合并工具输出无法解析（exit={code}）")
        _dump_stderr(merge_stderr)
        return 2
    totals = payload.get("totals") or {}
    # 注意：dry-run 时 status 是 "planned"，真跑是 "merged"，缺行的产物是 "partial"。
    # 三种都要算，否则 --dirs 子集真跑会把期望表数算成 0（2026-10-22 自测抓到过），
    # 而 partial 的目录明明写了盘、却会被算成「0 个目录 / 0 张表」，把失败信息也说错。
    planned_dirs = [d for d in payload.get("dirs", [])
                    if d.get("status") in ("planned", "merged", "partial")]
    tables = sum(int(d.get("tables") or 1) for d in planned_dirs)

    _say(f"计划：{len(planned_dirs)} 个目录 / {tables} 张表 / "
         f"{totals.get('files_in', 0):,} 文件 / {totals.get('rows_out', 0):,} 行")
    if args.dry_run:
        if code != 0:
            _say(f"[WARN] 合并工具退出码 {code}（源里有坏文件或目录被跳过）——"
                 f"本次只是预览不写盘，但照这个源真跑一定失败")
            _dump_stderr(merge_stderr)
        _say(f"耗时 {time.time() - started:.1f}s（未写盘）")
        return 0

    if code != 0:
        _say(f"错误：合并失败 exit={code}，不替换")
        _dump_stderr(merge_stderr)
        return 1

    target_dirs = args.dirs or [d["dir"] for d in planned_dirs]
    merge_files_failed = int(totals.get("files_failed") or 0)
    prod_tables = None
    _say()
    _say("校验 1/2：产物行数 vs 源 footer 累加（逐文件，跳过 0 行）")
    if args.skip_verify:
        _say("  [skip] 按 --skip-verify 跳过（行数 / 表数 / 坏文件都不核对）")
    else:
        src_rows, skipped, failed = count_source_rows(src, target_dirs)
        prod_rows, prod_tables, prod_failed = count_product_rows(tmp)
        _say(f"  源   : {src_rows:,} 行（跳过 0 行文件 {skipped}，读失败 {len(failed)}）")
        _say(f"  产物 : {prod_rows:,} 行 / {prod_tables} 张表（读失败 {len(prod_failed)}）")
        # 坏文件必须硬失败：同一个坏文件在两侧同时被跳过时，比总数会「恰好相等」
        # 而判 OK（审查 F5）。所以「没有坏文件」本身就是校验条件之一。
        if failed:
            _say(f"  [FAIL] 源侧 {len(failed)} 个文件读不出 footer"
                 f"（{', '.join(failed[:5])}{' …' if len(failed) > 5 else ''}）——"
                 f"行数基准不可信，不替换")
            return 1
        if prod_failed:
            _say(f"  [FAIL] 产物侧 {len(prod_failed)} 个文件读不出 footer"
                 f"（{', '.join(prod_failed[:5])}）——产物不可信，不替换")
            return 1
        if merge_files_failed:
            _say(f"  [FAIL] 合并阶段有 {merge_files_failed} 个文件失败"
                 f"（totals.files_failed）——产物缺行，不替换")
            return 1
        if src_rows != prod_rows:
            _say(f"  [FAIL] 行数不符，差 {src_rows - prod_rows:,} 行——不替换，临时目录保留：{tmp}")
            return 1
        _say("  [OK] 行数完全一致，且两侧都没有读不出的文件")

    _say("校验 2/2：表数")
    if args.skip_verify:
        _say(f"  [skip] 按 --skip-verify 跳过（计划 {tables} 张表，未核对产物）")
    else:
        if prod_tables != tables:
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
    _say(f"[REPLACE] 两阶段切换整理层：{out}")
    if out.exists():
        old_files = sum(1 for _ in out.glob("*"))
        old_size = sum(f.stat().st_size for f in out.glob("*") if f.is_file())
        _say(f"          现有 {old_files} 个条目 / {old_size / 1024**3:.2f} GiB"
             f"（切换成功后**永久删除**）")
    if tmp.is_dir():
        _say(f"          新层 {sum(1 for _ in tmp.glob('*.parquet'))} 张表：{tmp}")
    rc = swap_out(out, tmp)
    if rc != 0:
        _say("  [FAIL] 替换未完成，旧整理层保持不变")
        _log(f"refresh_curated REPLACE failed {time.strftime('%Y-%m-%dT%H:%M:%S')} "
             f"(exit={rc})", log_on)
        return 1
    _say(f"[REPLACE] 完成：{out}")
    _say(f"耗时 {time.time() - started:.1f}s")
    _log(f"refresh_curated REPLACE ok {time.strftime('%Y-%m-%dT%H:%M:%S')} "
         f"({totals.get('rows_out', 0)} rows)", log_on)
    return 0


if __name__ == "__main__":
    sys.exit(main())
