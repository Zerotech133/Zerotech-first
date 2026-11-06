# -*- coding: utf-8 -*-
"""
warehouse_reader.py — 统一取数层（P0，数据盘活方法论）

设计契约（为什么需要这一层，见 deliverables/2026-11-06_数据盘活方法论_*.md）：

1. 306 张表按"来源"摆在 11 个 channel、53 个物理目录里，同一件事实（日线）存在多份
   物理副本，行数相差 4.3 倍，且同名表在不同根目录下有**重名目录**
   （`daily` → tushare/daily + tushare_v2/daily；`ts_daily` → tdx + 两个 staging 目录）。
   注册表的 `physical_tables` 只是一个"名字"，无法唯一定位真实路径。
   → Reader 按名字全盘解析候选目录，默认取"非 staging 副本的并集"作为最完整覆盖，
     从根上消灭"读错副本 → 静默少样本"。

2. "读越界必须报错，不能返回空"：
   - 资产不存在 → UnknownAssetError
   - 查询日期窗口与数据实际覆盖 [min,max] 完全不相交 → OutOfBoundsError
   - 查询日期窗口部分落在覆盖外 → 警告（数据有洞），仍执行
   - 列名不存在 → ColumnError（提前校验，给出可用列清单）
   - 合法过滤后仍为 0 行 → EmptyResultError（默认开启，可关）
   - 多副本并存 → 警告列出各副本覆盖，不静默

3. schema 漂移（如 us_daily 的 trade_date 在部分文件是 VARCHAR）一律用
   union_by_name=true 处理，下游脚本不再各自踩坑。

本文件是纯新增模块，不修改任何现有代码/数据。DuckDB 只读，不做写。
"""
from __future__ import annotations

import json
import os
import re
import time
import glob
import warnings
from typing import Optional, Iterable, Dict, List, Tuple, Any

import duckdb

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class WarehouseError(Exception):
    """基类。"""


class UnknownAssetError(WarehouseError):
    """请求的资产 id 不在注册表。"""


class OutOfBoundsError(WarehouseError):
    """查询日期窗口与数据实际覆盖完全不相交。"""


class EmptyResultError(WarehouseError):
    """合法过滤后结果为 0 行（可能误读了错误副本 / 错误实体）。"""


class ColumnError(WarehouseError):
    """请求的列不存在。"""


class ReplicaWarning(UserWarning):
    """多副本并存时的可见告警（不是异常，但必须被看到）。"""


# 应排除的"临时/暂存/备份"根目录关键字（不参与权威副本选择）
_STAGING_HINTS = ("staging", "tail_fill", "backup", "bak", "archive",
                  "_repair", "prev", "._", ".bak")

_DATE_COL_RE = re.compile(
    r"^(trade_date|trade_time|report_date|ann_date|pub_date|list_date|"
    r"datetime|date|time|.*_date|.*_time)$", re.IGNORECASE)


