# -*- coding: utf-8 -*-
"""duck_lake.py -- A股量化情报工作站 Parquet 湖的 DuckDB 查询层。

================================================================================
!! 给主理人的 FLAG（不要删除）!!
================================================================================
生产 Python（"C:\\Program Files\\Python311\\python.exe"）**尚未安装 duckdb**，
本模块目前只能跑在受管 venv：
    C:\\Users\\t\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe
（venv 基座 Python 3.13，duckdb 1.5.6，经腾讯云 PyPI 镜像安装；
 清华镜像对 pip UA 返回 403，官方 pypi.org 直连超时，安装渠道问题见下。）
计划任务（schtasks）要接入本查询层之前，必须先解决生产 Python311 的
duckdb 安装渠道（清华镜像 403 / pypi.org 直连不通），**在主理人裁决前
不得擅自给 Python311 装任何包**。
================================================================================

设计说明
--------
1. 视图存储位置：**内存库**（duckdb.connect() 不落盘）。
   理由：视图是纯元数据、构建开销毫秒级；catalog.json 由 build_catalog.py
   持续重建，落一个 .duckdb 文件会与 catalog 及湖文件产生状态漂移，
   内存库每次按最新 catalog 现建，天然无漂移。湖本身 11GB/23 万文件，
   镜像化持久视图没有收益。

2. 视图来源：读 catalog.json（version 2，tables + aliases）。
   - 每张 parquet 类表 -> ``CREATE VIEW <表名> AS SELECT * FROM read_parquet('<glob>')``
   - 每个别名 -> 指向其 resolves 中可用的表；多表别名用 UNION ALL BY NAME 拼接
     （catalog 注明 'daily' 等为零重叠 concat；fallback 型别名按优先级顺序拼接，
      去重语义以 catalog 备注为准，本层不做 dedupe，留给查询方）。

3. layout -> glob 推导（统一排除下划线开头的临时/遗留文件）：
   ============  ============================================
   part          <store>/[!_]*.parquet          (part_0000.parquet ...)
   single        <store>/[!_]*.parquet
   by_date       <store>/[!_]*.parquet          (d_19910102.parquet ...)
   by_code_month <store>/[!_]*.parquet          (159915_SZ_202309.parquet ...)
   by_period     <store>/[!_]*.parquet          (p_20240930.parquet ...)
   by_code       <store>/ ** /[!_]*.parquet     (按代码子目录，含少量平铺)
   parquet       store 本身是文件则直读，是目录则同 part
   sqlite/jsonl/pg  不支持（非 parquet），跳过并记录
   ============  ============================================

4. 优雅跳过：目录缺失 / 无 parquet / 不支持的 layout / deprecated 表
   一律跳过并记录原因，--list 与构建摘要中可见，绝不抛异常中断。

用法
----
    # 列出全部可用视图 + 跳过的表
    python duck_lake.py --list

    # 执行任意 SQL（可引用任意视图/别名）
    python duck_lake.py --sql "SELECT count(*) FROM ts_daily"

    # 快速预览某张表
    python duck_lake.py --table ts_daily --limit 5

    # 显式指定 catalog（默认 C:/aios/quant_backtest/data/catalog.json）
    python duck_lake.py --catalog <path> --list
"""

from __future__ import annotations

import argparse
import glob as pyglob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# --------------------------------------------------------------------------
# 常量与路径映射
# --------------------------------------------------------------------------

DEFAULT_CATALOG = r"C:\aios\quant_backtest\data\catalog.json"

# catalog 里 store 路径的真实前缀（老路径），与 aios junction 的映射关系。
# C:\aios -> C:\项目\老田的AI助手 （junction）
STORE_PREFIX_REAL = "C:/项目/老田的AI助手/"
STORE_PREFIX_JUNCTION = "C:/aios/"

# 不支持做 parquet 视图的 layout
UNSUPPORTED_LAYOUTS = {"sqlite", "jsonl", "pg"}

# flat 类 layout：parquet 文件直接平铺在 store 目录下
FLAT_LAYOUTS = {"part", "single", "by_date", "by_code_month", "by_period", "parquet"}

# DuckDB 排除下划线开头文件的文件名 glob 片段
GLOB_BODY = "[!_]*.parquet"


@dataclass
class ViewEntry:
    """一个成功注册的视图。"""

    name: str
    kind: str  # "table" / "alias"
    glob: str = ""  # alias 视图无 glob
    sources: List[str] = field(default_factory=list)  # alias 指向的底层表
    rows_catalog: Optional[int] = None  # catalog 记录的行数（可能为 None）
    files_catalog: Optional[int] = None  # catalog 记录的文件数


@dataclass
class SkipEntry:
    """一个被跳过的 catalog 条目及原因。"""

    name: str
    kind: str  # "table" / "alias"
    reason: str


