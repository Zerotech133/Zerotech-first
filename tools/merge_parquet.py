#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""merge_parquet.py — 分片 parquet 的目录级合并（统一 schema + 流式写入）。

背景
----
``quant_backtest/data/tushare`` 下按“日/票/合约”分片存了约 22.9 万个 parquet，
平均 58 KB。冷读要 24 分钟、热读 2 分钟，成本几乎全耗在打开 22.9 万个 footer 和
文件系统元数据上。本脚本把每个子目录合并成一个 ``<dir>.parquet``。

为什么不能直接用 pyarrow.concat_tables
--------------------------------------
上一轮实测（55 个目录试样）：朴素 concat 只有 28/55 通过，promote_options 51/55，
**统一 schema + 逐列 cast 53/55**。原因是 8395 种“schema”里 99.9% 只是“某一整列在
某个分片里恰好全为空 → arrow 把它推成 null 类型”，列名和顺序其实完全一致，没有字段
增删。所以正确姿势是：先把各分片每列的物理类型收集起来算一个统一 schema，再把每个
分片 cast 过去。剩下 2 个失败的是“一个目录装了两张不同的表”（stk_pledge、
quarantine_prod_overwrite），本脚本会识别并拒绝，不会硬合。

流程
----
1. **扫描**：只打开 footer（``ParquetFile`` 一打开就读 footer，不碰数据页），收集
   每个文件的 (列名, 物理类型) 与行数。0 行文件直接剔除并计数——它们占全量的
   33.88%（77,568 个），合并进来纯属浪费 IO。
2. **统一 schema**：逐列在“非 null 类型”里选。数值统一提升（int64/double → double）；
   字符串族（string/large_string）→ large_string；整列在所有分片里都是空 → 保留
   null 类型（或用 ``--null-as string`` 落成 string）。
3. **写入**：逐文件读 → 按统一 schema 重排列/补齐缺失列 → 逐列 cast → 攒够一个
   row group 再落盘（``ParquetWriter`` 流式，不整目录进内存）。
4. **容错**：单文件读失败或 cast 失败，跳过 + 计数 + 继续，绝不中断整体。

硬约束
------
* ``--out`` 不得等于 ``--src``（防就地覆盖）；
* 写盘前检查目标目录存在、剩余空间够；
* **只读源文件**，脚本内没有任何删除/改名源文件的代码路径。

用法
----
    python -B merge_parquet.py --dry-run --dirs index_weight top_list
    python -B merge_parquet.py --dry-run --json > plan.json
    python -B merge_parquet.py --dirs index_weight --out C:\\项目\\_tmp_T8\\out
    python -B merge_parquet.py --dirs daily-部分 --out ... --max-files 500

退出码：0 全部成功；1 有目录失败/被跳过；2 参数或环境错误；3 空间不足。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception as exc:  # pragma: no cover - 环境缺库时给一句人话
    sys.stderr.write("需要 pyarrow（请用装了 pyarrow 的 Python）：%s\n" % exc)
    raise SystemExit(2)

__version__ = "1.0.0"

#: 默认数据根
DEFAULT_SRC = r"C:\项目\老田的AI助手\quant_backtest\data\tushare"

#: 默认排除（上一轮普查结论：与同目录代码文件重复 / 探针 / 隔离区）
DEFAULT_EXCLUDES = ("*_probe", "quarantine_*", "irm_qa_sh")

#: 状态常量
ST_PLANNED = "planned"
ST_MERGED = "merged"
ST_EMPTY = "skipped_all_empty"
ST_CONFLICT = "skipped_schema_conflict"
ST_FAILED = "failed"
ST_EXISTS = "skipped_out_exists"


# ---------------------------------------------------------------- 小工具


def human_bytes(n):
    """把字节数变成人看得懂的字串。"""
    if n is None:
        return "-"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    v = float(n)
    for u in units:
        if v < 1024.0 or u == units[-1]:
            return ("%.1f %s" % (v, u)) if u != "B" else ("%d B" % int(v))
        v /= 1024.0


