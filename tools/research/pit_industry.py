"""PIT（point-in-time）申万行业映射（L1/L2）。

数据来源（Tushare 本地落盘）：

- ``sw_l1_member`` / ``sw_l2_member``：``index_code, con_code, in_date, out_date,
  is_new``，记录每只股票在申万 L1/L2 行业指数中的纳入/剔除区间；
  ``out_date`` 为空表示当前仍在。
- ``sw_classify_l1``：``index_code, industry_name, level, ...``，指数代码到行业名。

本模块只使用 ``in_date <= date <= out_date``（或 out_date 为空）的区间，并按
``in_date`` 取最新的一条，避免用当前行业快照回填历史（前视/幸存偏差）。

申万 L2 历史成员由 ``tools/data_sources/tushare_sw_member.py`` 逐指数拉取：
``sw_l2_member`` 覆盖 131 个当前/历史 L2 指数；少数已停用指数（2014 版分类）
无成员数据，缺失代码由调用方按 ``UNKNOWN`` 处理。

典型用法::

    from pit_industry import load_sw_l1_industry, load_sw_l2_industry
    l1 = load_sw_l1_industry()
    l2 = load_sw_l2_industry()
    ind = l2.at("2025-06-30")  # pd.Series(index=ts_code, values=行业名)

研究用途，不依赖 factor_lib。
"""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TUSHARE_DIR = REPO_ROOT / "quant_backtest" / "data" / "tushare"
DEFAULT_L1_MEMBER_DIR = DEFAULT_TUSHARE_DIR / "sw_l1_member"
DEFAULT_L2_MEMBER_DIR = DEFAULT_TUSHARE_DIR / "sw_l2_member"
DEFAULT_MEMBER_DIR = DEFAULT_L1_MEMBER_DIR
DEFAULT_CLASSIFY_DIR = DEFAULT_TUSHARE_DIR / "sw_classify_l1"
UNKNOWN_INDUSTRY = "UNKNOWN"
LEVELS = ("L1", "L2", "L3")

REQUIRED_MEMBER_COLS = {"index_code", "con_code", "in_date"}
REQUIRED_CLASSIFY_COLS = {"index_code", "industry_name"}
REQUIRED_SNAPSHOT_COLS = {"ts_code", "l2_code", "in_date"}
DEFAULT_INDEX_MEMBER_ALL_DIR = DEFAULT_TUSHARE_DIR / "index_member_all"


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


def default_member_dir(level: str = "L1") -> Path:
    level = str(level).upper()
    if level == "L1":
        return DEFAULT_L1_MEMBER_DIR
    if level == "L2":
        return DEFAULT_L2_MEMBER_DIR
    return DEFAULT_TUSHARE_DIR / f"sw_{level.lower()}_member"


@dataclass
class PITIndustryMap:
    """按交易日查询申万行业的 PIT 映射。"""

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


def _classify_mapping(level: str, classify_dir: Path | str | None = None) -> dict[str, str]:
    """读取 ``index_code -> industry_name`` 映射（按 ``level`` 过滤）。"""
    classify_dir = Path(classify_dir) if classify_dir else DEFAULT_CLASSIFY_DIR
    classify = _read_partitioned_parquet(classify_dir, REQUIRED_CLASSIFY_COLS)
    if "level" in classify.columns:
        sub = classify[classify["level"].astype(str).str.upper() == level]
        if len(sub):
            classify = sub
    return (
        classify.drop_duplicates("index_code")
        .set_index("index_code")["industry_name"]
        .astype(str)
        .to_dict()
    )


def load_sw_industry(level: str = "L1",
                     member_dir: Path | str | None = None,
                     classify_dir: Path | str | None = None) -> PITIndustryMap:
    """从本地 Tushare 落盘加载申万 ``level`` 行业 PIT 映射。"""
    level = str(level).upper()
    if level not in LEVELS:
        raise ValueError(f"level 必须是 {LEVELS} 之一")
    member_dir = Path(member_dir) if member_dir else default_member_dir(level)
    classify_dir = Path(classify_dir) if classify_dir else DEFAULT_CLASSIFY_DIR
    members = _read_partitioned_parquet(member_dir, REQUIRED_MEMBER_COLS)
    mapping = _classify_mapping(level, classify_dir)
    members["index_code"] = members["index_code"].astype(str)
    members["industry_name"] = members["index_code"].map(mapping).fillna(UNKNOWN_INDUSTRY)
    if "is_new" not in members.columns:
        members["is_new"] = "N"
    return PITIndustryMap(
        members=members,
        source={
            "level": level,
            "member_dir": str(member_dir),
            "classify_dir": str(classify_dir),
            "n_members": int(len(members)),
            "n_codes": int(members["con_code"].nunique()),
            "n_industries": int(members["industry_name"].nunique()),
        },
    )


def load_sw_l1_industry(member_dir: Path | str | None = None,
                        classify_dir: Path | str | None = None) -> PITIndustryMap:
    """从本地 Tushare 落盘加载申万一级行业 PIT 映射。"""
    return load_sw_industry("L1", member_dir=member_dir, classify_dir=classify_dir)