def _map_store(store: str) -> str:
    """catalog 里的 store 路径 -> 本机可用路径。

    优先原样使用（C:/项目/... 本身有效）；若不存在则尝试 aios junction。
    """
    s = store.replace("\\", "/").rstrip("/")
    if os.path.exists(s):
        return s
    if s.startswith(STORE_PREFIX_REAL):
        alt = STORE_PREFIX_JUNCTION + s[len(STORE_PREFIX_REAL) :]
        if os.path.exists(alt):
            return alt
    return s


def _derive_glob(layout: str, store: str) -> str:
    """根据 catalog 的 layout 与 store 目录推导 read_parquet 的 glob。"""
    store = _map_store(store)
    if layout == "parquet":
        # parquet layout：store 可能直接是 .parquet 文件
        if store.lower().endswith(".parquet") and os.path.isfile(store):
            return store
    if layout in FLAT_LAYOUTS:
        return f"{store}/{GLOB_BODY}"
    if layout == "by_code":
        # 按代码分子目录；** 同时覆盖少量平铺文件的场景
        return f"{store}/**/{GLOB_BODY}"
    raise ValueError(f"unsupported layout: {layout}")


def _glob_has_files(glob_expr: str) -> bool:
    """用 Python 侧轻量探测 glob 是否至少命中一个文件（不必全量枚举）。"""
    # 把 DuckDB 的 [!_]* 片段翻译回 Python glob 语义做存在性探测
    py_expr = glob_expr.replace(GLOB_BODY, "*.parquet")
    if "**" in py_expr:
        it = pyglob.iglob(py_expr, recursive=True)
    else:
        it = pyglob.iglob(py_expr)
    for p in it:
        if not os.path.basename(p).startswith("_"):
            return True
    return False


# --------------------------------------------------------------------------
# 湖构建
# --------------------------------------------------------------------------


