# -*- coding: utf-8 -*-
"""11-05 /dw.html 页面替换演练/执行（默认 dry-run）。

用法:
  python tools/ops/dw_page_swap_20261105.py            # dry-run
  python tools/ops/dw_page_swap_20261105.py --apply    # 执行+自动回滚
  python tools/ops/dw_page_swap_20261105.py --rollback # 回滚
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
OLD = REPO / "portal" / "dw.html"
NEW = REPO / "portal" / "static" / "dw.html"
EXPECT_OLD_BYTES = 39027
EXPECT_OLD_BYTES_ONLY = True  # 新页按当前 static/dw.html 动态校验
SERVED = "http://127.0.0.1:8734/dw.html"


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fail(msg: str, rc: int = 2) -> int:
    print("ABORT:", msg)
    return rc


def backup_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(r"C:\Temp") / ("dw.html.bak-20261105-" + stamp)


def rollback() -> int:
    baks = sorted(Path(r"C:\Temp").glob("dw.html.bak-20261105-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not baks:
        return fail("no backup found", 2)
    shutil.copy2(baks[0], OLD)
    print("ROLLBACK OK source=%s bytes=%d" % (baks[0], OLD.stat().st_size))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    if args.rollback:
        return rollback()

    old_raw = OLD.read_bytes()
    new_raw = NEW.read_bytes()
    print("OLD bytes=%d sha=%s" % (len(old_raw), sha_bytes(old_raw)))
    print("NEW bytes=%d sha=%s" % (len(new_raw), sha_bytes(new_raw)))
    if len(old_raw) != EXPECT_OLD_BYTES:
        return fail("old page bytes mismatch expected %d" % EXPECT_OLD_BYTES)
    if not new_raw:
        return fail("new page is empty")
    if "数据仓" not in new_raw.decode("utf-8", "replace")[:4000]:
        return fail("new page title/content check failed")

    if not args.apply:
        print("DRY-RUN: would backup old then copy new; no changes made.")
        return 0

    bak = backup_path()
    shutil.copy2(OLD, bak)
    shutil.copy2(NEW, OLD)
    after = OLD.read_bytes()
    print("BACKUP %s" % bak)
    print("AFTER bytes=%d sha=%s" % (len(after), sha_bytes(after)))
    if sha_bytes(after) != sha_bytes(new_raw):
        shutil.copy2(bak, OLD)
        print("VERIFY FAIL -> auto rollback done")
        return 1

    try:
        served = urllib.request.urlopen(SERVED, timeout=8).read()
        title = ""
        try:
            import re
            m = re.search(rb"<title>(.*?)</title>", served, re.S)
            title = m.group(1).decode("utf-8", "replace") if m else ""
        except Exception:
            pass
        print("SERVED bytes=%d sha=%s title=%s" % (len(served), sha_bytes(served), title))
        if len(served) != len(new_raw) or sha_bytes(served) != sha_bytes(new_raw):
            shutil.copy2(bak, OLD)
            print("SERVED VERIFY FAIL -> auto rollback done")
            return 1
    except Exception as exc:  # noqa: BLE001
        shutil.copy2(bak, OLD)
        print("SERVED ERROR -> auto rollback:", exc)
        return 1
    print("SWAP OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
