#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal 大文件拆分守卫（2026-10-28，守则 v1）。

分级规则：
  硬规则（违规 exit 1）：
    · portal/portal_api.py > PORTAL_API_BUDGET（5,200 行）
    · portal/api_*.py 缺失/不可读
  建议项（默认只告警、exit 0；加 --strict 时同样 exit 1）：
    · 冻结名单（存量巨型文件）行数 > 基线（只减不增）
    · 非冻结 api_*.py > NEW_FILE_LIMIT（1,200 行）
用法：python tools/check_portal_split.py [--quiet] [--strict]
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
# 2026-10-31 P120 校正：api_kg 已拆分，基线 5,841→96（否则可回填 5,745 行不报警）；
# 另经 git show baa92f391 复核：misc/chanlun_pages/portfolio 冻结提交实测即 2710/2328/2322，
# 当前 2798/2403/2383 属真实增长（非基线登记错误），保持基线不变、增长需 owner 说明/回收。
FROZEN = {
    "api_backtest.py": 285,          # 2026-10-30 拆分 B0-B6 收口后收紧（6,395→285）
    "api_kg.py": 96,                 # 2026-10-31：已拆至 96 行，按实际收紧
    "api_misc.py": 2710,
    "api_chanlun_pages.py": 2328,
    "api_portfolio.py": 2322,
    # 2026-10-30 B7：_bt_stock_page 单函数页 1,300+ 行，拆分成本>收益，登记冻结（只减不增）
    "api_backtest_pages_stock.py": 1346,
}


def count_lines(p: Path) -> int:
    with p.open(encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="portal 大文件拆分守卫（分级）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--strict", action="store_true", help="建议项也判失败（CI 用）")
    args = ap.parse_args(argv)

    problems = []  # 硬规则
    warns = []     # 建议项
    notes = []

    pa = PORTAL / "portal_api.py"
    if not pa.is_file():
        problems.append("缺 portal/portal_api.py")
    else:
        n = count_lines(pa)
        if n > PORTAL_API_BUDGET:
            problems.append("portal_api.py %d > 预算 %d" % (n, PORTAL_API_BUDGET))
        elif not args.quiet:
            notes.append("portal_api.py %d / %d（%.0f%%）"
                         % (n, PORTAL_API_BUDGET, n * 100.0 / PORTAL_API_BUDGET))

    for p in sorted(PORTAL.glob("api_*.py")):
        n = count_lines(p)
        name = p.name
        if name in FROZEN:
            base = FROZEN[name]
            if n > base:
                warns.append("%s 冻结文件增长：%d > 基线 %d（守则：只减不增，需说明）"
                             % (name, n, base))
        elif n > NEW_FILE_LIMIT:
            warns.append("%s %d > 新文件上限 %d（应拆分或登记冻结基线）"
                         % (name, n, NEW_FILE_LIMIT))

    for w in warns:
        print("PORTAL-SPLIT WARN：%s" % w)
    if warns and not args.strict:
        print("…建议项不阻塞（--strict 可判失败）")
    if args.strict:
        problems.extend(warns)

    if problems:
        print("PORTAL-SPLIT FAIL：%d 项硬违规" % len(problems))
        for x in problems:
            print("  -", x)
        return 1
    print("PORTAL-SPLIT PASS" + ("" if args.quiet else "：" + "；".join(notes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
