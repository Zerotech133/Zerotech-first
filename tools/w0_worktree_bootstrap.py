#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把主仓 live 数据元数据/必要 sqlite 复制到 merge worktree，供分支 fast 门读取。

只写 worktree/quant_backtest/data 下被 gitignore 的生成物；不改分支代码。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

FILES = [
    ("quant_backtest/data/catalog.json", "catalog.json"),
    ("quant_backtest/data/warehouse_registry.json", "warehouse_registry.json"),
    ("quant_backtest/data/xueqiu/xueqiu_info.db", "xueqiu/xueqiu_info.db"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="W0 worktree data bootstrap")
    ap.add_argument("--repo", default=r"C:\项目\老田的AI助手")
    ap.add_argument("--worktree", required=True)
    a = ap.parse_args(argv)
    repo, wt = Path(a.repo), Path(a.worktree)
    if not (repo / "quant_backtest" / "data").is_dir():
        print("repo data missing: %s" % repo)
        return 2
    if not wt.is_dir():
        print("worktree missing: %s" % wt)
        return 2
    copied = 0
    for rel, dst_rel in FILES:
        src = repo / rel
        if not src.is_file():
            print("skip missing: %s" % src)
            continue
        dst = wt / "quant_backtest" / "data" / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print("copied %s -> %s" % (src.name, dst))
    print("done copied=%d" % copied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