def _is_string(t):
    return pa.types.is_string(t) or pa.types.is_large_string(t)


def _is_binary(t):
    return (pa.types.is_binary(t) or pa.types.is_large_binary(t)
            or pa.types.is_fixed_size_binary(t))


def _is_numeric(t):
    return (pa.types.is_integer(t) or pa.types.is_floating(t)
            or pa.types.is_decimal(t))


class Log(object):
    """同时写 stderr 和可选日志文件（长跑必须能落盘复盘）。"""

    def __init__(self, path=None):
        self._fh = None
        if path:
            parent = os.path.dirname(os.path.abspath(path))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------- 类型统一


def promote_types(types):
    """把同一列在各分片里出现的物理类型压成一个统一类型。

    参数 ``types`` 是 ``set[pa.DataType]``（已去重）。返回 ``(统一类型, 说明)``，
    说明为 ``None`` 表示没有分歧，``"numeric"`` / ``"string"`` / ``"timestamp"`` /
    ``"conflict"`` 表示发生了提升或家族冲突。
    """
    concrete = {t for t in types if not pa.types.is_null(t)}
    if not concrete:
        # 所有分片里这一列都是空 → 没法定，保留 null（下游看到的就是全 NaN 列）
        return pa.null(), None
    if len(concrete) == 1:
        return next(iter(concrete)), None

    ordered = sorted(concrete, key=str)

    # 1) 全是数值：int64 / double 冲突统一提升到 double
    if all(_is_numeric(t) for t in concrete):
        if all(pa.types.is_decimal(t) for t in concrete):
            if len({str(t) for t in ordered}) == 1:
                return ordered[0], None
            return pa.float64(), "numeric"
        if any(pa.types.is_floating(t) for t in concrete):
            return pa.float64(), "numeric"
        return pa.int64(), "numeric"

    # 2) 全是字符串族
    if all(_is_string(t) for t in concrete):
        return pa.large_string(), "string"

    # 3) 全是二进制族
    if all(_is_binary(t) for t in concrete):
        return pa.large_binary(), "string"

    # 4) 时间戳：只有单位不同时向上提；时区不一致只能退字符串
    if all(pa.types.is_timestamp(t) for t in concrete):
        tzs = {t.tz for t in concrete}
        if len(tzs) == 1:
            order = {"s": 0, "ms": 1, "us": 2, "ns": 3}
            unit = max((t.unit for t in concrete), key=lambda u: order.get(u, 9))
            return pa.timestamp(unit, tz=ordered[0].tz), "timestamp"
        return pa.large_string(), "conflict"

    # 5) 日期
    if all(pa.types.is_date(t) for t in concrete):
        return pa.date32(), "timestamp"

    # 6) 布尔
    if all(pa.types.is_boolean(t) for t in concrete):
        return pa.bool_(), None

    # 7) 其它家族混搭：退 large_string（cast 失败时会走 str() 兜底，见 _cast_column）
    return pa.large_string(), "conflict"


def _cast_column(col, target):
    """把一列 cast 到目标类型；arrow 不支持的直接 cast 时用 str() 兜底。

    只会在“家族混搭”这种病态目录里走到兜底分支，正常目录全是零拷贝的 arrow cast。
    """
    if col.type == target:
        return col
    try:
        return col.cast(target)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
        if _is_string(target) or _is_binary(target):
            values = [None if v is None else str(v) for v in col.to_pylist()]
            return pa.array(values, type=target)
        raise