class WarehouseReader:
    def __init__(self,
                 registry_path: Optional[str] = None,
                 data_root: Optional[str] = None,
                 cache_path: Optional[str] = None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.data_root = data_root or os.path.join(here, "data")
        self.registry_path = registry_path or os.path.join(
            self.data_root, "warehouse_registry.json")
        self.cache_path = cache_path or os.path.join(here, ".reader_cache.json")

        if not os.path.isfile(self.registry_path):
            raise WarehouseError(f"注册表不存在: {self.registry_path}")
        with open(self.registry_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        self._registry = reg
        self._physical: List[Dict[str, Any]] = reg.get("physical_tables", [])
        self._by_asset: Dict[str, Dict[str, Any]] = {
            t["asset_id"]: t for t in self._physical}
        # 名称 → 候选目录（解析后缓存）；键为物理表名
        self._path_cache: Dict[str, List[str]] = {}
        # 目录名 → 路径列表 一次性全量索引（避免 306 表 × 4 层 glob 重复枚举）
        self._name_index: Optional[Dict[str, List[str]]] = None
        # 目录 → 覆盖信息（带 mtime 校验的缓存）
        self._cov_cache: Dict[str, Dict[str, Any]] = {}
        self._con = duckdb.connect()
        self._con.execute("PRAGMA threads=4;")

    # ------------------------------------------------------------------ #
    # 公共：资产清单 / 信息
    # ------------------------------------------------------------------ #

    def list_assets(self) -> List[str]:
        return sorted(self._by_asset.keys())

    def info(self, asset_id: str) -> Dict[str, Any]:
        """返回某资产的注册表记录 + 解析到的候选目录 + 各目录覆盖。"""
        rec = self._by_asset.get(asset_id)
        if rec is None:
            raise UnknownAssetError(asset_id)
        dirs = self._resolve(rec["physical_tables"] or [asset_id])
        out = {
            "asset_id": asset_id,
            "channel": rec.get("channel"),
            "frequency": rec.get("frequency"),
            "registered_date_column": rec.get("date_column"),
            "primary_key": rec.get("primary_key"),
            "qualification_status": rec.get("qualification_status"),
            "candidates": [],
        }
        for d in dirs:
            cov = self.coverage_dir(d)
            out["candidates"].append({
                "dir": os.path.relpath(d, self.data_root),
                "is_staging": self._is_staging(d),
                "n_files": cov.get("n_files"),
                "date_min": cov.get("min"),
                "date_max": cov.get("max"),
                "n_rows_est": cov.get("n_rows"),
            })
        return out

    # ------------------------------------------------------------------ #
    # 目录解析（核心：名字 → 候选物理目录）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_staging(path: str) -> bool:
        low = path.lower().replace("\\", "/")
        return any(h in low for h in _STAGING_HINTS)

    def _resolve(self, names: Iterable[str]) -> List[str]:
        """把物理表名解析为真实存在的目录（去重、稳定排序）。"""
        result: List[str] = []
        seen = set()
        for name in names:
            if name in self._path_cache:
                for d in self._path_cache[name]:
                    if d not in seen:
                        seen.add(d)
                        result.append(d)
                continue
            found = self._glob_dirs(name)
            self._path_cache[name] = found
            for d in found:
                if d not in seen:
                    seen.add(d)
                    result.append(d)
        return result

    def _build_name_index(self) -> None:
        """一次性遍历 data_root（深度 ≤4），建立 目录名 → 路径列表 索引。
        替代每表 4 层通配 glob（306 表 × glob 会重复枚举整棵树，~6 分钟）。
        深度与原 glob 层数一致，索引语义等价。"""
        idx: Dict[str, List[str]] = {}
        for root, dirs, _files in os.walk(self.data_root):
            rel = os.path.relpath(root, self.data_root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            for d in dirs:
                idx.setdefault(d, []).append(os.path.join(root, d))
            if depth >= 3:              # 子目录(depth4)已收进索引，不再下钻
                dirs[:] = []
        self._name_index = idx

    def _glob_dirs(self, name: str) -> List[str]:
        if self._name_index is None:
            self._build_name_index()
        assert self._name_index is not None
        hits: List[str] = []
        for p in self._name_index.get(name, []):
            if os.path.isdir(p) and self._has_parquet(p):
                hits.append(os.path.normpath(p))
        # 稳定排序：非 staging 优先，再按路径
        hits.sort(key=lambda p: (self._is_staging(p), p))
        return hits

    @staticmethod
    def _has_parquet(directory: str) -> bool:
        for _root, _dirs, files in os.walk(directory):
            if any(f.endswith(".parquet") for f in files):
                return True
        return False

    # ------------------------------------------------------------------ #
    # 覆盖信息（带 mtime 缓存）
    # ------------------------------------------------------------------ #

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self, cache: Dict[str, Any]) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def coverage_dir(self, directory: str) -> Dict[str, Any]:
        """返回某目录的日期覆盖与行数估计（带 mtime 缓存）。"""
        directory = os.path.normpath(directory)
        cache = self._load_cache()
        rec = cache.get(directory)
        try:
            mtime = os.path.getmtime(directory)
        except OSError:
            mtime = 0.0
        if rec and rec.get("mtime") == mtime:
            return rec

        # 探测日期列
        date_col = self._detect_date_col(directory)
        n_files = sum(
            1 for _r, _d, fs in os.walk(directory) for f in fs
            if f.endswith(".parquet"))
        cov: Dict[str, Any] = {
            "mtime": mtime, "n_files": n_files,
            "date_col": date_col, "min": None, "max": None, "n_rows": None}
        if date_col:
            try:
                row = self._con.execute(
                    f"SELECT min({date_col}), max({date_col}), count(*) "
                    f"FROM read_parquet('{directory}/**/*.parquet', "
                    f"union_by_name=true)").fetchone()
                cov["min"], cov["max"], cov["n_rows"] = row
            except Exception as e:  # 目录无数据或 schema 异常
                cov["error"] = str(e)[:200]
        cache[directory] = cov
        self._save_cache(cache)
        return cov

    def _detect_date_col(self, directory: str) -> Optional[str]:
        try:
            cols = [r[0] for r in self._con.execute(
                f"DESCRIBE SELECT * FROM read_parquet("
                f"'{directory}/**/*.parquet', union_by_name=true)"
            ).fetchall()]
        except Exception:
            return None
        for c in cols:
            if _DATE_COL_RE.match(c):
                return c
        return None

    # ------------------------------------------------------------------ #
    # 列校验
    # ------------------------------------------------------------------ #

    def _actual_columns(self, dirs: List[str]) -> List[str]:
        cols: set = set()
        for d in dirs:
            try:
                for r in self._con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet("
                    f"'{d}/**/*.parquet', union_by_name=true)").fetchall():
                    cols.add(r[0])
            except Exception:
                continue
        return sorted(cols)

    # ------------------------------------------------------------------ #
    # 核心读取
    # ------------------------------------------------------------------ #

    def read(self,
             asset_id: str,
             columns: Optional[List[str]] = None,
             start: Optional[str] = None,
             end: Optional[str] = None,
             where: Optional[str] = None,
             date_col: Optional[str] = None,
             require_nonempty: bool = True,
             validate_bounds: bool = True,
             replica: str = "authoritative",
             limit: Optional[int] = None) -> "pd.DataFrame":
        """统一读取接口。

        replica:
          'authoritative'（默认）= 取非 staging 候选目录的**并集**（最完整覆盖）
          'all'             = 取所有候选目录并集（含 staging，仅调试用）
          '<root 子串>'      = 仅用路径含该子串的目录（精确钉选某副本）

        validate_bounds / require_nonempty 共同实现"读越界报错"。
        """
        import pandas as pd  # 延迟导入；环境内有 pandas 3.0.6

        rec = self._by_asset.get(asset_id)
        if rec is None:
            raise UnknownAssetError(
                f"资产 '{asset_id}' 不在注册表。可用前缀示例: "
                + ", ".join(self._suggest(asset_id)))

        names = rec.get("physical_tables") or [asset_id]
        all_dirs = self._resolve(names)
        if not all_dirs:
            raise UnknownAssetError(
                f"资产 '{asset_id}' 注册了但未解析到任何物理目录 "
                f"(physical_tables={names})")

        # 副本选择
        if replica == "all":
            dirs = all_dirs
        elif replica == "authoritative":
            dirs = [d for d in all_dirs if not self._is_staging(d)]
            if not dirs:  # 全是 staging 时也退而求其次
                dirs = all_dirs
        else:
            dirs = [d for d in all_dirs if replica in d.replace("\\", "/")]
            if not dirs:
                raise WarehouseError(
                    f"replica='{replica}' 未匹配到任何目录；候选: "
                    + ", ".join(os.path.relpath(d, self.data_root)
                                for d in all_dirs))

        # 多副本可见告警
        non_staging = [d for d in all_dirs if not self._is_staging(d)]
        if len(non_staging) > 1 and replica == "authoritative":
            lines = []
            for d in all_dirs:
                c = self.coverage_dir(d)
                lines.append(
                    f"  - {os.path.relpath(d, self.data_root)} "
                    f"[staging={self._is_staging(d)}] "
                    f"{c.get('min')}~{c.get('max')} "
                    f"rows≈{c.get('n_rows')}")
            warnings.warn(
                f"资产 '{asset_id}' 存在 {len(non_staging)} 个非 staging 副本，"
                f"已取并集(最完整覆盖)。各副本:\n" + "\n".join(lines),
                ReplicaWarning, stacklevel=2)

        # 日期列
        date_column = (date_col or rec.get("date_column")
                      or self._detect_date_col(dirs[0]))
        if (start or end) and not date_column:
            raise WarehouseError(
                f"资产 '{asset_id}' 无法推断日期列，无法做边界校验；"
                f"请显式传 date_col=")

        # 边界校验：数据实际覆盖
        if validate_bounds and (start or end) and date_column:
            dmin = dmax = None
            for d in dirs:
                c = self.coverage_dir(d)
                if c.get("min") is not None:
                    dmin = c["min"] if dmin is None else min(dmin, c["min"])
                if c.get("max") is not None:
                    dmax = c["max"] if dmax is None else max(dmax, c["max"])
            if dmin is not None and dmax is not None:
                lo = start or dmin
                hi = end or dmax
                # 完全不相交 → 报错
                if str(hi) < str(dmin) or str(lo) > str(dmax):
                    raise OutOfBoundsError(
                        f"查询窗口 [{lo},{hi}] 与数据实际覆盖 "
                        f"[{dmin},{dmax}] 完全不相交。可能：①用了错误副本；"
                        f"②日期列推断错；③资产确实无此区间数据。")
                # 部分越界 → 警告（数据有洞）
                if str(lo) < str(dmin) or str(hi) > str(dmax):
                    warnings.warn(
                        f"查询窗口 [{lo},{hi}] 部分落在数据覆盖 "
                        f"[{dmin},{dmax}] 之外，越界部分将返回空。",
                        ReplicaWarning, stacklevel=2)

        # 列校验
        if columns:
            actual = set(self._actual_columns(dirs))
            missing = [c for c in columns if c not in actual]
            if missing:
                raise ColumnError(
                    f"列不存在: {missing}。可用列(并集)前30: "
                    + ", ".join(sorted(actual)[:30]))

        # 拼 SQL（多目录必须用数组语法传入，否则 DuckDB 报
        # read_parquet(VARCHAR, VARCHAR) 不匹配）
        col_sql = "*" if not columns else ", ".join(columns)
        paths = ", ".join(
            "'" + d.replace("\\", "/").replace("'", "''")
            + "/**/*.parquet'" for d in dirs)
        from_sql = f"read_parquet([{paths}], union_by_name=true)"
        sql = f"SELECT {col_sql} FROM {from_sql}"
        conds = []
        if start and date_column:
            conds.append(f"{date_column} >= '{start}'")
        if end and date_column:
            conds.append(f"{date_column} <= '{end}'")
        if where:
            conds.append(f"({where})")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if limit:
            sql += f" LIMIT {int(limit)}"

        df = self._con.execute(sql).df()
        if require_nonempty and len(df) == 0:
            raise EmptyResultError(
                f"资产 '{asset_id}' 查询返回 0 行（已应用过滤）。"
                f"可能误读了错误副本/错误实体，或过滤过严。"
                f"用 info('{asset_id}') 查看真实覆盖；"
                f"确认无数据可传 require_nonempty=False。")
        return df

    # ------------------------------------------------------------------ #
    # 便捷接口
    # ------------------------------------------------------------------ #

    def read_daily(self, market: str = "A", **kwargs) -> "pd.DataFrame":
        """A 股日线统一入口：默认取最完整日线（tushare/daily 超集）。"""
        asset = {"A": "daily", "US": "us_daily", "HK": "hk_daily"}.get(
            market.upper())
        if asset is None or asset not in self._by_asset:
            raise UnknownAssetError(f"market='{market}' 无对应日线资产")
        return self.read(asset, **kwargs)

    def _suggest(self, asset_id: str, k: int = 6) -> List[str]:
        a = asset_id.lower()
        return [x for x in self.list_assets() if a in x.lower()][:k] or \
            self.list_assets()[:k]

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    import argparse
    ap = argparse.ArgumentParser(description="统一取数层 Reader（P0）")
    ap.add_argument("--asset", required=True, help="资产 id，如 us_daily / daily")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--where", default=None)
    ap.add_argument("--columns", default=None,
                    help="逗号分隔；不传则 *")
    ap.add_argument("--replica", default="authoritative")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--info", action="store_true",
                    help="仅打印资产信息（覆盖/副本）后退出")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--allow-empty", action="store_true")
    args = ap.parse_args()

    r = WarehouseReader()
    if args.info:
        import pprint
        pprint.pprint(r.info(args.asset))
        return
    df = r.read(
        args.asset,
        columns=args.columns.split(",") if args.columns else None,
        start=args.start, end=args.end, where=args.where,
        replica=args.replica, limit=args.limit,
        validate_bounds=not args.no_validate,
        require_nonempty=not args.allow_empty,
    )
    print(f"# {args.asset}: {len(df)} rows, {len(df.columns)} cols")
    with pd_option_context():
        print(df.head(10 if not args.limit else args.limit).to_string())


def pd_option_context():
    import pandas as pd
    return pd.option_context("display.max_columns", 30,
                             "display.width", 200)


if __name__ == "__main__":
    _main()