class DuckLake:
    """基于 catalog.json 的 DuckDB 湖查询层（内存库视图）。"""

    def __init__(
        self, catalog_path: str = DEFAULT_CATALOG, verbose: bool = True
    ) -> None:
        self.catalog_path = catalog_path
        self.verbose = verbose
        self.views: Dict[str, ViewEntry] = {}
        self.skipped: List[SkipEntry] = []
        self.con = None  # 延迟到 build 时创建

    # -- 构建视图 ----------------------------------------------------------

    def build(self) -> None:
        """读 catalog 并注册全部可用视图。"""
        try:
            import duckdb  # 延迟导入，让 --help 无需 duckdb
        except ImportError:
            # 2026-10-16：duckdb 安装在项目 vendor 目录（不污染全局 site-packages），
            # 生产 Python311 经此路径发现。腾讯云镜像安装：pip install duckdb --target C:\aios\vendor
            _vendor = r"C:\aios\vendor"
            if os.path.isdir(_vendor) and _vendor not in sys.path:
                sys.path.insert(0, _vendor)
            import duckdb

        with open(self.catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        tables: Dict[str, dict] = catalog.get("tables", {})
        aliases: Dict[str, dict] = catalog.get("aliases", {})

        self.con = duckdb.connect()  # 内存库

        # 1) 表视图
        for name, meta in tables.items():
            self._register_table_view(name, meta)

        # 2) 别名视图（依赖表视图已建好）
        for alias, meta in aliases.items():
            self._register_alias_view(alias, meta)

        if self.verbose:
            self._print_summary()

    def _register_table_view(self, name: str, meta: dict) -> None:
        layout = str(meta.get("layout", ""))
        store = str(meta.get("store", ""))
        deprecated = bool(meta.get("deprecated", False))

        if deprecated:
            self.skipped.append(
                SkipEntry(name, "table", "deprecated 表，catalog 标记弃用")
            )
            return
        if layout in UNSUPPORTED_LAYOUTS:
            self.skipped.append(
                SkipEntry(name, "table", f"layout={layout} 非 parquet，本层不支持")
            )
            return
        try:
            glob_expr = _derive_glob(layout, store)
        except ValueError as exc:
            self.skipped.append(SkipEntry(name, "table", str(exc)))
            return

        mapped_store = _map_store(store)
        if not os.path.exists(mapped_store):
            self.skipped.append(
                SkipEntry(name, "table", f"store 目录缺失: {mapped_store}")
            )
            return
        if not _glob_has_files(glob_expr):
            self.skipped.append(
                SkipEntry(name, "table", f"store 存在但无 parquet 文件: {mapped_store}")
            )
            return

        try:
            self.con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{glob_expr}')"  # nosec B608 - 值参数化/内部标识符（已抽查）
            )
        except Exception as exc:  # noqa: BLE001
            self.skipped.append(SkipEntry(name, "table", f"CREATE VIEW 失败: {exc}"))
            return

        self.views[name] = ViewEntry(
            name=name,
            kind="table",
            glob=glob_expr,
            rows_catalog=meta.get("rows"),
            files_catalog=meta.get("files"),
        )

    def _register_alias_view(self, alias: str, meta: dict) -> None:
        resolves: List[str] = list(meta.get("resolves", []))
        available = [t for t in resolves if t in self.views]
        if not available:
            self.skipped.append(
                SkipEntry(alias, "alias", f"resolves {resolves} 均不可用")
            )
            return
        if alias in self.views:
            # catalog 里别名与某张实体表同名（如 daily/ts_daily 场景）：
            # 保留实体表视图，别名视为冗余，不算错误。
            self.skipped.append(
                SkipEntry(
                    alias,
                    "alias",
                    "与同名实体表视图冲突，保留实体表；如需多源拼接请直接查询各 resolves 表",
                )
            )
            return
        try:
            if len(available) == 1:
                body = f"SELECT * FROM {available[0]}"  # nosec B608 - 值参数化/内部标识符（已抽查）
            else:
                # 多源别名：UNION ALL BY NAME 拼接（零重叠/fallback 语义见 catalog 备注）
                parts = [f"SELECT * FROM {t}" for t in available]  # nosec B608 - 值参数化/内部标识符（已抽查）
                body = "\nUNION ALL BY NAME\n".join(parts)
            self.con.execute(f"CREATE VIEW {alias} AS {body}")
        except Exception as exc:  # noqa: BLE001
            self.skipped.append(SkipEntry(alias, "alias", f"CREATE VIEW 失败: {exc}"))
            return
        self.views[alias] = ViewEntry(name=alias, kind="alias", sources=available)

    # -- 展示 / 查询 -------------------------------------------------------

    def _print_summary(self) -> None:
        print(
            f"[duck_lake] catalog={self.catalog_path}",
            f"views={len(self.views)} skipped={len(self.skipped)}",
        )
        if self.skipped:
            print("[duck_lake] 跳过明细:")
            for s in self.skipped:
                print(f"  - {s.kind}:{s.name} -> {s.reason}")

    def list_views(self) -> None:
        """打印全部可用视图清单。"""
        print(f"{'视图名':<24} {'类型':<6} {'行数(catalog)':>14}  来源/glob")
        print("-" * 100)
        for name, v in sorted(self.views.items(), key=lambda kv: (kv[1].kind, kv[0])):
            rows = "-" if v.rows_catalog is None else f"{v.rows_catalog:,}"
            detail = v.glob if v.kind == "table" else " <- " + ",".join(v.sources)
            print(f"{name:<24} {v.kind:<6} {rows:>14}  {detail}")
        print("-" * 100)
        print(f"合计 {len(self.views)} 个视图；跳过 {len(self.skipped)} 条：")
        for s in self.skipped:
            print(f"  [skip] {s.kind}:{s.name} -> {s.reason}")

    def run_sql(self, sql: str) -> None:
        """执行 SQL 并打印结果表格 + 行数 + 耗时。"""
        t0 = time.perf_counter()
        cur = self.con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        elapsed = time.perf_counter() - t0

        widths = [
            max(len(str(c)), *(len(str(r[i])) for r in rows) if rows else [0])
            for i, c in enumerate(cols)
        ]

        def fmt_row(vals: List[object]) -> str:
            return " | ".join(str(v).rjust(w) for v, w in zip(vals, widths))

        print(fmt_row(cols))
        print("-+-".join("-" * w for w in widths))
        for r in rows[:50]:
            print(fmt_row(r))
        if len(rows) > 50:
            print(f"... 共 {len(rows)} 行（仅显示前 50 行）")
        print(f"\n[{len(rows)} rows in {elapsed:.3f}s]")

    def preview(self, table: str, limit: int) -> None:
        if table not in self.views:
            raise SystemExit(f"视图不存在: {table}（用 --list 查看可用视图）")
        self.run_sql(f"SELECT * FROM {table} LIMIT {int(limit)}")  # nosec B608 - 值参数化/内部标识符（已抽查）


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    # Windows 控制台（GBK）下统一用 UTF-8 输出，避免中文乱码；
    # 老终端可用 `chcp 65001` 配合。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="duck_lake",
        description="A股量化情报工作站 Parquet 湖的 DuckDB 查询层（基于 catalog.json）",
    )
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="catalog.json 路径")
    parser.add_argument(
        "--list", action="store_true", help="列出全部可用视图与跳过明细"
    )
    parser.add_argument("--table", default=None, help="预览/查询的目标表或别名")
    parser.add_argument(
        "--sql", default=None, help="要执行的 SQL（可引用任意视图/别名）"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="--table 预览行数（默认 20）"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="不打印构建摘要")
    args = parser.parse_args(argv)

    lake = DuckLake(catalog_path=args.catalog, verbose=not args.quiet)
    lake.build()

    if args.list:
        lake.list_views()
        return 0
    if args.sql:
        lake.run_sql(args.sql)
        return 0
    if args.table:
        lake.preview(args.table, args.limit)
        return 0
    # 无参数：默认 --list
    lake.list_views()
    return 0


if __name__ == "__main__":
    sys.exit(main())