def build_schema(col_order, col_types, null_as="null"):
    """由扫描结果算统一 schema。

    返回 ``(schema, promotions, conflicts, null_cols)``：
    * ``promotions``: ``{列名: {"from": [...], "to": "..."}}`` —— 真正发生过提升的列
    * ``conflicts``: 家族冲突（无法无损统一）的列名
    * ``null_cols``: 全分片皆空、保留了 null 类型的列名
    """
    fields = []
    promotions = {}
    conflicts = []
    null_cols = []

    for name in col_order:
        dtype, kind = promote_types(col_types[name])
        if kind == "conflict":
            conflicts.append(name)
        if len({str(t) for t in col_types[name] if not pa.types.is_null(t)}) > 1:
            promotions[name] = {
                "from": sorted({str(t) for t in col_types[name]}),
                "to": str(dtype),
            }
        if pa.types.is_null(dtype):
            null_cols.append(name)
            if null_as == "string":
                dtype = pa.large_string()
                promotions.setdefault(name, {"from": ["null"], "to": "string"})
        fields.append(pa.field(name, dtype))

    return pa.schema(fields), promotions, conflicts, null_cols


# ---------------------------------------------------------------- 文件枚举


def list_parquet(dir_path):
    """列出目录下的 *.parquet（非递归），返回按文件名排序的 [(path, size)]。"""
    out = []
    with os.scandir(dir_path) as it:
        for entry in it:
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".parquet"):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            out.append((entry.path, size))
    out.sort(key=lambda item: os.path.basename(item[0]).lower())
    return out


def excluded(name, patterns):
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return pat
    return None


def ensure_out_dir(out_dir):
    """写盘前确保输出目录存在（不存在就建；已存在的不动）。"""
    if os.path.isdir(out_dir):
        return False
    os.makedirs(out_dir, exist_ok=True)
    return True


def check_free_space(out_dir, need_bytes):
    """写盘前确认目标盘剩余空间够。返回 (够不够, 剩余字节)。"""
    free = shutil.disk_usage(out_dir).free
    return free >= need_bytes, free


# ---------------------------------------------------------------- 核心


def scan_dir(dir_name, files, progress_every, log):
    """只读 footer，算行数 / 统一 schema 素材。"""
    empty = 0
    per_file = []
    failed = []
    total_rows = 0
    bytes_in = 0

    for idx, (path, size) in enumerate(files, 1):
        if progress_every and idx % progress_every == 0:
            log("  [%s] scan %d/%d 行数=%d 空文件=%d"
                % (dir_name, idx, len(files), total_rows, empty))
        try:
            with pq.ParquetFile(path) as pf:
                rows = pf.metadata.num_rows
                schema = pf.schema_arrow
                names = tuple(schema.names)
                types = tuple(schema.types)
        except Exception as exc:  # 坏文件 / 半截文件
            failed.append({"file": os.path.basename(path),
                           "error": "%s: %s" % (type(exc).__name__, exc)})
            continue

        if rows == 0:
            empty += 1
            continue

        per_file.append((path, rows, names, types))
        total_rows += rows
        bytes_in += size

    col_order = []
    col_types = {}
    col_sets = set()
    for _path, _rows, names, types in per_file:
        col_sets.add(frozenset(names))
        for name, dtype in zip(names, types):
            if name not in col_types:
                col_types[name] = set()
                col_order.append(name)
            col_types[name].add(dtype)

    # “一个目录装两张表”检测：列集合互相不是子集关系 → 真冲突
    multi_table = False
    if len(col_sets) > 1:
        maximal = [s for s in col_sets if not any(s < other for other in col_sets)]
        multi_table = len(maximal) > 1

    return {
        "per_file": per_file,
        "col_order": col_order,
        "col_types": col_types,
        "col_sets": col_sets,
        "multi_table": multi_table,
        "rows_planned": total_rows,
        "bytes_in": bytes_in,
        "files_skipped_empty": empty,
        "scan_failed": failed,
    }


