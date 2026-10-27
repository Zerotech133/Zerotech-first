#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_data_manifest.py — 数据根轻量清单（只读，不复制任何数据）。

背景
----
``quant_backtest/data`` 约 43.5 GB / 26.8 万文件，是"可重建但不是零成本"的
资产。整盘只有一块 C:，已有 git bundle 兜住 4 个仓的历史，但数据本身不在
任何仓里，因此需要一份**不下数据**的清单：出事后能据此判断要重建哪些目录、
哪些文件是新增的。

口径（刻意做轻）
----------------
* 只对**顶层子目录**各出一行汇总（文件数 / 总字节 / 最新 mtime），
  **不为每个文件建条目**（26 万条会把 JSON 和内存都撑爆）。
* 顶层散落文件单独汇总成 ``scan.root_files``，仍计入 total。
* 扫描用 ``os.scandir`` 的显式栈（DirEntry 自带 stat 缓存，不额外 stat）；
  默认全量递归，实测本机热缓存 268k 文件约 1~2 秒；``--max-depth 2`` 可退化为
  "顶层子目录的直接 + 一层子目录"，供冷缓存/网络盘场景保命。
* ``--time-budget``（默认 120s）是软预算：超时后剩余顶层目录标记为
  ``scanned: false``，totals 标记 ``partial: true``，退出码 2，绝不静默。

输出（默认 ``<workspace>/_vault/data_manifest.json``）
------------------------------------------------------
generated_at / root / total_files / total_bytes /
dirs[] (name, files, bytes, latest_mtime) /
catalog (path, size, sha256) /
scan (elapsed_sec, max_depth, partial, errors, ...)

用法
----
    python -B tools/gen_data_manifest.py
    python -B tools/gen_data_manifest.py --out C:/tmp/data_manifest.json
    python -B tools/gen_data_manifest.py --max-depth 2 --time-budget 60

退出码：0 成功；1 硬错误（根目录不存在/写盘失败）；2 扫描因预算不完整。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import logging

# tools/gen_data_manifest.py -> repo root -> workspace root
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "quant_backtest" / "data"
DEFAULT_OUT = REPO_ROOT.parent / "_vault" / "data_manifest.json"

SCHEMA = "data-manifest/v1"
HASH_CHUNK = 4 * 1024 * 1024
PROGRESS_EVERY = 20000


