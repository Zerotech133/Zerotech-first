# -*- coding: utf-8 -*-
"""TR1 事件—价格影响跟踪复盘：路径、交易日历与只读数据访问工具。

数据根可经环境变量覆盖（测试或迁移机器时使用）：

- ``TRACK_CURATED_DIR``：tushare_curated 数据根
- ``TRACK_TDX_DIR``：tdx 分片表数据根
- ``TRACK_WORK_ROOT``：产物根（默认 ``C:\\项目\\analysis_work\\track_review``）
- ``TRACK_DB``：SQLite 路径覆盖（默认 ``<work_root>/track.db``）

除了 ``ensure_dirs`` / ``write_json`` 会写 ``work_root`` 下的产物目录外，所有
读取函数均为只读，不会修改 curated/tdx 数据仓。
"""
from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass

import pandas as pd

DEFAULT_CURATED_DIR = r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated"
DEFAULT_TDX_DIR = r"C:\项目\老田的AI助手\quant_backtest\data\tdx"
DEFAULT_WORK_ROOT = r"C:\项目\analysis_work\track_review"

# 公告关键词方向（与 TR0 原型保持一致，保证事件数可比）
POS_WORDS = ("回购", "增持", "中标", "预增", "扭亏", "盈利", "上修", "买入", "推荐", "签署", "合作", "分红")
NEG_WORDS = ("减持", "立案", "问询", "诉讼", "质押", "解禁", "预减", "首亏", "续亏", "下修", "处罚", "终止", "风险提示")

# (窗口名, 相对 d0 起始偏移, 结束偏移)；CAR 为区间内 AR 之和（单位：百分点）
WINDOWS = (
    ("pre_5_1", -5, -1),
    ("d0_1", 0, 1),
    ("d0_3", 0, 3),
    ("d0_5", 0, 5),
    ("d0_10", 0, 10),
)
WINDOW_NAMES = tuple(w[0] for w in WINDOWS)
WINDOW_LABELS = {
    "pre_5_1": "CAR[-5,-1]",
    "d0_1": "CAR[0,1]",
    "d0_3": "CAR[0,3]",
    "d0_5": "CAR[0,5]",
    "d0_10": "CAR[0,10]",
}

MARKET_INDEX = "000300.SH"
SW_MEMBER_FILE = "sw_l1_member.parquet"
SW_CLASSIFY_FILE = "sw_classify_l1.parquet"


@dataclass(frozen=True)
class Paths:
    """数据根与产物根配置。"""

    curated: str = DEFAULT_CURATED_DIR
    tdx: str = DEFAULT_TDX_DIR
    work_root: str = DEFAULT_WORK_ROOT

    @property
    def artifacts_dir(self) -> str:
        return os.path.join(self.work_root, "artifacts")

    @property
    def toplist_dir(self) -> str:
        return os.path.join(self.work_root, "toplist")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.work_root, "logs")

    @property
    def db_path(self) -> str:
        return os.environ.get("TRACK_DB") or os.path.join(self.work_root, "track.db")


def default_paths() -> Paths:
    """按环境变量（若有）构造默认 Paths。"""
    return Paths(
        curated=os.environ.get("TRACK_CURATED_DIR") or DEFAULT_CURATED_DIR,
        tdx=os.environ.get("TRACK_TDX_DIR") or DEFAULT_TDX_DIR,
        work_root=os.environ.get("TRACK_WORK_ROOT") or DEFAULT_WORK_ROOT,
    )


def ensure_dirs(paths: Paths | None = None) -> Paths:
    """创建产物目录；不触碰 curated/tdx 数据根。"""
    paths = paths or default_paths()
    for d in (paths.work_root, paths.artifacts_dir, paths.toplist_dir, paths.logs_dir):
        os.makedirs(d, exist_ok=True)
    return paths


