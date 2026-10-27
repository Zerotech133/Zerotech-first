# -*- coding: utf-8 -*-
"""O8 P2：分线 lock 快照校验（只读）。

检查 requirements/lines/*.lock：
  · 文件存在且非空；
  · 每行是 `name==version` 或 `#` 注释（忽略空行）；
  · 包名无重复（同一文件内）；
  · 统计包数。

用法：
  python -X utf8 tools/req_lines_check.py
  python -X utf8 tools/req_lines_check.py --json
  python -X utf8 tools/req_lines_check.py --strict   # 有 error → rc=1
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(REPO, "requirements", "lines")
LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._\-]*)==([^=\s]+)$")


def check_file(path):
    errors = []
    names = []
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return {"file": os.path.basename(path), "ok": False, "packages": 0,
                "errors": ["read failed: %s" % e]}
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = LINE_RE.match(s)
        if not m:
            errors.append("line %d 格式非法: %s" % (i, s[:80]))
            continue
        names.append(m.group(1).lower())
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        errors.append("重复包: " + ", ".join(dup[:10]))
    if not names:
        errors.append("无有效包行")
    return {"file": os.path.basename(path), "ok": not errors,
            "packages": len(names), "errors": errors}


def check_dir(path=DEFAULT_DIR):
    files = sorted(glob.glob(os.path.join(path, "*.lock")))
    results = [check_file(p) for p in files]
    return {
        "dir": path,
        "files": len(results),
        "ok": all(r["ok"] for r in results),
        "total_packages": sum(r["packages"] for r in results),
        "results": results,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="分线 lock 快照校验（只读）")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.dir):
        print("目录不存在：%s" % args.dir, file=sys.stderr)
        return 2
    res = check_dir(args.dir)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("req lines: files=%d total_packages=%d ok=%s" % (
            res["files"], res["total_packages"], res["ok"]))
        for r in res["results"]:
            print("  [%s] %-28s packages=%-4d %s" % (
                "OK  " if r["ok"] else "FAIL", r["file"], r["packages"],
                "; ".join(r["errors"]) if r["errors"] else ""))
    if args.strict and not res["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
