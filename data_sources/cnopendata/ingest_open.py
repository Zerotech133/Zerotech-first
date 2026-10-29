#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0：把 CnOpenData OPEN 数据集的原始文件解析为 parquet 并登记 manifest。

设计约束（依据 2026-10-21 数据湖规范侦察）：
- 数据根 = quant_backtest/data（AGENTS.md 立章），本脚本只写 quant_backtest/data/cnopendata/
- 原件留在 data_sources/cnopendata/open/<id>/raw/，不移动、不删除
- 每个物理文件 -> 一个 part_000N.parquet
- 扫描生成 manifest，人工不手填行数
- 不改 build_catalog.py（那属代码变更，须另立工单）

WO-QT-20261021-101（身份补齐，仅作用于多工作表 Excel 与省份类列）：
- 多工作表 Excel（如 czyx 一个工作簿 6 张数据表、同挂「省×年」网格）原本
  无维度列可区分来源，6 个分片的 (prov, year) 互相重复 → 声明该主键会被
  datamart 去重静默吞行。故为**每个分片**补一列 dim = 工作表名；单表输入
  不加该列（避免污染其他数据集表结构）。
- 省份类列（PROVINCE_COLUMNS）去掉所有空白（含全角 U+3000）并 strip：
  实测 czyx.prov 存在「上  海」与「上海」并存，同一省一年两套写法。
  **只清洗这三列**，不对全部字符串列做全局清洗（数值列不动、不改 dtype）。

WO-QT-20261021-106（财税三表省份双轨命名归一 + dim 白名单）：
- ① **DIM_TABLES 白名单**：原规则「一个物理文件解析出多张数据表就加 dim」
  会把 chinese-dictionary-of-financial-sentiment（一个 xlsx 含 positive /
  negative 两表）在下次全量重跑时静默多出 dim 列（schema 2 → 3），把一张
  与维度无关的静态词表改写掉。改为按目录名（item.name，含 cnod- 前缀）
  白名单：只有 cnod-czyx 这类「同一省×年网格上的多张维度表」才补 dim。
- ② **prov_std 归一列**：人大财税所三表（czyx / yspl / gjzfssfc）的省份列
  双轨并存——czyx 片 0000 用简称（上海/内蒙古/云南），片 0001–0004 用官方
  全称（上海市/内蒙古自治区/云南省）；yspl、gjzfssfc 全表用简称。下游按
  省份跨表 join 必须先把两套写法收敛到同一取值。
  **原列（prov / 省份）一律不改写**，另加 prov_std = 官方全称。
  映射不手工硬编：从 czyx 原件里取全称集合与简称集合，对每个简称找**唯一**
  满足 full.startswith(short) 的全称；0 个或多个候选即报错停下并打印冲突
  （不猜）。gjzfssfc 的 省份代码（11/12 这类数字代码）不在处理范围。

用法：
    python ingest_open.py            # 解析全部
    python ingest_open.py --dry-run  # 只报告不落盘
    python ingest_open.py --only cnod-czyx   # 只重建单表
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import logging

BASE = Path(__file__).resolve().parent
OPEN_DIR = BASE / "open"
DATA_ROOT = BASE.parents[1] / "quant_backtest" / "data" / "cnopendata"
MANIFEST = DATA_ROOT / "_manifest.json"

SUPPORTED = {".txt", ".csv", ".tsv", ".xlsx", ".xls", ".parquet"}
SKIP = {".zip", ".rar", ".7z", ".pdf", ".md", ".json"}


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


NON_DATA_SHEETS = ("说明", "备注", "readme", "封面", "声明", "目录")


def is_non_data_sheet(name: str, df: pd.DataFrame) -> bool:
    """判定说明页/封面页，避免把它们当数据入库。

    两类典型特征：
    1. 表名含"说明/备注/封面"等字样（用子串匹配，因为实际叫「数据说明」）；
    2. 表头大面积是 Unnamed 列——说明首行是标题而不是字段名。
    """
    if any(k in str(name).lower() for k in NON_DATA_SHEETS):
        return True
    if df.shape[1] <= 1 and df.shape[0] < 5:
        return True
    unnamed = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
    if df.shape[1] and unnamed / df.shape[1] > 0.5:
        return True
    return False


