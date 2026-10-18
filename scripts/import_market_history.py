#!/usr/bin/env python
"""历史行情数据导入（Windows / macOS / Linux 通用）。

用法（在部署包根目录，用系统 Python 或项目 venv 均可——本脚本只用标准库）：

    python scripts\\import_market_history.py ^
        --dsn postgresql://postgres:<密码>@localhost:5432/zhice_os_v2

    # pg_restore 不在 PATH 时手动指定 PostgreSQL bin 目录：
    python scripts\\import_market_history.py --dsn ... ^
        --pg-bin "C:\\Program Files\\PostgreSQL\\16\\bin"

行为：
- 读取 market_history/export_manifest.json 的表清单，逐表：
  TRUNCATE 目标表 → pg_restore --data-only（COPY 灌入）→ 行数与导出基准核对。
- 替换语义：每次导入前清空该行情表再灌（历史行情表没有增量合并需求），
  因此**幂等可重跑**；业务表（持仓/成交/策略等）不受影响。
- 必须已先完成主数据库恢复（DEPLOY-WINDOWS.md 第 3 节），行情表结构已存在。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

# 导入顺序：先小表/字典，后大表；分钟线最耗时放最后
TABLE_ORDER = [
    "v2_instrument",
    "v2_adj_factor",
    "v2_top_list",
    "v2_earnings_forecast",
    "v2_dividend",
    "v2_fundamental",
    "v2_holder_trade",
    "v2_bar_daily",
    "v2_bar_minute",
]


def find_pg_tools(pg_bin: str | None) -> tuple[str, str]:
    """返回 (pg_restore 路径, psql 路径)。优先 --pg-bin，其次 PATH，再扫常见安装目录。"""
    candidates: list[str] = []
    if pg_bin:
        candidates.append(pg_bin)
    which = shutil.which("pg_restore")
    if which:
        candidates.append(os.path.dirname(which))
    # Windows 默认安装路径（版本倒序，取最高版）
    for pattern in (
        r"C:\Program Files\PostgreSQL\*\bin",
        r"C:\Program Files (x86)\PostgreSQL\*\bin",
    ):
        for d in sorted(glob.glob(pattern), reverse=True):
            candidates.append(d)

    ext = ".exe" if os.name == "nt" else ""
    for d in candidates:
        pg_restore = os.path.join(d, f"pg_restore{ext}")
        psql = os.path.join(d, f"psql{ext}")
        if os.path.isfile(pg_restore) and os.path.isfile(psql):
            return pg_restore, psql
    raise SystemExit(
        "找不到 pg_restore/psql。请安装 PostgreSQL（含命令行工具），或用 "
        "--pg-bin 指定 bin 目录，例如：\n"
        '  --pg-bin "C:\\Program Files\\PostgreSQL\\16\\bin"'
    )


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """subprocess.run 包装：失败时回显 stdout/stderr 并终止。"""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stdout or "")
        sys.stderr.write(res.stderr or "")
        raise SystemExit(f"命令失败({res.returncode})：{' '.join(cmd[:4])} ...")
    return res


def table_count(psql: str, dsn: str, table: str) -> int:
    res = run([psql, dsn, "-At", "-c", f"SELECT count(*) FROM {table}"])
    return int(res.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", required=True,
                        help="目标库连接串，如 postgresql://postgres:密码@localhost:5432/zhice_os_v2")
    parser.add_argument("--dir", default=None,
                        help="dump 所在目录（默认：脚本同目录的 ../market_history）")
    parser.add_argument("--pg-bin", default=None, help="PostgreSQL bin 目录（默认自动探测）")
    parser.add_argument("--only", default=None,
                        help="只导入指定表（逗号分隔），如 --only v2_bar_daily")
    parser.add_argument("--skip-truncate", action="store_true",
                        help="不清空目标表（表非空时 COPY 会失败，一般不用）")
    args = parser.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    hist_dir = args.dir or os.path.normpath(os.path.join(base, "..", "market_history"))
    manifest_path = os.path.join(hist_dir, "export_manifest.json")
    if not os.path.isfile(manifest_path):
        raise SystemExit(f"找不到 {manifest_path}——请在部署包根目录运行本脚本")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    expected: dict[str, int] = manifest["tables"]

    tables = [t for t in TABLE_ORDER if t in expected]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        tables = [t for t in tables if t in wanted]

    pg_restore, psql = find_pg_tools(args.pg_bin)
    print(f"pg_restore: {pg_restore}")
    print(f"目标库导入 {len(tables)} 张行情表（替换语义，可重跑）\n")

    results: list[tuple[str, int, int]] = []
    for t in tables:
        dump = os.path.join(hist_dir, f"{t}.dump")
        if not os.path.isfile(dump):
            print(f"[跳过] {t}: 缺 {dump}")
            continue
        if not args.skip_truncate:
            run([psql, args.dsn, "-c", f"TRUNCATE TABLE {t}"])
        run([pg_restore, "--data-only", "--disable-triggers", "-d", args.dsn, dump])
        actual = table_count(psql, args.dsn, t)
        exp = expected[t]
        mark = "OK " if actual == exp else "WARN"
        print(f"[{mark}] {t}: 导入 {actual} / 基准 {exp}")
        results.append((t, actual, exp))

    mismatch = [(t, a, e) for t, a, e in results if a != e]
    if not mismatch:
        print(f"\n完成：{len(results)} 张表，行数与导出基准全部一致")
        return 0
    print(f"\n完成但有 {len(mismatch)} 张表行数与基准不一致：{mismatch}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
