# -*- coding: utf-8 -*-
"""safe_delete.py — 按 AGENTS §7 的要求删除：默认进回收站，永久删除需显式开关。

## 为什么有这个工具

AGENTS §7 红线写着「Windows 删除一律进回收站（Python SHFileOperationW），禁 rm/del」。
但 PowerShell 的 `Remove-Item -Recurse` **不走回收站**，是永久删除。
2026-10-23/24 我误以为"本机回收站不可用"，于是用 `Remove-Item` 永久删了约 19.4 GB
（其中 0.69 GB 的 `tushare_v2` 是误删，事后花 4 小时重建）。

2026-10-24 实测纠正：本进程是 **Medium 完整性级别**（不是 Low IL），
`SHFileOperationW(FOF_ALLOWUNDO)` 返回 0 且文件确实出现在回收站里。
**回收站一直是可用的**，错的是我用错了删除方式。

## 用法

    python -B tools/safe_delete.py <路径...>              # 进回收站（默认，可还原）
    python -B tools/safe_delete.py <路径...> --dry-run    # 只报告将删什么
    python -B tools/safe_delete.py <路径...> --permanent  # 永久删除（需再加 --i-know）

退出码：0 全部成功 / 1 有失败 / 2 参数错误
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from ctypes import wintypes
import logging

FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040          # 进回收站
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def measure(path: str) -> tuple[int, int]:
    """返回 (文件数, 字节数)。"""
    if os.path.isfile(path):
        return 1, os.path.getsize(path)
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            fp = os.path.join(root, n)
            try:
                files += 1
                size += os.path.getsize(fp)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/safe_delete.py", exc)
    return files, size


def recycle(paths: list[str]) -> tuple[bool, str]:
    """把路径送入回收站。返回 (是否成功, 说明)。"""
    # SHFileOperationW 的 pFrom 是双 NUL 结尾的多字符串
    joined = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = joined
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        return False, "SHFileOperationW 返回码 %d" % rc
    if op.fAnyOperationsAborted:
        return False, "操作被中止"
    return True, "已送入回收站"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="删除到回收站（默认）；永久删除需 --permanent --i-know")
    ap.add_argument("paths", nargs="+", help="要删除的路径")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不删除")
    ap.add_argument("--permanent", action="store_true", help="永久删除（绕过回收站）")
    ap.add_argument("--i-know", action="store_true", help="确认永久删除不可恢复")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if a.permanent and not a.i_know:
        print("拒绝：--permanent 必须同时给 --i-know（永久删除不可恢复）", file=sys.stderr)
        return 2

    existing, missing = [], []
    for p in a.paths:
        (existing if os.path.exists(p) else missing).append(p)

    rows = []
    for p in existing:
        f, b = measure(p)
        rows.append({"path": p, "files": f, "bytes": b,
                     "mb": round(b / 1024 / 1024, 2)})

    total_mb = round(sum(r["mb"] for r in rows), 2)
    payload = {"mode": "dry-run" if a.dry_run else
               ("permanent" if a.permanent else "recycle"),
               "items": rows, "missing": missing, "total_mb": total_mb}

    if a.dry_run:
        payload["ok"] = True
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print("  [将删] %-60s %8.2f MB / %d 文件" % (r["path"][:60], r["mb"], r["files"]))
            for m in missing:
                print("  [不存在] %s" % m)
            print("\n  dry-run：未删除任何东西。合计 %.2f MB" % total_mb)
        return 0

    if a.permanent:
        import shutil
        failed = []
        for p in existing:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError as exc:
                failed.append({"path": p, "error": str(exc)[:120]})
        payload.update(ok=not failed, method="permanent", failed=failed)
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("  永久删除 %d 项，失败 %d 项，合计 %.2f MB"
                  % (len(existing), len(failed), total_mb))
            for f in failed:
                print("    失败 %s: %s" % (f["path"], f["error"]))
        return 0 if not failed else 1

    ok, msg = recycle(existing) if existing else (True, "无待删项")
    payload.update(ok=ok, method="recycle", message=msg)
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print("  [已删] %-60s %8.2f MB / %d 文件" % (r["path"][:60], r["mb"], r["files"]))
        print("\n  %s；合计 %.2f MB。可从回收站还原（打开回收站 → 还原）" % (msg, total_mb))
        if missing:
            print("  跳过不存在的 %d 项" % len(missing))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