def read_frames(path: Path) -> list[tuple[str, pd.DataFrame, str]]:
    """按扩展名读入，返回 [(标签, DataFrame, 说明)]。

    Excel 多工作表逐个返回——只取第一张会把「说明」页当成数据，
    真实词表反而漏掉。非数据页在此处按规则跳过。
    """
    ext = path.suffix.lower()
    if ext == ".parquet":
        return [("", pd.read_parquet(path), "parquet 直读")]

    if ext in {".xlsx", ".xls"}:
        book = pd.ExcelFile(path)
        out: list[tuple[str, pd.DataFrame, str]] = []
        for sheet in book.sheet_names:
            df = book.parse(sheet)
            if is_non_data_sheet(sheet, df):
                continue
            out.append((str(sheet), df, f"Excel 工作表「{sheet}」（共 {len(book.sheet_names)} 张）"))
        if not out:
            raise RuntimeError("Excel 全部工作表均被判为非数据页")
        return out

    # 文本类：先试 utf-8，再 gbk；分隔符先嗅探，再退逗号/制表符
    last_err: Exception | None = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        for sep in (None, ",", "\t", "|"):
            try:
                df = pd.read_csv(
                    path, encoding=enc, sep=sep, engine="python",
                    on_bad_lines="warn", skip_blank_lines=True,
                    dtype=str, keep_default_na=False,
                )
                if df.shape[1] >= 1:
                    return [("", df, f"文本 编码={enc} 分隔符={sep or '嗅探'}")]
            except Exception as exc:  # noqa: BLE001
                last_err = exc
    raise RuntimeError(f"无法解析文本文件：{last_err}")



def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # 丢弃全空列与全空行（keep_default_na=False 时是空串）
    df = df.loc[:, ~(df.astype(str).apply(lambda s: s.str.strip() == "").all())]
    df = df.loc[~(df.astype(str).apply(lambda s: s.str.strip() == "").all(axis=1))]
    return df.reset_index(drop=True)


# ── WO-QT-20261021-101：身份补齐 ──────────────────────────────────────────
# 省份类列名单（唯一清洗白名单）：只有这三列做空白归一，其余字符串列一律
# 保持原样——全局清洗会把「带空格即不同实体」的业务语义抹平，且会牵连
# 其他数据集的表结构。
PROVINCE_COLUMNS = ("prov", "省份", "省份代码")

# 需剔除的空白字符：常规空白（\t\n\r\f\v + 半角空格）之外，追加
#   U+3000 表意空格（全角空格，中文表头最常见）
#   U+00A0 不换行空格（Excel 复制粘贴常见）
#   U+200B 零宽空格（爬虫/PDF 转表常见，肉眼不可见但是独立码位）
_WS_RE = re.compile(r"[\s\u3000\u00a0\u200b]+")


# ── WO-QT-20261021-106 ①：dim 白名单 ─────────────────────────────────────
# 键是**目录名**（main 里的 item.name，带 cnod- 前缀），不是去前缀的 table。
# 只有「一个工作簿里多张表、且这些表共享同一 (省×年) 网格」的数据集才需要
# dim 区分来源；其余多工作表文件（词表 positive/negative 两页）加 dim 是
# 无端改表结构（schema 2 → 3）。
DIM_TABLES = {"cnod-czyx"}

# ── WO-QT-20261021-106 ②：省份标准名 prov_std ───────────────────────────
# 新列名（原列保持源值不动）。取值 = 官方全称（…省/…市/…自治区）。
PROV_STD_COL = "prov_std"
# 省份**名称**列候选（按优先级）。gjzfssfc 的 省份代码 是 11/12 这类数字
# 代码，故意不在此列——它不是名称，归一没有意义。
PROV_NAME_COLUMNS = ("省份", "prov")
# 官方全称后缀白名单：用于把取值集合切成「全称集合」与「简称集合」。
PROV_FULL_SUFFIXES = ("省", "市", "自治区")
# 建映射的唯一参照表（人大财税所同源，片 0000 简称 / 片 0001–0004 全称）。
PROV_REFERENCE_TABLE = "czyx"
_PROV_MAP_CACHE: dict[str, str] | None = None