def merge_dir(dir_name, files, args, log):
    """处理一个目录：扫描 → 统一 schema → 流式写单文件。"""
    started = time.time()
    stats = {
        "dir": dir_name,
        "status": ST_PLANNED,
        "out_path": None,
        "files_in": len(files),
        "files_skipped_empty": 0,
        "files_failed": 0,
        "rows_out": 0,
        "bytes_out": None,
        "bytes_in": 0,
        "columns": 0,
        "row_groups": None,
        "promotions": {},
        "conflicts": [],
        "null_cols": [],
        "error": None,
        "failed_samples": [],
        "elapsed_sec": 0.0,
    }

    scan = scan_dir(dir_name, files, args.progress_every, log)
    stats["files_skipped_empty"] = scan["files_skipped_empty"]
    stats["files_failed"] = len(scan["scan_failed"])
    stats["failed_samples"] = scan["scan_failed"][:5]
    stats["bytes_in"] = scan["bytes_in"]
    stats["rows_out"] = scan["rows_planned"]

    if not scan["per_file"]:
        stats["status"] = ST_EMPTY
        stats["error"] = "没有非空 parquet"
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats

    if scan["multi_table"] and not args.force_union:
        stats["status"] = ST_CONFLICT
        stats["error"] = ("目录内有 %d 组互不包含的列集合（像是两张表）；"
                          "确认要硬合请加 --force-union"
                          % len(scan["col_sets"]))
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats

    schema, promotions, conflicts, null_cols = build_schema(
        scan["col_order"], scan["col_types"], args.null_as)
    stats["promotions"] = promotions
    stats["conflicts"] = conflicts
    stats["null_cols"] = null_cols
    stats["columns"] = len(schema)

    col_names = schema.names
    # --dry-run 时 --out 可以省略，此时不拼输出路径（也就不做存在性检查）
    out_path = os.path.join(args.out, "%s.parquet" % dir_name) if args.out else None
    stats["out_path"] = out_path

    if args.dry_run:
        stats["bytes_out"] = scan["bytes_in"]  # 上界估计：合并不新增数据
        stats["status"] = ST_PLANNED
        stats["elapsed_sec"] = round(time.time() - started, 3)
        log("  [%s] 计划：%d 文件 → %d 行 / %d 列，源 %s，跳过空文件 %d"
            % (dir_name, len(scan["per_file"]), stats["rows_out"], len(schema),
               human_bytes(scan["bytes_in"]), stats["files_skipped_empty"]))
        return stats

    if os.path.exists(out_path) and not args.overwrite:
        stats["status"] = ST_EXISTS
        stats["error"] = "输出已存在（加 --overwrite 覆盖）：%s" % out_path
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats

    # ---- 硬约束 2：写盘前确认输出目录存在 + 空间够
    try:
        if ensure_out_dir(args.out):
            log("  [%s] 创建输出目录 %s" % (dir_name, args.out))
        need = int(scan["bytes_in"] * 1.05) + (16 << 20)
        ok, free = check_free_space(args.out, need)
    except OSError as exc:
        stats["status"] = ST_FAILED
        stats["error"] = "输出目录不可用：%s" % exc
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats
    if not ok:
        stats["status"] = ST_FAILED
        stats["error"] = ("空间不足：需要约 %s，目标盘剩 %s"
                          % (human_bytes(need), human_bytes(free)))
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats

    # ---- 真正写盘
    written_rows = 0
    failed = []
    buffer = []
    buffered_rows = 0
    writer = None
    try:
        writer = pq.ParquetWriter(out_path, schema)
        for idx, (path, _rows, _names, _types) in enumerate(scan["per_file"], 1):
            if args.progress_every and idx % args.progress_every == 0:
                log("  [%s] write %d/%d 已写行数=%d"
                    % (dir_name, idx, len(scan["per_file"]), written_rows))
            try:
                table = pq.read_table(path)
                arrays = []
                for name, field in zip(col_names, schema):
                    if name in table.column_names:
                        col = table.column(name)
                        arrays.append(_cast_column(col, field.type))
                    else:
                        arrays.append(pa.nulls(table.num_rows, field.type))
                table = pa.Table.from_arrays(arrays, schema=schema)
            except Exception as exc:
                failed.append({"file": os.path.basename(path),
                               "error": "%s: %s" % (type(exc).__name__, exc)})
                continue

            if table.num_rows == 0:
                continue
            buffer.append(table)
            buffered_rows += table.num_rows
            if buffered_rows >= args.row_group_rows:
                writer.write_table(pa.concat_tables(buffer))
                written_rows += buffered_rows
                buffer = []
                buffered_rows = 0

        if buffer:
            writer.write_table(pa.concat_tables(buffer))
            written_rows += buffered_rows
    except Exception:
        stats["status"] = ST_FAILED
        stats["error"] = traceback.format_exc(limit=3)
        # 写盘中断可能留下半截产物：如实记下大小并提示重跑覆盖（本脚本不删任何文件）
        if out_path and os.path.exists(out_path):
            stats["bytes_out"] = os.path.getsize(out_path)
            stats["error"] = ("写盘中断，已留下半截产物 %s；重跑请加 --overwrite\n%s"
                              % (human_bytes(stats["bytes_out"]), stats["error"]))
        stats["elapsed_sec"] = round(time.time() - started, 3)
        return stats
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

    stats["rows_out"] = written_rows
    stats["files_failed"] += len(failed)
    stats["failed_samples"] = (scan["scan_failed"] + failed)[:5]
    stats["bytes_out"] = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    stats["row_groups"] = pq.ParquetFile(out_path).metadata.num_row_groups
    stats["status"] = ST_MERGED
    stats["elapsed_sec"] = round(time.time() - started, 3)
    log("  [%s] 完成：%d 行，%s → %s（压缩比 %.2fx），失败 %d"
        % (dir_name, written_rows, human_bytes(scan["bytes_in"]),
           human_bytes(stats["bytes_out"]),
           (scan["bytes_in"] / stats["bytes_out"]) if stats["bytes_out"] else 0.0,
           stats["files_failed"]))
    return stats


