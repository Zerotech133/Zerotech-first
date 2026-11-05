"""PIT（point-in-time）申万一级行业映射。

数据来源（Tushare 本地落盘）：

- ``sw_l1_member``：``index_code, con_code, in_date, out_date, is_new``，记录每只
  股票在申万一级行业指数中的纳入/剔除区间；``out_date`` 为空表示当前仍在。
- ``sw_classify_l1``：``index_code, industry_name, level, ...``，指数代码到行业名。

本模块只使用 ``in_date <= date <= out_date``（或 out_date 为空）的区间，并按
``in_date`` 取最新的一条，避免用当前行业快照回填历史（前视/幸存偏差）。

典型用法::

    from pit_industry import load_sw_l1_industry
    industry_map = load_sw_l1_industry()
    ind = industry_map.at("2025-06-30")  # pd.Series(index=ts_code, values=行业名)

研究用途，不依赖 factor_lib。
"""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TUSHARE_DIR = REPO_ROOT / "quant_backtest" / "data" / "tushare"
DEFAULT_MEMBER_DIR = DEFAULT_TUSHARE_DIR / "sw_l1_member"
DEFAULT_CLASSIFY_DIR = DEFAULT_TUSHARE_DIR / "sw_classify_l1"
UNKNOWN_INDUSTRY = "UNKNOWN"

REQUIRED_MEMBER_COLS = {"index_code", "con_code", "in_date"}
REQUIRED_CLASSIFY_COLS = {"index_code", "industry_name"}


def _normalize_date(value) -> pd.Timestamp:
    """把 20250101 / '20250101' / Timestamp 统一为 Timestamp（日期级）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "nat"}:
        return pd.NaT
    s = s.replace("-", "").replace("/", "")[:8]
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce")


def _read_partitioned_parquet(directory: Path | str, required: set[str]) -> pd.DataFrame:
    """读取目录下所有 parquet 分片；目录不存在/为空时报错。"""
    directory = Path(directory)
    files = sorted(glob.glob(str(directory / "*.parquet")))
    if not files:
        raise FileNotFoundError(f"没有 parquet 分片: {directory}")
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{directory} 缺少字段: {sorted(missing)}")
    return df


@dataclass
class PITIndustryMap:
    """按交易日查询申万一级行业的 PIT 映射。"""

    members: pd.DataFrame  # con_code, index_code, in_date, out_date, is_new, industry_name
    source: dict

    def __post_init__(self) -> None:
        m = self.members.copy()
        m["con_code"] = m["con_code"].astype(str)
        m["index_code"] = m["index_code"].astype(str)
        m["in_date"] = pd.to_datetime(m["in_date"], errors="coerce").dt.normalize()
        m["out_date"] = pd.to_datetime(m["out_date"], errors="coerce").dt.normalize()
        m = m.dropna(subset=["con_code", "in_date"])
        # is_new=Y 是当前最新记录；同一代码同一 in_date 时优先 Y。
        m["_is_new_rank"] = (m.get("is_new", "N").astype(str).str.upper() == "Y").astype(int)
        m = m.sort_values(
            ["con_code", "in_date", "_is_new_rank", "index_code"],
            ascending=[True, True, True, True],
        )
        self.members = m.reset_index(drop=True)

    @property
    def min_date(self) -> pd.Timestamp:
        return self.members["in_date"].min()

    @property
    def max_date(self) -> pd.Timestamp:
        out = self.members["out_date"].dropna()
        return out.max() if len(out) else pd.Timestamp.today().normalize()

    def at(self, date) -> pd.Series:
        """返回 ``date`` 当日有效的 ts_code -> 行业名（无记录不返回，由调用方 fillna）。"""
        day = _normalize_date(date)
        if pd.isna(day):
            raise ValueError(f"非法日期: {date!r}")
        m = self.members
        active = (m["in_date"] <= day) & (
            m["out_date"].isna() | (m["out_date"] >= day)
        )
        sub = m.loc[active]
        if sub.empty:
            return pd.Series(dtype=object, name="industry")
        # 同一代码可能因历史区间有重叠，取 in_date 最新的一条（同日优先 is_new=Y）。
        sub = sub.sort_values(
            ["con_code", "in_date", "_is_new_rank", "index_code"],
            ascending=[True, False, False, True],
        ).drop_duplicates("con_code", keep="first")
        return sub.set_index("con_code")["industry_name"].rename("industry")

    def coverage(self, date) -> dict:
        """返回当日覆盖统计，便于报告数据质量。"""
        ser = self.at(date)
        return {
            "date": str(_normalize_date(date).date()),
            "n_codes": int(ser.shape[0]),
            "n_industries": int(ser.nunique()),
        }


def load_sw_l1_industry(member_dir: Path | str | None = None,
                        classify_dir: Path | str | None = None) -> PITIndustryMap:
    """从本地 Tushare 落盘加载申万一级行业 PIT 映射。"""
    member_dir = Path(member_dir) if member_dir else DEFAULT_MEMBER_DIR
    classify_dir = Path(classify_dir) if classify_dir else DEFAULT_CLASSIFY_DIR
    members = _read_partitioned_parquet(member_dir, REQUIRED_MEMBER_COLS)
    classify = _read_partitioned_parquet(classify_dir, REQUIRED_CLASSIFY_COLS)
    if "level" in classify.columns:
        l1 = classify[classify["level"].astype(str).str.upper() == "L1"]
        if len(l1):
            classify = l1
    mapping = (
        classify.drop_duplicates("index_code")
        .set_index("index_code")["industry_name"]
        .astype(str)
        .to_dict()
    )
    members["index_code"] = members["index_code"].astype(str)
    members["industry_name"] = members["index_code"].map(mapping).fillna(UNKNOWN_INDUSTRY)
    if "is_new" not in members.columns:
        members["is_new"] = "N"
    return PITIndustryMap(
        members=members,
        source={
            "member_dir": str(member_dir),
            "classify_dir": str(classify_dir),
            "n_members": int(len(members)),
            "n_codes": int(members["con_code"].nunique()),
            "n_industries": int(members["industry_name"].nunique()),
        },
    )
