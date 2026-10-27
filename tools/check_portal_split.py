#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal 大文件拆分守卫（2026-10-28，守则 v1）。

规则：
  1) portal/portal_api.py ≤ PORTAL_API_BUDGET（5,200 行）；
  2) 非冻结的 portal/api_*.py ≤ NEW_FILE_LIMIT（1,200 行）；
  3) 冻结名单（存量巨型文件）只允许"不增长"（相对基线）。
退出码：0=PASS，1=违规。
用法：python tools/check_portal_split.py [--quiet]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PORTAL = REPO / "portal"
PORTAL_API_BUDGET = 5200
NEW_FILE_LIMIT = 1200
# 冻结名单：基线行数（2026-10-28 实测），只减不增
FROZEN = {
    "api_backtest.py": 6135,
    "api_kg.py": 5841,
    "api_misc.py": 2710,
    "api_chanlun_pages.py": 2328,
    "api_portfolio.py": 2322,
}


def count_lines(p: Path) -> int:
    with p.open(encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="portal 大文件拆分守卫")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    problems = []
    notes = []

    pa = PORTAL / "portal_api.py"
    if pa.is_file():
        n = count_lines(pa)
        if n > PORTAL_API_BUDGET:
            problems.append("portal_api.py %d > 预算 %d" % (n, PORTAL_API_BUDGET))
        elif not args.quiet:
            notes.append("portal_api.py %d / %d（%.0f%%）" % (n, PORTAL_API_BUDGET, n * 100.0 / PORTAL_API_BUDGET))

    for p in sorted(PORTAL.glob("api_*.py")):
        n = count_lines(p)
        name = p.name
        if name in FROZEN:
            base = FROZEN[name]
            if n > base:
                problems.append("%s 冻结文件增长：%d > 基线 %d（拆分守则：只减不增）" % (name, n, base))
        elif n > NEW_FILE_LIMIT:
            problems.append("%s %d > 新文件上限 %d（拆分或说明入冻结名单）" % (name, n, NEW_FILE_LIMIT))

    if problems:
        print("PORTAL-SPLIT FAIL：%d 项违规" % len(problems))
        for x in problems:
            print("  -", x)
        return 1
    print("PORTAL-SPLIT PASS" + ("" if args.quiet else "：" + "；".join(notes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