# ---------------------------------------------------------------- CLI


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="merge_parquet.py",
        description="把分片 parquet 目录合并成单文件（统一 schema + 流式写入）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python -B merge_parquet.py --dry-run --dirs index_weight top_list\n"
               "  python -B merge_parquet.py --dirs index_weight --out C:\\项目\\_tmp_T8\\out\n")
    parser.add_argument("--src", default=DEFAULT_SRC, help="源数据根（默认 %(default)s）")
    parser.add_argument("--out", default=None,
                        help="输出目录；不得等于 --src。--dry-run 时可省略")
    parser.add_argument("--dirs", nargs="+", default=None,
                        help="只处理这些子目录（可多值）；默认全部（受 --exclude 约束）")
    parser.add_argument("--dry-run", action="store_true", help="只出计划，不写盘")
    parser.add_argument("--json", action="store_true", help="stdout 输出机器可读 JSON")
    parser.add_argument("--max-files", type=int, default=None,
                        help="每个目录最多处理多少个 parquet（试跑用）")
    parser.add_argument("--exclude", action="append", default=None, metavar="GLOB",
                        help="额外排除的目录名 glob（可重复，逗号亦可）")
    parser.add_argument("--no-default-excludes", action="store_true",
                        help="不用默认排除（%s）" % ",".join(DEFAULT_EXCLUDES))
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")
    parser.add_argument("--force-union", action="store_true",
                        help="即使目录像“两张表”也强行并成并集（不推荐）")
    parser.add_argument("--null-as", choices=("null", "string"), default="null",
                        help="全空列的输出类型：null（保真，默认）或 string（兼容性更好）")
    parser.add_argument("--row-group-rows", type=int, default=512000,
                        help="每攒够这么多行落一个 row group（默认 %(default)s）")
    parser.add_argument("--progress-every", type=int, default=2000,
                        help="每处理多少文件报一次进度（0 关闭）")
    parser.add_argument("--log", default=None, help="把日志额外追加到这个文件")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser.parse_args(argv)