def load_sw_l2_industry(member_dir: Path | str | None = None,
                        classify_dir: Path | str | None = None) -> PITIndustryMap:
    """从本地 Tushare 落盘加载申万二级行业 PIT 映射。"""
    return load_sw_industry("L2", member_dir=member_dir, classify_dir=classify_dir)


def load_sw_l2_industry_with_snapshot_fallback(
    member_dir: Path | str | None = None,
    classify_dir: Path | str | None = None,
    snapshot_dir: Path | str | None = None,
) -> PITIndustryMap:
    """加载 SW L2 历史成员，并用当前 ``index_member_all`` 快照补齐缺失代码。

    背景：``sw_l2_member`` 由 Tushare ``index_member`` 逐指数拉取，2023 上半年
    科创板/北交所大量股票未出现在历史成员表中。``index_member_all`` 是当前
    快照（``is_new=Y``、无 ``out_date``），但带有 ``in_date``。对完全缺失于历史
    成员表的代码，若 ``in_date <= date``，说明其在目标日已经是成员，可以作为
    兜底；行业名取当前 L2 分类映射，若期间发生再分类仍有前视，报告须标注。

    该映射的 ``source`` 会记录 ``fallback`` 与 ``n_fallback_codes``，便于报告
    区分纯 PIT 与 snapshot-assisted 口径；默认的 ``load_sw_l2_industry`` 不受影响。
    """
    base = load_sw_industry("L2", member_dir=member_dir, classify_dir=classify_dir)
    snapshot_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_INDEX_MEMBER_ALL_DIR
    snapshot = _read_partitioned_parquet(snapshot_dir, REQUIRED_SNAPSHOT_COLS)
    l2_names = _classify_mapping("L2", classify_dir)

    snap = pd.DataFrame({
        "con_code": snapshot["ts_code"].astype(str),
        "index_code": snapshot["l2_code"].astype(str),
        "in_date": pd.to_datetime(snapshot["in_date"], errors="coerce").dt.normalize(),
        "out_date": pd.to_datetime(snapshot["out_date"], errors="coerce").dt.normalize()
        if "out_date" in snapshot.columns else pd.Series(pd.NaT, index=snapshot.index),
        "is_new": snapshot["is_new"].astype(str) if "is_new" in snapshot.columns else "Y",
    })
    snap["industry_name"] = snap["index_code"].map(l2_names).fillna(UNKNOWN_INDUSTRY)
    snap = snap.dropna(subset=["con_code", "in_date", "index_code"])
    snap["_is_new_rank"] = (snap["is_new"].str.upper() == "Y").astype(int)
    # 同一代码在当前快照里只应有一条；若结构异常则按 in_date 最新、is_new 优先。
    snap = (
        snap.sort_values(["con_code", "in_date", "_is_new_rank", "index_code"])
        .drop_duplicates("con_code", keep="last")
    )

    # 只补“历史成员表没有覆盖到的缺口”：
    # - 代码完全不在历史成员表；或
    # - 快照 in_date 落在该代码所有历史区间的空隙中（不是某个区间的内部），
    #   此时快照区间从 in_date 起开放，后续历史区间若 in_date 更晚会自然覆盖它。
    # 若快照 in_date 落在某个历史区间内部，则跳过，避免用当前 L2 标签覆盖历史分类。
    spans: dict[str, list[tuple[pd.Timestamp, pd.Timestamp | pd.NaT]]] = {}
    for code, group in base.members.groupby("con_code", sort=False):
        spans[str(code)] = list(zip(group["in_date"], group["out_date"]))
    add_rows = []
    for row in snap.itertuples(index=False):
        code = str(row.con_code)
        start = row.in_date
        intervals = spans.get(code)
        if intervals:
            inside = any(
                (start >= begin) and (pd.isna(end) or start <= end)
                for begin, end in intervals
            )
            if inside:
                continue
        add_rows.append({
            "index_code": str(row.index_code),
            "con_code": code,
            "in_date": start,
            "out_date": row.out_date,
            "is_new": row.is_new,
            "industry_name": row.industry_name,
        })
    extra = pd.DataFrame(add_rows)
    columns = ["index_code", "con_code", "in_date", "out_date", "is_new", "industry_name"]
    if extra.empty:
        extra = pd.DataFrame(columns=columns)
    members = pd.concat([base.members[columns], extra[columns]], ignore_index=True)

    source = dict(base.source)
    source.update({
        "fallback": "index_member_all_snapshot",
        "snapshot_dir": str(snapshot_dir),
        "n_fallback_rows": int(len(extra)),
        "n_fallback_codes": int(extra["con_code"].nunique()),
        "fallback_note": (
            "仅补历史成员表未覆盖的缺口（代码缺失，或快照 in_date 落在历史区间空隙）；"
            "in_date<=date 才生效；行业名取当前 L2 分类，若期间再分类仍有剩余前视"
        ),
    })
    return PITIndustryMap(members=members, source=source)