def normalize_date(value: object) -> str | None:
    """把日期规范成 ``YYYYMMDD`` 字符串；无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            value = int(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "nat", "null"}:
        return None
    s = s.replace("-", "").replace("/", "")
    if "." in s:  # 20250103.0 之类的数值字符串
        head = s.split(".", 1)[0]
        if head.isdigit() and len(head) == 8:
            s = head
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return None


def read_curated(
    name: str,
    columns: list[str] | None = None,
    filters: list[tuple] | None = None,
    paths: Paths | None = None,
) -> pd.DataFrame:
    """读取 curated 单表（只读）；文件缺失或读取失败时返回空 DataFrame。"""
    paths = paths or default_paths()
    path = os.path.join(paths.curated, name)
    empty = pd.DataFrame(columns=list(columns or []))
    if not os.path.isfile(path):
        return empty
    try:
        return pd.read_parquet(path, columns=columns, filters=filters)
    except Exception:
        try:
            return pd.read_parquet(path)
        except Exception:
            return empty


def read_tdx_ordered(
    name: str,
    columns: list[str] | None = None,
    paths: Paths | None = None,
) -> pd.DataFrame:
    """按分片文件名顺序读取 tdx 整表（保留物理行序）。

    质押等「顺序去重」类事件需要与 tr0_card 相同的行序：tr0 逐片读取后
    直接按文件顺序做 drop_duplicates(keep="last")，不做日期排序。
    """
    paths = paths or default_paths()
    directory = os.path.join(paths.tdx, name)
    files = sorted(glob.glob(os.path.join(directory, "*.parquet")))
    wanted = list(columns) if columns else None
    frames: list[pd.DataFrame] = []
    for file_path in files:
        try:
            frame = pd.read_parquet(file_path, columns=wanted)
        except Exception:
            try:
                frame = pd.read_parquet(file_path)
            except Exception:
                continue
        if wanted:
            frame = frame[[c for c in wanted if c in frame.columns]]
        if len(frame):
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=wanted or [])
    return pd.concat(frames, ignore_index=True)


def read_tdx(
    name: str,
    columns: list[str] | None = None,
    date_col: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    paths: Paths | None = None,
) -> pd.DataFrame:
    """读取 tdx 分片表（只读），尽量用 pyarrow dataset 过滤下推。

    参数
    ----
    name: tdx 下的表目录名，如 ``ts_anns_d``
    columns: 只读取的列（减少 IO）；None 表示全部列
    date_col/date_start/date_end: 按日期列剪枝（字符串 ``YYYYMMDD``）
    codes: 按 ``ts_code`` 过滤
    """
    paths = paths or default_paths()
    directory = os.path.join(paths.tdx, name)
    files = sorted(glob.glob(os.path.join(directory, "*.parquet")))
    wanted = list(columns) if columns else None
    if not files:
        return pd.DataFrame(columns=wanted or [])

    start = normalize_date(date_start)
    end = normalize_date(date_end)
    code_list = [str(c) for c in codes] if codes else None

    try:
        import pyarrow as pa
        import pyarrow.dataset as ds

        dataset = ds.dataset(directory, format="parquet")
        schema = dataset.schema
        expr = None
        if date_col and (start or end) and date_col in schema.names:
            field_type = schema.field(date_col).type
            if pa.types.is_integer(field_type):
                if start:
                    expr = ds.field(date_col) >= int(start)
                if end:
                    new = ds.field(date_col) <= int(end)
                    expr = new if expr is None else expr & new
            else:
                if start:
                    expr = ds.field(date_col) >= start
                if end:
                    new = ds.field(date_col) <= end
                    expr = new if expr is None else expr & new
        if code_list and "ts_code" in schema.names:
            new = ds.field("ts_code").isin(code_list)
            expr = new if expr is None else expr & new
        table = dataset.to_table(columns=wanted, filter=expr)
        return table.to_pandas()
    except Exception:
        pass

    # 兜底：逐文件读取后过滤（兼容个别分片 schema 不一致）
    frames: list[pd.DataFrame] = []
    for file_path in files:
        try:
            frame = pd.read_parquet(file_path, columns=wanted)
        except Exception:
            try:
                frame = pd.read_parquet(file_path)
            except Exception:
                continue
        if date_col and (start or end) and date_col in frame.columns:
            dates = frame[date_col].map(normalize_date)
            mask = pd.Series(True, index=frame.index)
            if start:
                mask &= dates >= start
            if end:
                mask &= dates <= end
            frame = frame[mask]
        if code_list and "ts_code" in frame.columns:
            frame = frame[frame["ts_code"].astype(str).isin(code_list)]
        if len(frame):
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=wanted or [])
    result = pd.concat(frames, ignore_index=True, sort=False)
    if wanted:
        result = result[[c for c in wanted if c in result.columns]]
    return result


def load_stock_names(codes: list[str] | tuple[str, ...] | None = None, paths: Paths | None = None) -> dict[str, str]:
    """读取股票代码 → 名称映射（stock_basic 当前快照）。"""
    filters = [("ts_code", "in", [str(c) for c in codes])] if codes else None
    df = read_curated("stock_basic.parquet", columns=["ts_code", "name"], filters=filters, paths=paths)
    if df is None or len(df) == 0:
        return {}
    return {str(k): str(v) for k, v in zip(df["ts_code"], df["name"])}


def load_trade_days(start: str | None = None, end: str | None = None, paths: Paths | None = None) -> list[str]:
    """SSE 交易日列表（``trade_cal.is_open == 1``），升序。"""
    df = read_curated("trade_cal.parquet", columns=["cal_date", "is_open"], paths=paths)
    if df is None or len(df) == 0:
        # 兜底：用沪深300 的交易日
        bench = read_curated(
            "index_daily.parquet",
            columns=["trade_date"],
            filters=[("ts_code", "==", MARKET_INDEX)],
            paths=paths,
        )
        if bench is None or len(bench) == 0:
            return []
        days = sorted({d for d in bench["trade_date"].map(normalize_date).tolist() if d})
    else:
        open_df = df[df["is_open"].astype(int) == 1]
        days = sorted({d for d in open_df["cal_date"].map(normalize_date).tolist() if d})
    start = normalize_date(start)
    end = normalize_date(end)
    if start:
        days = [d for d in days if d >= start]
    if end:
        days = [d for d in days if d <= end]
    return days


def write_json(path: str, payload: object) -> str:
    """写 JSON（UTF-8、中文不转义），自动创建父目录。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    return path


def write_text(path: str, text: str) -> str:
    """写 UTF-8 文本，自动创建父目录。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def today_str() -> str:
    """当天日期（``YYYYMMDD``，仅用于 CLI 默认值）。"""
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d")