def _flatten_excludes(values):
    out = []
    for raw in values or ():
        for part in str(raw).replace(";", ",").split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def main(argv=None):
    args = parse_args(argv)
    log = Log(args.log)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        log("错误：--src 不是目录：%s" % src)
        log.close()
        return 2

    # 硬约束 1：--out 不得等于 --src
    out = None
    if args.out:
        out = os.path.abspath(args.out)
        if os.path.normcase(os.path.realpath(out)) == os.path.normcase(os.path.realpath(src)):
            log("错误：--out 不得等于 --src（会就地覆盖源数据）：%s" % src)
            log.close()
            return 2
    elif not args.dry_run:
        log("错误：非 --dry-run 必须给 --out")
        log.close()
        return 2
    args.out = out

    if out:
        try:
            if os.path.commonpath([os.path.realpath(out),
                                   os.path.realpath(src)]) == os.path.realpath(src):
                log("警告：--out 在 --src 里面，产物会落进数据根；建议挪到数据根之外")
        except ValueError:  # 不同盘符
            pass

    excludes = list(DEFAULT_EXCLUDES) if not args.no_default_excludes else []
    excludes += _flatten_excludes(args.exclude)

    # 选目录
    if args.dirs:
        names = []
        bad = []
        for raw in args.dirs:
            name = os.path.basename(raw.rstrip("\\/"))
            if os.path.isdir(os.path.join(src, name)):
                if name not in names:
                    names.append(name)
            else:
                bad.append(name)
        if bad:
            log("错误：--dirs 里这些子目录不存在：%s" % ", ".join(bad))
            log.close()
            return 2
        skipped = [(n, excluded(n, excludes)) for n in names]
        names = [n for n, pat in skipped if not pat]
        for n, pat in skipped:
            if pat:
                log("跳过目录 %s（命中排除规则 %s）" % (n, pat))
    else:
        names = sorted(
            d for d in os.listdir(src)
            if os.path.isdir(os.path.join(src, d))
            and not d.startswith(".")          # 数据根里有隐藏的 .git，别去碰
            and not excluded(d, excludes))

    if not names:
        log("没有可处理的目录（src=%s）" % src)
        log.close()
        return 0

    log("merge_parquet %s | src=%s | out=%s | 目录 %d 个 | dry_run=%s"
        % (__version__, src, out or "-", len(names), args.dry_run))

    started = time.time()
    results = []
    for idx, name in enumerate(names, 1):
        dir_path = os.path.join(src, name)
        files = list_parquet(dir_path)
        if args.max_files is not None:
            files = files[:args.max_files]
        log("[%d/%d] %s：%d 个 parquet" % (idx, len(names), name, len(files)))
        if not files:
            results.append({
                "dir": name, "status": ST_EMPTY, "out_path": None,
                "files_in": 0, "files_skipped_empty": 0, "files_failed": 0,
                "rows_out": 0, "bytes_out": 0, "bytes_in": 0, "columns": 0,
                "row_groups": None, "promotions": {}, "conflicts": [],
                "null_cols": [], "error": "目录内没有 parquet", "failed_samples": [],
                "elapsed_sec": 0.0,
            })
            continue
        results.append(merge_dir(name, files, args, log))

    elapsed = round(time.time() - started, 3)

    # 汇总
    totals = {
        "dirs_selected": len(results),
        "dirs_merged": sum(1 for r in results if r["status"] == ST_MERGED),
        "dirs_planned": sum(1 for r in results if r["status"] == ST_PLANNED),
        "dirs_skipped": sum(1 for r in results
                            if r["status"] in (ST_EMPTY, ST_CONFLICT, ST_EXISTS)),
        "dirs_failed": sum(1 for r in results if r["status"] == ST_FAILED),
        "files_in": sum(r["files_in"] for r in results),
        "files_skipped_empty": sum(r["files_skipped_empty"] for r in results),
        "files_failed": sum(r["files_failed"] for r in results),
        "rows_out": sum(r["rows_out"] or 0 for r in results),
        "bytes_in": sum(r["bytes_in"] or 0 for r in results),
        "bytes_out": sum(r["bytes_out"] or 0 for r in results),
        "bytes_out_is_estimate": bool(args.dry_run),
        "elapsed_sec": elapsed,
        "free_bytes": None,
    }
    if out and os.path.isdir(out):
        try:
            totals["free_bytes"] = shutil.disk_usage(out).free
        except OSError:
            pass

    payload = {
        "script": "merge_parquet.py",
        "version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "src": src,
        "out": out,
        "excludes": excludes,
        "max_files": args.max_files,
        "null_as": args.null_as,
        "totals": totals,
        "dirs": results,
    }

    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        _print_human(payload)

    log("汇总：%d 目录 / %d 文件 / 跳过空文件 %d / 失败 %d / %d 行 / %s → %s / 耗时 %.1fs"
        % (totals["dirs_selected"], totals["files_in"],
           totals["files_skipped_empty"], totals["files_failed"],
           totals["rows_out"], human_bytes(totals["bytes_in"]),
           human_bytes(totals["bytes_out"]), elapsed))
    log.close()

    if totals["dirs_failed"]:
        return 1
    if totals["dirs_skipped"] and not args.dry_run:
        return 1
    return 0