def _norm_prov(value: object) -> str:
    """省份取值归一：去全部空白（同 normalize_province_columns 口径）。

    空值（None / NaN / pd.NA）统一返回空串，由调用方决定如何计数，
    绝不把缺失值变成 "nan" 这种假取值。
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "data_sources/cnopendata/ingest_open.py", exc)
    return _WS_RE.sub("", str(value)).strip()


def _is_full_province(name: str) -> bool:
    """官方全称判定：以 省/市/自治区 结尾（简称一律不是）。"""
    return any(name.endswith(suffix) for suffix in PROV_FULL_SUFFIXES)


def _prov_values_from_frame(df: pd.DataFrame) -> list[str]:
    """从一帧里抠出省份名称列的全部非空取值（已归一）。"""
    for col in PROV_NAME_COLUMNS:
        if col in df.columns:
            return [v for v in (_norm_prov(x) for x in df[col].tolist()) if v]
    return []


def _czyx_prov_values() -> list[str]:
    """取 czyx 的省份取值全集（简称 + 全称），作为建映射的唯一数据依据。

    优先级：原件 raw/（权威，能从零重建）→ 已落盘分片（原件被清理时兜底）。
    两条路都拿不到就报错——映射不允许凭记忆硬编。
    """
    values: list[str] = []
    raw_dir = OPEN_DIR / f"cnod-{PROV_REFERENCE_TABLE}" / "raw"
    if raw_dir.is_dir():
        for path in sorted(p for p in raw_dir.iterdir() if p.is_file()):
            if path.suffix.lower() not in SUPPORTED:
                continue
            try:
                frames = read_frames(path)
            except Exception:  # noqa: BLE001
                continue
            for _label, frame, _note in frames:
                values.extend(_prov_values_from_frame(clean(frame)))
    if not values:
        parts = sorted((DATA_ROOT / PROV_REFERENCE_TABLE).glob("part_*.parquet"))
        for part in parts:
            values.extend(_prov_values_from_frame(pd.read_parquet(part)))
    if not values:
        raise RuntimeError(
            f"找不到省份映射参照数据：{raw_dir} 与 "
            f"{DATA_ROOT / PROV_REFERENCE_TABLE}/part_*.parquet 均无可读省份列")
    return values


def build_province_map() -> dict[str, str]:
    """建立「简称 → 官方全称」确定性映射（本回内缓存）。

    规则：对每个简称，在**全称集合**里找唯一满足 full.startswith(short) 的
    全称。0 个候选（无对应全称）或 ≥2 个候选（前缀歧义）都**报错停下**并
    打印冲突明细——宁可失败也不猜（错映射会污染跨表 join 的省份维度）。

    附加 1:1 校验：简称数 == 全称数、目标值无重复、每个全称都被用到。
    任何一条不成立即报错。
    """
    global _PROV_MAP_CACHE
    if _PROV_MAP_CACHE is not None:
        return _PROV_MAP_CACHE

    values = sorted({v for v in _czyx_prov_values() if v})
    full_set = sorted(v for v in values if _is_full_province(v))
    short_set = sorted(v for v in values if not _is_full_province(v))
    if not short_set or not full_set:
        raise RuntimeError(
            f"省份映射参照不完整：简称 {len(short_set)} 个 / 全称 {len(full_set)} 个"
            f"（取值全集 {values}）")

    mapping: dict[str, str] = {}
    conflicts: list[str] = []
    for short in short_set:
        candidates = [full for full in full_set if full.startswith(short)]
        if len(candidates) != 1:
            conflicts.append(f"{short} -> {candidates or '无候选'}")
            continue
        mapping[short] = candidates[0]

    used = list(mapping.values())
    duplicated = sorted({t for t in used if used.count(t) > 1})
    unused = [full for full in full_set if full not in used]
    problems: list[str] = []
    if conflicts:
        problems.append("前缀匹配非唯一：" + "；".join(conflicts))
    if duplicated:
        problems.append("目标值重复：" + "、".join(duplicated))
    if unused:
        problems.append("全称未被映射：" + "、".join(unused))
    if len(short_set) != len(full_set):
        problems.append(f"简称 {len(short_set)} 个 ≠ 全称 {len(full_set)} 个，非 1:1")
    if problems:
        raise RuntimeError(
            f"{PROV_REFERENCE_TABLE} 省份简称→全称映射无法唯一确定，已停止：\n  "
            + "\n  ".join(problems)
            + f"\n  简称集合：{short_set}\n  全称集合：{full_set}")

    _PROV_MAP_CACHE = mapping
    print(f"[prov_std] {PROV_REFERENCE_TABLE} 简称→官方全称映射 "
          f"{len(mapping)} 条（1:1 校验通过，供人工复核）：")
    for idx, (short, full) in enumerate(sorted(mapping.items()), 1):
        print(f"  {idx:2d}. {short:<6} -> {full}")
    return mapping


def add_prov_std_column(
    df: pd.DataFrame, table: str, mapping: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, object] | None]:
    """追加 prov_std 列（官方全称）；原省份名称列**原样保留**。

    - 名称列按 PROV_NAME_COLUMNS 优先级取（省份 优先于 prov），
      所以 gjzfssfc 用 省份（不是 省份代码）。
    - 无名称列（czyx 末片 part_0005 宽表）直接跳过，不造列。
    - 已映射不到、且本身也不是全称的取值 = 数据漂移，报错停下并列出。
    返回 (df, 统计) —— 统计含源列名/去重数/空值数，供 main 打印。
    """
    source_col = next((c for c in PROV_NAME_COLUMNS if c in df.columns), None)
    if source_col is None:
        return df, None
    series = df[source_col]
    if not (pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)):
        return df, None

    normalized = series.map(_norm_prov)
    known_full = set(mapping.values())
    unknown = sorted({v for v in normalized.tolist()
                      if v and v not in mapping and v not in known_full})
    if unknown:
        raise RuntimeError(
            f"{table}.{source_col} 出现映射表外的省份取值 {unknown}，"
            f"无法确定官方全称，已停止（不猜）")

    df = df.copy()
    df[PROV_STD_COL] = normalized.map(
        lambda v: mapping.get(v, v) if v else None)
    stats: dict[str, object] = {
        "source_col": source_col,
        "nunique": int(df[PROV_STD_COL].dropna().nunique()),
        "nulls": int(df[PROV_STD_COL].isna().sum()),
    }
    return df, stats


def add_dim_column(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """多工作表来源标识：把工作表名落成 dim 列（原位追加，不改动既有列序）。

    仅当一个物理文件解析出多张数据表时调用。dim 是这些分片唯一互不重叠的
    身份列——缺了它，(prov, year) 在 6 张维度表之间天然重复，任何声明该
    主键的消费方都会把 1995 行去重塌成 879 行。
    """
    df = df.copy()
    df["dim"] = str(label)
    return df


def normalize_province_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, int, int]]]:
    """省份类列空白归一 → (df, [(列名, 归一前去重数, 归一后去重数)])。

    只处理 PROVINCE_COLUMNS 且必须是字符串/对象列：数值列（如 gjzfssfc 的
    省份代码 int64）抠不出空白，硬转 str 反而会改 dtype、破坏下游数值消费。
    空值原样保留（dropna 计数，不把 NaN 变成一个 "nan" 取值）。
    """
    touched: list[tuple[str, int, int]] = []
    df = df.copy()
    for col in PROVINCE_COLUMNS:
        if col not in df.columns:
            continue
        series = df[col]
        if not (pd.api.types.is_object_dtype(series)
                or pd.api.types.is_string_dtype(series)):
            continue
        before = int(series.dropna().astype(str).nunique())
        cleaned = series.map(
            lambda v: _WS_RE.sub("", str(v)).strip() if pd.notna(v) else v)
        after = int(cleaned.dropna().astype(str).nunique())
        df[col] = cleaned
        touched.append((col, before, after))
    return df, touched


def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"version": 1, "tables": {}}
    return {"version": 1, "tables": {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    if not OPEN_DIR.is_dir():
        print(f"没有原始目录：{OPEN_DIR}", file=sys.stderr)
        return 2

    manifest = load_manifest()
    ok, skipped, failed = [], [], []

    for item in sorted(OPEN_DIR.iterdir()):
        if not item.is_dir():
            continue
        if args.only and item.name != args.only:
            continue
        raw = item / "raw"
        if not raw.is_dir():
            skipped.append((item.name, "无 raw 目录"))
            continue
        meta_path = item / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        table = item.name.removeprefix("cnod-")

        # 同源别名（不同条目指向同一物理文件）只入湖一次，避免重复计行
        if meta.get("alias_of"):
            skipped.append((item.name, f"同源别名，并入 {meta['alias_of']}，不重复入湖"))
            continue

        files = sorted(p for p in raw.iterdir() if p.is_file())
        if not files:
            skipped.append((item.name, "raw 为空"))
            continue

        parts, errors, notes = [], [], []
        pid = 0
        for f in files:
            ext = f.suffix.lower()
            if ext in SKIP or ext in {".rtf"}:
                skipped.append((f"{item.name}/{f.name}", f"非表格格式 {ext}，留存不解析"))
                continue
            if ext not in SUPPORTED:
                skipped.append((f"{item.name}/{f.name}", f"未支持格式 {ext}"))
                continue
            try:
                frames = read_frames(f)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f.name}: {exc}")
                continue
            for label, df, note in frames:
                df = clean(df)
                if df.empty:
                    errors.append(f"{f.name}[{label or '-'}]: 解析后为空表")
                    continue
                # WO-QT-20261021-101 ① / WO-QT-20261021-106 ①：
                # 只有 DIM_TABLES 白名单内的目录（当前仅 cnod-czyx）才补
                # dim=工作表名。其余多工作表文件（如情感词典 positive/
                # negative 两页）不加，避免静默改表结构（2 列 → 3 列）。
                # 单表输入（parquet/文本/单表 Excel）同样不加。
                if item.name in DIM_TABLES and len(frames) > 1 and label:
                    df = add_dim_column(df, label)
                    note = f"{note}；补 dim 列=「{label}」"
                # WO-QT-20261021-101 ②：省份类列空白归一（白名单三列）
                df, prov_stats = normalize_province_columns(df)
                for col, n_before, n_after in prov_stats:
                    print(f"  [归一] {table}/{f.name}[{label or '-'}] "
                          f"{col}: {n_before} -> {n_after} 个不同取值")
                    note = f"{note}；{col} 空白归一 {n_before}->{n_after}"
                # WO-QT-20261021-106 ②：省份双轨命名归一 → 追加 prov_std
                # （官方全称）。原列 prov/省份 一律不改写；无名称列的分片
                # （czyx 末片 part_0005 宽表）跳过。
                if any(c in df.columns for c in PROV_NAME_COLUMNS):
                    df, prov_std_stats = add_prov_std_column(
                        df, table, build_province_map())
                    if prov_std_stats:
                        print(f"  [prov_std] {table}/{f.name}[{label or '-'}] "
                              f"{prov_std_stats['source_col']} -> {PROV_STD_COL}: "
                              f"{prov_std_stats['nunique']} 个取值，"
                              f"空值 {prov_std_stats['nulls']}")
                        note = (f"{note}；{PROV_STD_COL}="
                                f"{prov_std_stats['nunique']} 个官方全称")
                out_dir = DATA_ROOT / table
                target = out_dir / f"part_{pid:04d}.parquet"
                if not args.dry_run:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    df.to_parquet(target, index=False)
                parts.append({
                    "file": f.name, "sheet": label or None, "parquet": target.name,
                    "rows": int(len(df)), "cols": int(df.shape[1]),
                    "columns": list(df.columns)[:30],
                    "bytes_src": f.stat().st_size, "sha256_src": sha256_of(f),
                })
                notes.append(f"{f.name}[{label or '-'}]: {note}")
                pid += 1

        if not parts:
            # 只有报错才算失败；无可用表格（如体积过大主动暂缓）归入跳过
            if errors:
                failed.append((item.name, "; ".join(errors)))
            else:
                skipped.append((item.name, "无可解析表格（原件留存，未入湖）"))
            continue

        total_rows = sum(p["rows"] for p in parts)

        # 清理上一轮遗留的分片：本轮工作表数变少时，旧 part 会留在盘上，
        # 回读行数就会多于 manifest（实测 czyx 多出 135 行）。
        # 不删除，移入 _stale/ 保留证据。
        if not args.dry_run:
            table_dir = DATA_ROOT / table
            keep = {p["parquet"] for p in parts}
            stale = [q for q in table_dir.glob("part_*.parquet") if q.name not in keep]
            if stale:
                stale_dir = table_dir / "_stale"
                stale_dir.mkdir(parents=True, exist_ok=True)
                for q in stale:
                    q.replace(stale_dir / f"{q.stem}.{tz_now().strftime('%Y%m%d%H%M%S')}.parquet")
                notes.append(f"清理上一轮遗留分片 {len(stale)} 个 -> _stale/")

        manifest["tables"][table] = {
            "id": item.name,
            "name": meta.get("name", table),
            "source": "cnopendata",
            "source_url": meta.get("source_url"),
            "landing_page": meta.get("landing_page"),
            "license_note": meta.get("license_note", ""),
            "fetched_at": meta.get("fetched_at"),
            "parsed_at": tz_now().isoformat(timespec="seconds"),
            "rows": total_rows,
            "files": parts,
            "parse_notes": notes,
            "errors": errors,
        }
        ok.append((table, total_rows, len(parts), "; ".join(errors)))

    if not args.dry_run and ok:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        manifest["updated_at"] = tz_now().isoformat(timespec="seconds")
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'[dry-run] ' if args.dry_run else ''}入库完成 {len(ok)} 张表")
    for t, rows, nf, err in ok:
        print(f"  OK  {t}: {rows} 行 / {nf} 文件" + (f"  (告警: {err})" if err else ""))
    if skipped:
        print(f"跳过 {len(skipped)} 项：")
        for name, why in skipped:
            print(f"  --  {name}: {why}")
    if failed:
        print(f"失败 {len(failed)} 项：", file=sys.stderr)
        for name, why in failed:
            print(f"  !!  {name}: {why}", file=sys.stderr)
        return 1
    print(f"数据根: {DATA_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
