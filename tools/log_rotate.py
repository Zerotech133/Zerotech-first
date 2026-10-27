# -*- coding: utf-8 -*-
"""日志轮转工具 v0（只读演示默认，--apply 才动手；不删除，仅归档+截断）。

为什么不用 rm/删除：Windows 删除一律走回收站（AGENTS §7），本工具**不删除**任何
文件——把大日志压缩归档到 `<dir>/_log_archive/`，原文件截断为 0；保留策略只做
"标记过期"（移到 `_log_archive/_expired/`），由人工清理。

用法：
  python tools/log_rotate.py --dir C:\\Temp --min-mb 20            # dry-run 列出候选
  python tools/log_rotate.py --dir C:\\Temp --min-mb 20 --apply    # 归档+截断
  python tools/log_rotate.py --dir C:\\Temp --min-mb 20 --apply --purge-expired
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
from pathlib import Path

ARCHIVE = "_log_archive"
EXPIRED = "_expired"


def _in_use(path: Path) -> bool:
    """Windows 上被进程打开的日志无法 rename；用 rename 探测（不改内容）。"""
    tmp = path.with_suffix(path.suffix + ".lockprobe")
    try:
        os.replace(path, tmp)
        os.replace(tmp, path)
        return False
    except OSError:
        try:
            if tmp.exists():
                os.replace(tmp, path)
        except OSError:
            pass
        return True


def rotate_file(path: Path, archive_dir: Path, min_mb: float,
                dry_run: bool = True) -> dict:
    size = path.stat().st_size
    if size < min_mb * 1024 * 1024:
        return {"file": str(path), "ok": True, "skipped": "below_threshold"}
    if _in_use(path):
        return {"file": str(path), "ok": False, "skipped": "in_use"}
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = archive_dir / ("%s.%s.gz" % (path.name, ts))
    if dry_run:
        return {"file": str(path), "ok": True, "dry_run": True,
                "bytes": size, "would_archive": str(dst)}
    archive_dir.mkdir(parents=True, exist_ok=True)
    # 原地读→gzip 归档→原地截断（不移动/不删除原文件；被占用则 fail-visible）
    try:
        with open(path, "rb") as src, gzip.open(dst, "wb") as out:
            shutil.copyfileobj(src, out)
        path.write_text("", encoding="utf-8")
    except OSError as e:
        return {"file": str(path), "ok": False, "skipped": "locked",
                "detail": str(e)[:120]}
    return {"file": str(path), "ok": True, "bytes": size,
            "archived": str(dst), "truncated": True}


def rotate_dir(directory: Path, min_mb: float = 20.0, dry_run: bool = True,
               purge_expired: bool = False, keep: int = 20) -> dict:
    directory = Path(directory)
    archive = directory / ARCHIVE
    results = []
    for p in sorted(directory.glob("*.log")) + sorted(directory.glob("*.log.err")):
        if p.is_file():
            results.append(rotate_file(p, archive, min_mb, dry_run))
    expired = []
    if purge_expired and archive.is_dir():
        files = sorted(archive.glob("*.gz"), key=lambda x: x.stat().st_mtime)
        for old in files[:-keep] if len(files) > keep else []:
            dst = archive / EXPIRED
            dst.mkdir(exist_ok=True)
            target = dst / old.name
            if dry_run:
                expired.append({"dry_run": True, "would_move": str(target)})
            else:
                shutil.move(str(old), str(target))
                expired.append({"moved": str(target)})
    return {"ok": True, "dir": str(directory), "dry_run": dry_run,
            "rotated": [r for r in results if r.get("ok") and not r.get("skipped")],
            "skipped": [r for r in results if r.get("skipped")],
            "expired": expired, "note": "不删除任何文件；过期仅移入归档 _expired/"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="日志轮转（默认 dry-run）")
    ap.add_argument("--dir", default=r"C:\Temp")
    ap.add_argument("--min-mb", type=float, default=20.0)
    ap.add_argument("--apply", action="store_true", help="真正归档+截断")
    ap.add_argument("--purge-expired", action="store_true",
                    help="把归档目录中超出 keep 的旧包移入 _expired（不删除）")
    ap.add_argument("--keep", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = rotate_dir(Path(args.dir), args.min_mb, dry_run=not args.apply,
                     purge_expired=args.purge_expired, keep=args.keep)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else json.dumps(rep, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