def _print_human(payload):
    """给人看的表格。"""
    print("merge_parquet %s  生成于 %s  dry_run=%s"
          % (payload["version"], payload["generated_at"], payload["dry_run"]))
    print("src : %s" % payload["src"])
    print("out : %s" % (payload["out"] or "-"))
    print("")
    head = ("目录", "状态", "文件", "跳过空", "失败", "行数", "源大小", "产物")
    print("%-22s %-24s %7s %6s %5s %12s %11s %11s"
          % (head[0], head[1], head[2], head[3], head[4], head[5], head[6], head[7]))
    print("-" * 108)
    for r in payload["dirs"]:
        print("%-22s %-24s %7d %6d %5d %12d %11s %11s"
              % (r["dir"], r["status"], r["files_in"], r["files_skipped_empty"],
                 r["files_failed"], r["rows_out"], human_bytes(r["bytes_in"]),
                 human_bytes(r["bytes_out"])))
        if r["conflicts"]:
            print("    冲突列(%d)：%s" % (len(r["conflicts"]), ", ".join(r["conflicts"][:8])))
        if r["promotions"]:
            for col, info in list(r["promotions"].items())[:6]:
                print("    提升 %s: %s → %s" % (col, "|".join(info["from"]), info["to"]))
        if r["null_cols"]:
            print("    全空列(%d)：%s" % (len(r["null_cols"]), ", ".join(r["null_cols"][:8])))
        if r["error"]:
            print("    说明：%s" % r["error"].strip().splitlines()[0])
    print("-" * 108)
    t = payload["totals"]
    print("合计：%d 目录（合并 %d / 计划 %d / 跳过 %d / 失败 %d）"
          % (t["dirs_selected"], t["dirs_merged"], t["dirs_planned"],
             t["dirs_skipped"], t["dirs_failed"]))
    print("     文件 %d，跳过空文件 %d，失败 %d，输出行数 %d"
          % (t["files_in"], t["files_skipped_empty"], t["files_failed"], t["rows_out"]))
    print("     源 %s → 产物 %s%s，耗时 %ss"
          % (human_bytes(t["bytes_in"]), human_bytes(t["bytes_out"]),
             "（估计）" if t["bytes_out_is_estimate"] else "", t["elapsed_sec"]))


if __name__ == "__main__":
    raise SystemExit(main())