def iso_local(ts: float | None) -> str | None:
    """epoch -> local ISO8601 with offset, or None for 'no file'."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{bytes_} B"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def scan_dir(top: str, max_depth: int, deadline: float | None) -> dict:
    """Recursively aggregate one top-level directory with an explicit scandir stack.

    ``max_depth`` = 0 means unlimited. Depth 1 is the direct content of ``top``.
    Returns counters plus a ``partial`` flag if the soft deadline was hit.
    """
    files = 0
    total = 0
    latest = 0.0
    errors = 0
    symlinks = 0
    truncated = 0
    partial = False
    stack: list[tuple[str, int]] = [(top, 1)]

    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if max_depth and depth >= max_depth:
                                truncated += 1
                            else:
                                stack.append((entry.path, depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            files += 1
                            total += stat.st_size
                            if stat.st_mtime > latest:
                                latest = stat.st_mtime
                            if deadline is not None and (files % PROGRESS_EVERY) == 0:
                                if time.monotonic() > deadline:
                                    partial = True
                                    stack.clear()
                                    break
                        else:
                            symlinks += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        if partial:
            break

    return {
        "files": files,
        "bytes": total,
        "latest_mtime": iso_local(latest),
        "partial": partial,
        "errors": errors,
        "skipped_symlinks": symlinks,
        "truncated_dirs": truncated,
    }


def scan_root(root: Path, max_depth: int, time_budget: float) -> dict:
    started = time.monotonic()
    deadline = started + time_budget if time_budget > 0 else None

    top_dirs: list[tuple[str, str]] = []
    root_files = 0
    root_bytes = 0
    root_latest = 0.0
    root_errors = 0
    skipped_top = 0

    with os.scandir(root) as it:
        for entry in it:
            try:
                if entry.is_dir(follow_symlinks=False):
                    top_dirs.append((entry.name, entry.path))
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    root_files += 1
                    root_bytes += stat.st_size
                    if stat.st_mtime > root_latest:
                        root_latest = stat.st_mtime
                else:
                    skipped_top += 1
            except OSError:
                root_errors += 1

    top_dirs.sort(key=lambda item: item[0].lower())

    dirs: list[dict] = []
    errors = root_errors
    skipped = skipped_top
    truncated = 0
    partial = False
    files_total = root_files
    bytes_total = root_bytes

    for name, path in top_dirs:
        if deadline is not None and time.monotonic() > deadline:
            partial = True
            dirs.append(
                {
                    "name": name,
                    "files": None,
                    "bytes": None,
                    "latest_mtime": None,
                    "scanned": False,
                }
            )
            continue

        stats = scan_dir(path, max_depth, deadline)
        dirs.append(
            {
                "name": name,
                "files": stats["files"],
                "bytes": stats["bytes"],
                "latest_mtime": stats["latest_mtime"],
                "scanned": True,
            }
        )
        files_total += stats["files"]
        bytes_total += stats["bytes"]
        errors += stats["errors"]
        skipped += stats["skipped_symlinks"]
        truncated += stats["truncated_dirs"]
        if stats["partial"]:
            partial = True

    dirs.sort(key=lambda d: (-1 if d["bytes"] is None else d["bytes"]), reverse=True)

    elapsed = time.monotonic() - started
    return {
        "total_files": files_total,
        "total_bytes": bytes_total,
        "dirs": dirs,
        "partial": partial,
        "elapsed_sec": round(elapsed, 3),
        "top_dirs": len(top_dirs),
        "errors": errors,
        "skipped_symlinks": skipped,
        "truncated_dirs": truncated,
        "root_files": {
            "files": root_files,
            "bytes": root_bytes,
            "latest_mtime": iso_local(root_latest),
        },
    }


def catalog_info(root: Path) -> dict:
    path = root / "catalog.json"
    info: dict = {"path": str(path), "present": False}
    try:
        stat = path.stat()
    except OSError:
        return info

    info["present"] = True
    info["size"] = stat.st_size
    info["mtime"] = iso_local(stat.st_mtime)
    try:
        info["sha256"] = sha256_file(path)
    except OSError as exc:
        info["sha256"] = None
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lightweight manifest for the quant data root (read-only).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="data root (default: <repo>/quant_backtest/data)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output JSON (default: <workspace>/_vault/data_manifest.json)")
    parser.add_argument("--max-depth", type=int, default=0,
                        help="0 = full recursion (default); 2 = top dir + one nested level")
    parser.add_argument("--time-budget", type=float, default=120.0,
                        help="soft scan budget in seconds (0 disables, default 120)")
    args = parser.parse_args(argv)

    if args.max_depth < 0:
        parser.error("--max-depth must be >= 0")
    if args.time_budget < 0:
        parser.error("--time-budget must be >= 0")

    started = time.monotonic()
    root = args.root.expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        print(f"ERROR: data root not found: {root} ({exc})", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"ERROR: data root is not a directory: {root}", file=sys.stderr)
        return 1

    print(f"[scan] root: {root}")
    print(f"[scan] max_depth={args.max_depth or 'unlimited'} "
          f"time_budget={args.time_budget or 'off'}s")

    result = scan_root(root, args.max_depth, args.time_budget)
    scope = "full" if args.max_depth == 0 else f"depth<={args.max_depth}"
    print(f"[scan] {result['top_dirs']} top-level dirs, {result['total_files']} files, "
          f"{human(result['total_bytes'])}, scope={scope}, "
          f"partial={result['partial']}, {result['elapsed_sec']:.2f}s")

    cat_started = time.monotonic()
    catalog = catalog_info(root)
    if catalog.get("present"):
        print(f"[catalog] {catalog['size']} bytes sha256={catalog.get('sha256')} "
              f"({time.monotonic() - cat_started:.2f}s)")
    else:
        print("[catalog] catalog.json not found (recorded as present=false)")

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "total_files": result["total_files"],
        "total_bytes": result["total_bytes"],
        "dirs": result["dirs"],
        "catalog": catalog,
        "schema": SCHEMA,
        "scan": {
            "elapsed_sec": result["elapsed_sec"],
            "max_depth": args.max_depth,
            "time_budget_sec": args.time_budget,
            "partial": result["partial"],
            "top_dirs": result["top_dirs"],
            "root_files": result["root_files"],
            "errors": result["errors"],
            "skipped_symlinks": result["skipped_symlinks"],
            "truncated_dirs": result["truncated_dirs"],
        },
    }

    try:
        write_json_atomic(args.out, payload)
    except OSError as exc:
        print(f"ERROR: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1

    size = args.out.stat().st_size
    print(f"[write] {args.out} ({size} bytes, {len(result['dirs'])} dirs, "
          f"{time.monotonic() - started:.2f}s total)")

    if result["partial"]:
        print("WARN: scan hit --time-budget; totals are partial (see scan.partial). "
              "Re-run with --max-depth 2 or a larger --time-budget.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/gen_data_manifest.py", exc)
    raise SystemExit(main())
