# -*- coding: utf-8 -*-
"""因子库 v1.1 · 因子注册表 + PIT 特征 + 20 日 CAR 按日横截面 IC 评测。

本模块是「选股因子/量学形态 → 可复现评测」的统一底座：
  - FACTORS（dict[str, FactorSpec]）注册表：内置 17 个因子（9 个量学形态可计算版 + 8 个通用因子），
    启动时自动注册 Tushare factor_list 的 B1 行情 86 / B2 Alpha101 101 / B3 财报 84 个因子
    （合计 288 个；映射全表见 `map` 子命令，B3 的 dividend_yield_3y_avg 因无数据源标记 blocked）；
  - 所有因子函数签名统一：fn(panel) -> DataFrame（日期 × 代码），只允许使用「当日及以前」的数据；
  - 20 日 CAR 口径：T 日收盘出信号 → T+1 收盘入场 → 持有 20 个交易日后（T+21 收盘）卖出，
    收益 = 个股区间涨跌幅 - 等权市场同期涨跌幅 - 单边 30bp 成本（与 H24/H26/H29 口径一致）；
  - 评测输出：按日横截面 Spearman IC（均值/IR/t/胜率）、IS/OOS 切分、分年 IC、
    block bootstrap（复用 tools/research/stat_utils.py）、五分位分组收益（top/bottom quintile，年化）。

PIT（Point-In-Time）纪律（判例见 research/MEETING.md）：
  - 因子只允许用 shift(+n) / rolling(过去窗口) 等「向后看」算子，不允许负数 lag（见测试静态检查）；
  - **确认型形态（黄金柱等）信号必须落在确认完成日**，次日（T+1 收盘）才有收益；
    即 golden_pillar 的 signal[T] = 基柱在 T-3、且 T-2/T-1/T 三日不破实顶 + 逐日缩量 → T 为确认日。
    任何「后 N 日不破/缩量」条件都不得按形态日索引（MEETING.md 判例 H29 第三类陷阱）。
  - 财务数据用 ann_date（公告日）≤ T 的最新一期 ROE（见 load_latest_fina 同口径）；
    估值/换手用 T 日 daily_basic（收盘后可得，T+1 入场不构成前视）；
  - 股票池过滤（tradable）只用 T 日可知的价格/成交额 + 上市日期（静态），不含当前 ST 名称等后验信息。

输出可复现：固定 bootstrap seed、按日聚合、无随机抽样（除 bootstrap 外）；同一数据/参数重跑数值一致。

用法：
    python tools/research/factor_lib.py list
    python tools/research/factor_lib.py list --json
    python tools/research/factor_lib.py eval --start 20260101 --end 20261030 \
        --factors mom20,double_vol,golden_pillar --is-end 20260630 \
        --out research/reports/factor_lib_demo_20261101.json

输出 JSON schema（顶层）：
    {meta: {lib, version, generated_at, requested, effective, is_end, horizon, cost,
            universe, market, baseline, data, notes, runtime_sec, ...},
     factors: {name: {name, family, kind, direction, description, source,
                      n_obs, n_days, signal, ic, ic_directed, by_year, is_oos,
                      bootstrap: {ic_directed, long_short_spread},
                      quintiles: {method, n_groups, groups: {Q1..Q5}, top_minus_bottom},
                      event_split}}}

依赖：pandas / numpy / pyarrow（与项目 py312 一致）。
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import sys
import time
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# ---------------------------------------------------------------- 路径/常量
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"

# 复用项目统计修正工具（block bootstrap / 非重叠 t）
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import stat_utils  # noqa: E402

LIB_VERSION = "1.2.0"  # 1.2.0: B2 Alpha101 + B3 PIT 财报因子接入；B1 行情因子 86 个
DEFAULT_HORIZON = 20          # 持有期（交易日）：T+1 收盘入场，T+1+h 收盘卖出
DEFAULT_COST = 0.003          # 单边成本 30bp（买卖各 15bp 近似为单边 30bp，与 H24/H29 同口径）
DEFAULT_MIN_CROSS_SECTION = 50  # 单日横截面最少有效股票数（少于此不计 IC 日）
DEFAULT_SEED = 42
DEFAULT_BLOCK = 20            # block bootstrap 块长（≥ 持有期，修正重叠窗口自相关）
WARMUP_DAYS = 40              # 载入面板时多载入的历史交易日数（供 20 日滚动窗口使用）
AMOUNT_UNIT = 1000.0          # tushare daily.amount 单位为千元 → 元
MIN_PRICE = 2.0               # 可交易股票池：股价下限（元）
MIN_AMOUNT_YUAN = 5e7         # 可交易股票池：成交额下限（元）
MIN_LIST_DAYS = 365           # 可交易股票池：上市日历天数下限（≈250 个交易日）
REQUIRED_FACTORS = (
    # 任务验收点名必须存在的因子（≥15 个中的硬性清单）
    "vol_ratio5", "double_vol", "shrink_vol", "up_vol_down", "yin_yang",
    "near_high_vol", "golden_pillar", "zero_vol", "aokou",
    "mom20", "rev5", "low_turnover", "pe", "roe", "quality_trap",
)

DAILY_FIELDS = ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount")

# daily_basic 扩展字段（Tushare factor_list B1 批次需要：换手/估值/股本/市值）
DAILY_BASIC_FIELDS = ("pe_ttm", "pb", "ps_ttm", "dv_ratio", "turnover_rate",
                      "total_share", "float_share", "free_share", "total_mv",
                      "circ_mv", "volume_ratio")


# ---------------------------------------------------------------- 数值小工具
def _num(x, nd: int = 4):
    """转 float 并保留 nd 位小数；NaN/inf/None → None（保证 JSON 可序列化）。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, nd)


def _to_np(df: pd.DataFrame | None, dtype=np.float64) -> np.ndarray | None:
    if df is None:
        return None
    return np.asarray(df, dtype=dtype)


def _rank_2d(a: np.ndarray, average: bool = True) -> np.ndarray:
    """沿 axis=1（按日期横截面）计算秩，NaN 位置返回 NaN。

    average=True：同值取平均秩（Spearman 正确口径）；
    average=False：同值按列顺序（ts_code 升序）打破并列，用于恰好等分的五分组。
    """
    a = np.asarray(a, dtype=float)
    n, m = a.shape
    if n == 0 or m == 0:
        return np.full(a.shape, np.nan)
    valid = np.isfinite(a)
    a2 = np.where(valid, a, np.inf)
    order = np.argsort(a2, axis=1, kind="quicksort" if average else "stable")
    sv = np.take_along_axis(a2, order, axis=1)
    pos = np.arange(m, dtype=np.float64)[None, :]
    if average:
        starts = np.ones((n, m), dtype=bool)
        starts[:, 1:] = sv[:, 1:] != sv[:, :-1]
        first = np.maximum.accumulate(np.where(starts, pos, 0.0), axis=1)
        ends = np.empty((n, m), dtype=bool)
        ends[:, :-1] = sv[:, 1:] != sv[:, :-1]
        ends[:, -1] = True
        last = np.minimum.accumulate(np.where(ends, pos, float(m - 1))[:, ::-1], axis=1)[:, ::-1]
        sorted_rank = (first + last) / 2.0 + 1.0
    else:
        sorted_rank = np.broadcast_to(np.arange(1, m + 1, dtype=np.float64), (n, m))
    ranks = np.empty_like(sorted_rank)
    np.put_along_axis(ranks, order, sorted_rank, axis=1)
    ranks[~valid] = np.nan
    return ranks


def _spearman_ic(x: np.ndarray, y: np.ndarray, min_n: int) -> np.ndarray:
    """逐日横截面 Spearman IC：返回长度 = 日期数的 IC 数组（无效日 NaN）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n_dates = x.shape[0] if x.ndim == 2 else 0
    if n_dates == 0:
        return np.full((0,), np.nan)
    mask = np.isfinite(x) & np.isfinite(y)
    if not mask.any():
        return np.full((n_dates,), np.nan)
    rx = _rank_2d(np.where(mask, x, np.nan))
    ry = _rank_2d(np.where(mask, y, np.nan))
    n = mask.sum(axis=1).astype(float)
    sx = np.nansum(rx, axis=1)
    sy = np.nansum(ry, axis=1)
    sxx = np.nansum(rx * rx, axis=1)
    syy = np.nansum(ry * ry, axis=1)
    sxy = np.nansum(rx * ry, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cov = n * sxy - sx * sy
        vx = n * sxx - sx * sx
        vy = n * syy - sy * sy
        den = np.sqrt(np.maximum(vx * vy, 0.0))
        ic = cov / np.where(den > 0, den, np.nan)
    ok = (n >= max(3, int(min_n))) & (vx > 0) & (vy > 0) & np.isfinite(ic)
    return np.where(ok, ic, np.nan)


def _newey_west_t(s: pd.Series, lags: int) -> float | None:
    """Newey-West HAC t 值（Bartlett 核），修正 h 日重叠收益导致的自相关。"""
    n = int(len(s))
    if n < 3:
        return None
    x = np.asarray(s, dtype=float)
    mean = float(np.nanmean(x))
    e = x - mean
    gamma0 = float(np.dot(e, e) / n)
    var = gamma0
    for k in range(1, min(int(lags), n - 1) + 1):
        gamma = float(np.dot(e[k:], e[:-k]) / n)
        var += 2.0 * (1.0 - k / float(lags + 1)) * gamma
    if var <= 0:
        return None
    se = math.sqrt(var / n)
    return mean / se if se > 0 else None


def _series_stats(x: pd.Series, hac_lags: int = 0) -> dict:
    """日频序列的均值/标准差/IR/t/胜率（IC 用）。

    hac_lags>0 时 t 用 Newey-West HAC；重叠 h 日前瞻 IC 应传 hac_lags=h。
    """
    s = pd.Series(x, dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "mean": None, "std": None, "ir": None, "t": None, "positive_rate": None}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    ir = mean / std if std > 0 else None
    t_iid = ir * math.sqrt(n) if ir is not None else None
    t = _newey_west_t(s, hac_lags) if hac_lags and n > 1 else t_iid
    return {"n_days": n,
            "mean": _num(mean, 6),
            "std": _num(std, 6) if n > 1 else None,
            "ir": _num(ir, 4),
            "t": _num(t, 3),
            "t_iid": _num(t_iid, 3),
            "positive_rate": _num(float((s > 0).mean()), 4)}


def _return_stats(x: pd.Series, ann_factor: float) -> dict:
    """日频收益序列统计；ann_factor = 252/持有期，用于把 h 日平均 CAR 年化。"""
    s = pd.Series(x, dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "mean_car_pct": None, "std_car_pct": None, "t": None,
                "positive_rate": None, "ann_pct": None}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    t = mean / std * math.sqrt(n) if std > 0 else None
    return {"n_days": n,
            "mean_car_pct": _num(mean * 100.0, 4),
            "std_car_pct": _num(std * 100.0, 4) if n > 1 else None,
            "t": _num(t, 3),
            "positive_rate": _num(float((s > 0).mean()), 4),
            "ann_pct": _num(mean * ann_factor * 100.0, 2)}


def _daily_mean(car: np.ndarray, mask: np.ndarray, dates: list[str]) -> pd.Series:
    """按日计算 mask 内个股 CAR 的等权均值。"""
    cnt = mask.sum(axis=1).astype(float)
    tot = np.nansum(np.where(mask, car, 0.0), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.where(cnt > 0, tot / np.where(cnt > 0, cnt, 1.0), np.nan)
    return pd.Series(daily, index=pd.Index(dates))


def _rolling(df: pd.DataFrame, n: int, min_periods: int | None = None) -> pd.DataFrame:
    """沿时间轴（axis=0）滚动；min_periods 默认 max(2, n//2)，与 H24/H29 口径一致。"""
    if min_periods is None:
        min_periods = max(2, n // 2)
    return df.rolling(n, min_periods=min_periods)


def _event(mask: pd.DataFrame, valid: pd.DataFrame) -> pd.DataFrame:
    """布尔条件 → 0/1 事件因子矩阵；valid=False 处填 NaN（PIT 不可算）。"""
    v = valid.fillna(False).astype(bool)
    m = mask.fillna(False).astype(bool) & v
    return m.astype(float).where(v)


def _nan_frame(p: "Panel") -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=p.dates, columns=p.codes)


# ---------------------------------------------------------------- 面板
class Panel:
    """日期 × 代码 的行情/基本面面板（每个字段一个 DataFrame，index=trade_date, columns=ts_code）。

    fields 常用字段：open/high/low/close/pre_close/pct_chg/vol/amount/adj_close/pe_ttm/
    turnover_rate/roe。缺字段的因子会得到全 NaN（并通过 valid 掩码剔除）。
    eligible（可选 bool DataFrame）：可交易股票池掩码；None 表示全市场。
    """

    def __init__(self, fields: dict[str, pd.DataFrame], eligible: pd.DataFrame | None = None,
                 meta: dict | None = None):
        if not isinstance(fields, dict) or "close" not in fields:
            raise ValueError("Panel 需要 fields dict 且必须包含 'close'")
        base = fields["close"]
        if not isinstance(base, pd.DataFrame):
            raise TypeError("fields['close'] 必须是 DataFrame")
        self.dates = pd.Index([str(d) for d in base.index], name="trade_date")
        self.codes = pd.Index([str(c) for c in base.columns], name="ts_code")
        norm: dict[str, pd.DataFrame] = {}
        for key, value in fields.items():
            if not isinstance(value, pd.DataFrame):
                raise TypeError(f"字段 {key} 必须是 DataFrame")
            norm[str(key)] = value.reindex(index=base.index, columns=base.columns)
        self.fields = norm
        if eligible is not None:
            eligible = eligible.reindex(index=base.index, columns=base.columns).fillna(False).astype(bool)
        self.eligible = eligible
        self.load_meta = dict(meta or {})

    # -- 轻量访问器
    def __getattr__(self, item: str) -> pd.DataFrame:
        try:
            fields = object.__getattribute__(self, "fields")
        except AttributeError:  # pragma: no cover - 构造期保护
            raise AttributeError(item) from None
        if item in fields:
            return fields[item]
        raise AttributeError(f"Panel 无字段: {item}")

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.dates), len(self.codes))

    def optional(self, name: str) -> pd.DataFrame:
        """缺字段时返回全 NaN 面板（因子容错）。"""
        if name in self.fields:
            return self.fields[name]
        return pd.DataFrame(np.nan, index=self.dates, columns=self.codes)

    def eligible_mask(self) -> pd.DataFrame:
        if self.eligible is not None:
            return self.eligible
        return pd.DataFrame(True, index=self.dates, columns=self.codes)

    def market_daily_return(self) -> pd.Series:
        """等权市场日收益（% → 小数）；有股票池时按池内等权。"""
        ret = self.pct_chg / 100.0
        if self.eligible is not None:
            ret = ret.where(self.eligible)
        return ret.mean(axis=1)

    def subset(self, max_date: str | None = None, min_date: str | None = None) -> "Panel":
        """截断到 [min_date, max_date]（字符串 YYYYMMDD，含端点）；用于 PIT 截断不变性测试。"""
        mask = np.ones(len(self.dates), dtype=bool)
        if max_date is not None:
            mask &= np.asarray(self.dates <= str(max_date))
        if min_date is not None:
            mask &= np.asarray(self.dates >= str(min_date))
        keep = self.dates[mask]
        fields = {k: v.loc[keep] for k, v in self.fields.items()}
        elig = self.eligible.loc[keep] if self.eligible is not None else None
        meta = dict(self.load_meta, subset_to=str(max_date))
        # 指数收益/成交量等 meta 序列也截断到保留日期，保证 PIT 截断不变性
        for key in ("index_returns", "index_volume"):
            if isinstance(meta.get(key), dict):
                meta[key] = {k: v.loc[v.index.intersection(keep)] for k, v in meta[key].items()}
        return Panel(fields, eligible=elig, meta=meta)

    @classmethod
    def empty(cls) -> "Panel":
        idx = pd.Index([], dtype="str")
        cols = pd.Index([], dtype="str")
        names = set(DAILY_FIELDS) | {"adj_close", "roe"} | set(DAILY_BASIC_FIELDS)
        return cls({name: pd.DataFrame(index=idx, columns=cols, dtype=float) for name in names})

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Panel(dates={self.shape[0]}, codes={self.shape[1]}, fields={sorted(self.fields)})"


# ---------------------------------------------------------------- 因子注册表
@dataclass(frozen=True)
class FactorSpec:
    """因子元信息 + 计算函数。direction=+1 表示「因子值越大预期 20 日 CAR 越高」，-1 反之。"""

    name: str
    family: str
    kind: str                    # "event"（0/1 形态事件）| "continuous"（连续值）
    direction: int               # +1 / -1（先验方向，用于 directed IC 与多空方向）
    description: str
    source: str
    requires: tuple[str, ...]    # 依赖的面板字段（loader 据此决定要不要加载财务数据）
    compute: Callable[[Panel], pd.DataFrame] = dc_field(repr=False, compare=False)


FACTORS: dict[str, FactorSpec] = {}


def register(name: str, family: str, kind: str, direction: int, description: str,
             source: str = "", requires: tuple[str, ...] = ()) -> Callable:
    """装饰器：把函数注册为因子。函数签名 fn(panel) -> DataFrame（日期 × 代码）。"""
    if kind not in ("event", "continuous"):
        raise ValueError(f"kind 必须为 event/continuous: {kind}")
    if direction not in (1, -1):
        raise ValueError(f"direction 必须为 +1/-1: {direction}")

    def deco(fn: Callable[[Panel], pd.DataFrame]) -> Callable[[Panel], pd.DataFrame]:
        if name in FACTORS:
            raise ValueError(f"因子重复注册: {name}")
        FACTORS[name] = FactorSpec(name=name, family=family, kind=kind, direction=int(direction),
                                   description=description, source=source,
                                   requires=tuple(requires), compute=fn)
        return fn

    return deco


# ============================ 量学形态（可计算版，9 个） ============================
# 说明：量学书中的形态名 → 简化可计算定义（与 H24/H26/H27/H29 的落地口径对齐/修正，
# 全部只用当日及以前数据；确认型形态见 golden_pillar）。
@register("vol_ratio5", family="量学形态", kind="continuous", direction=1,
          description="量比：当日成交量 / 近5日均量（含当日，与 H24/H29 口径一致）；先验：量增价升",
          source="伏击涨停·量柱七形态")
def _f_vol_ratio5(p: Panel) -> pd.DataFrame:
    ma5 = _rolling(p.vol, 5, min_periods=3).mean()
    return p.vol / ma5.replace(0.0, np.nan)


@register("double_vol", family="量学形态", kind="event", direction=1,
          description="倍量柱：量比5 ≥ 1.9 且当日收阳（pct_chg>0）；先验看涨",
          source="伏击涨停·量柱七形态 / H24 倍量柱")
def _f_double_vol(p: Panel) -> pd.DataFrame:
    vr = _f_vol_ratio5(p)
    mask = (vr >= 1.9) & (p.pct_chg > 0)
    valid = vr.notna() & p.pct_chg.notna()
    return _event(mask, valid)


@register("shrink_vol", family="量学形态", kind="event", direction=1,
          description="缩量柱：当日量 < 前日量 × 0.8（惜售/缩量洗盘）；先验看涨",
          source="伏击涨停·量柱七形态")
def _f_shrink_vol(p: Panel) -> pd.DataFrame:
    prev = p.vol.shift(1)
    mask = p.vol < prev * 0.8
    valid = prev.notna() & p.vol.notna()
    return _event(mask, valid)


@register("up_vol_down", family="量学形态", kind="event", direction=1,
          description="价升量缩：当日收阳且量 < 前日量 × 0.8；先验看涨",
          source="涨停密码·第12章 价升量缩 / H25")
def _f_up_vol_down(p: Panel) -> pd.DataFrame:
    prev = p.vol.shift(1)
    mask = (p.pct_chg > 0) & (p.vol < prev * 0.8)
    valid = prev.notna() & p.pct_chg.notna()
    return _event(mask, valid)


@register("yin_yang", family="量学形态", kind="event", direction=1,
          description="极阴次阳：前日跌幅 ≤ -5%，当日收阳且收盘 ≥ 前日实体中点（严格版采用前日开收中点）；先验看涨",
          source="涨停密码·第15章 极阴次阳 / H26/H29 严格版")
def _f_yin_yang(p: Panel) -> pd.DataFrame:
    prev_pct = p.pct_chg.shift(1)
    prev_open = p.open.shift(1)
    prev_close = p.close.shift(1)
    body_mid = (prev_open + prev_close) / 2.0
    mask = (prev_pct <= -5) & (p.pct_chg > 0) & (p.close >= body_mid)
    valid = prev_pct.notna() & prev_open.notna() & prev_close.notna() & p.pct_chg.notna()
    return _event(mask, valid)


@register("near_high_vol", family="量学形态", kind="event", direction=1,
          description="兵临城下：收盘 ≥ 近20日最高收盘 × 0.98 且量比5 ≥ 1.5；先验看涨",
          source="涨停密码·第4章 兵临城下 / H27")
def _f_near_high_vol(p: Panel) -> pd.DataFrame:
    hi20 = _rolling(p.close, 20, min_periods=10).max()
    vr = _f_vol_ratio5(p)
    mask = (p.close >= hi20 * 0.98) & (vr >= 1.5)
    valid = vr.notna() & hi20.notna()
    return _event(mask, valid)


@register("golden_pillar", family="量学形态", kind="event", direction=1,
          description=("黄金柱（确认日信号）：基柱 T-3 为阳柱且量比5≥1.5；T-2、T-1、T 三日收盘均不破基柱实顶"
                       "（≥基柱收盘×0.99）且三日量均 < 基柱量×0.9 → T 为确认完成日，T+1 入场"),
          source="伏击涨停·第8章 黄金柱 / 判例 H29（确认型形态不得按形态日索引）")
def _f_golden_pillar(p: Panel) -> pd.DataFrame:
    vr = _f_vol_ratio5(p)
    base = (p.close > p.open) & (vr >= 1.5)                   # 基柱（T-3）必须是阳线，不能只用 pct_chg>0
    base3 = base.astype(float).shift(3) == 1.0                # 基柱信号平移到确认日窗口
    base_close = p.close.shift(3)
    base_vol = p.vol.shift(3)
    hold_ok = ((p.close.shift(2) >= base_close * 0.99)
               & (p.close.shift(1) >= base_close * 0.99)
               & (p.close >= base_close * 0.99))
    shrink_ok = ((p.vol.shift(2) < base_vol * 0.9)
                 & (p.vol.shift(1) < base_vol * 0.9)
                 & (p.vol < base_vol * 0.9))
    mask = base3 & hold_ok & shrink_ok
    valid = base_close.notna() & base_vol.notna()
    return _event(mask, valid)


@register("zero_vol", family="量学形态", kind="event", direction=1,
          description="缩量地量：当日量创近20日新低（严格低于前20日最低量）；先验：地量见底",
          source="涨停密码·第16章 牛股量形（地量）")
def _f_zero_vol(p: Panel) -> pd.DataFrame:
    prev_min = _rolling(p.vol.shift(1), 20, min_periods=10).min()
    mask = p.vol <= prev_min
    valid = prev_min.notna() & p.vol.notna()
    return _event(mask, valid)


@register("aokou", family="量学形态", kind="event", direction=1,
          description=("凹口收口（近似）：近10日内曾自20日高点回落≥8%（凹口），当日放量收阳且站上5日均线、"
                       "量≥5日均量（收口）；先验看涨"),
          source="涨停密码·第2章 凹口淘金（近似实现）")
def _f_aokou(p: Panel) -> pd.DataFrame:
    hi20 = _rolling(p.close, 20, min_periods=10).max()
    drawdown = p.close < hi20 * 0.92
    recent_dd = _rolling(drawdown.astype(float), 10, min_periods=5).max() > 0.5
    ma5 = _rolling(p.close, 5, min_periods=3).mean()
    vma5 = _rolling(p.vol, 5, min_periods=3).mean()
    mask = (recent_dd
            & (p.close > p.close.shift(1))
            & (p.close >= ma5)
            & (p.vol >= vma5))
    valid = hi20.notna() & ma5.notna() & vma5.notna() & p.close.shift(1).notna()
    return _event(mask, valid)


# ============================ 通用因子（8 个） ============================
@register("mom20", family="通用", kind="continuous", direction=1,
          description="20日动量：复权收盘价 20 日收益率（先验：动量延续）",
          source="通用动量（selection_engine momentum20 同口径）",
          requires=("adj_close",))
def _f_mom20(p: Panel) -> pd.DataFrame:
    return p.adj_close / p.adj_close.shift(20) - 1.0


@register("rev5", family="通用", kind="continuous", direction=-1,
          description="5日反转：复权收盘价 5 日收益率（原始值；先验：短期反转，取负向）",
          source="通用短期反转（selection_engine reversal5 同口径）",
          requires=("adj_close",))
def _f_rev5(p: Panel) -> pd.DataFrame:
    return p.adj_close / p.adj_close.shift(5) - 1.0


@register("low_turnover", family="通用", kind="continuous", direction=-1,
          description="换手率（原始值；先验：低换手/低拥挤度占优，取负向）",
          source="通用拥挤度（selection_engine low_turnover 同口径）",
          requires=("turnover_rate",))
def _f_low_turnover(p: Panel) -> pd.DataFrame:
    return p.optional("turnover_rate").copy()


@register("pe", family="通用", kind="continuous", direction=-1,
          description="PE(TTM)（原始值，仅保留正 PE；先验：低估值占优，取负向）",
          source="通用估值（selection_engine pe_ttm 同口径）",
          requires=("pe_ttm",))
def _f_pe(p: Panel) -> pd.DataFrame:
    pe = p.optional("pe_ttm")
    return pe.where(pe > 0)


@register("roe", family="通用", kind="continuous", direction=1,
          description="最新一期 ROE（PIT：ann_date ≤ T 的最新公告；原始值未年化，先验：高质量占优）",
          source="通用质量（selection_engine load_latest_fina 同口径）",
          requires=("roe",))
def _f_roe(p: Panel) -> pd.DataFrame:
    return p.optional("roe").copy()


@register("quality_trap", family="通用", kind="event", direction=-1,
          description=("H14 质量陷阱：PE 横截面处于最高 1/3 层、且层内 ROE 排名前 20% 的股票"
                       "（先验：high PE + high ROE = 陷阱，利空）"),
          source="H14 质量陷阱规避（selection_engine v1.1 同定义）",
          requires=("pe_ttm", "roe"))
def _f_quality_trap(p: Panel) -> pd.DataFrame:
    pe = p.optional("pe_ttm")
    roe = p.optional("roe")
    pe_pos = pe.where(pe > 0)
    valid = pe_pos.notna() & roe.notna()
    pe_np, roe_np = _to_np(pe_pos), _to_np(roe)
    valid_np = _to_np(valid, dtype=bool)
    n = valid_np.sum(axis=1).astype(float)
    pe_rank = _rank_2d(np.where(valid_np, pe_np, np.nan))
    with np.errstate(divide="ignore", invalid="ignore"):
        pe_pct = pe_rank / np.where(n > 0, n, np.nan)[:, None]
    high = valid_np & (n[:, None] >= 6) & (pe_pct >= 2.0 / 3.0)
    roe_high = np.where(high, roe_np, np.nan)
    m_high = np.isfinite(roe_high)
    n_high = m_high.sum(axis=1).astype(float)
    roe_rank = _rank_2d(roe_high)
    with np.errstate(divide="ignore", invalid="ignore"):
        roe_pct = roe_rank / np.where(n_high > 0, n_high, np.nan)[:, None]
    trap = high & (n_high[:, None] >= 5) & (roe_pct >= 0.8)
    out = np.where(valid_np, np.where(trap, 1.0, 0.0), np.nan)
    return pd.DataFrame(out, index=pe.index, columns=pe.columns)


@register("vol20", family="通用", kind="continuous", direction=-1,
          description="20日波动率：复权日收益的 20 日标准差（先验：低波动异象，取负向）",
          source="通用低波异象",
          requires=("adj_close",))
def _f_vol20(p: Panel) -> pd.DataFrame:
    daily_ret = p.adj_close.pct_change()
    return _rolling(daily_ret, 20, min_periods=10).std()


@register("max_ret20", family="通用", kind="continuous", direction=-1,
          description="近20日最大单日收益（MAX 彩票因子；先验：高 MAX 未来收益低，取负向）",
          source="通用 MAX/彩票效应",
          requires=("adj_close",))
def _f_max_ret20(p: Panel) -> pd.DataFrame:
    daily_ret = p.adj_close.pct_change()
    return _rolling(daily_ret, 20, min_periods=10).max()


# ---------------------------------------------------------------- 前视收益 / 市场基准
def forward_car(panel: Panel, horizon: int = DEFAULT_HORIZON,
                cost: float = DEFAULT_COST) -> pd.DataFrame:
    """未来 horizon 日相对等权市场的 CAR 矩阵（信号日 T 的行 = T+1 入场、T+1+h 卖出的收益）。

    个股收益：adj_close(T+1+h) / adj_close(T+1) - 1（复权、买入持有）；
    市场收益：等权市场日收益复利累计 c(T+1+h)/c(T+1) - 1（与 H24/H26/H29 同口径，
    注意日再平衡等权与个股买入持有存在「再平衡差」，故全样本无条件 CAR 均值不是 -cost，
    见输出 meta.baseline 与 notes）；
    成本：单边 30bp；CAR = 个股收益 - 市场收益 - cost。
    """
    adj = panel.adj_close
    entry = adj.shift(-1)
    exit_ = adj.shift(-(1 + horizon))
    fwd = exit_ / entry - 1.0
    mkt_daily = panel.market_daily_return()
    cum = (1.0 + mkt_daily.fillna(0.0)).cumprod()
    mkt_fwd = cum.shift(-(1 + horizon)) / cum.shift(-1) - 1.0
    return fwd.sub(mkt_fwd, axis=0) - cost


# ---------------------------------------------------------------- 评测窗口
def _resolve_window(dates: pd.Index, start: str | None, end: str | None,
                    horizon: int) -> dict | None:
    """把请求的 [start, end] 信号日区间落到面板交易日上，并裁剪到可算满 20 日收益的末日。"""
    n = len(dates)
    if n == 0:
        return None
    date_list = [str(d) for d in dates]
    effective_end = n - 1 - (1 + horizon)   # 该日行仍能取到 T+1+h 的复权收盘
    if effective_end < 0:
        return None
    start_i = 0 if start is None else bisect.bisect_left(date_list, str(start))
    if end is None:
        end_i = effective_end
    else:
        end_i = bisect.bisect_right(date_list, str(end)) - 1
        end_i = min(end_i, effective_end)
    if start_i > end_i:
        return None
    return {"start_i": int(start_i), "end_i": int(end_i), "n": int(end_i - start_i + 1),
            "start": date_list[start_i], "end": date_list[end_i],
            "requested_start": None if start is None else str(start),
            "requested_end": None if end is None else str(end),
            "panel_first": date_list[0], "panel_last": date_list[-1]}


# ---------------------------------------------------------------- 单因子评测
def _quantile_stats(f: np.ndarray, car: np.ndarray, elig: np.ndarray, dates: list[str],
                    direction: int, ann_factor: float, kind: str, n_groups: int = 5,
                    cost: float = 0.0) -> dict:
    """分组收益：连续因子按日横截面五分组；0/1 事件因子用「信号组(Q5) vs 非信号组(Q1)」。

    car 已逐票扣单边成本；多空价差还须再扣两腿 2×cost，否则报告的是毛价差。
    """
    pair = elig & np.isfinite(f) & np.isfinite(car)
    if kind == "event":
        sig = pair & (f == 1.0)
        non = pair & (f == 0.0)
        daily_sig = _daily_mean(car, sig, dates)
        daily_non = _daily_mean(car, non, dates)
        n_days = len(dates)
        empty_ret = _return_stats(pd.Series(dtype=float), ann_factor)
        groups = {f"Q{g}": dict(empty_ret) | {"n_obs": 0, "avg_members_per_day": 0.0}
                  for g in range(2, n_groups)}
        groups["Q1"] = dict(_return_stats(daily_non, ann_factor)) | {
            "n_obs": int(non.sum()),
            "avg_members_per_day": _num(float(non.sum()) / n_days, 1) if n_days else 0.0,
        }
        groups[f"Q{n_groups}"] = dict(_return_stats(daily_sig, ann_factor)) | {
            "n_obs": int(sig.sum()),
            "avg_members_per_day": _num(float(sig.sum()) / n_days, 1) if n_days else 0.0,
        }
        groups = dict(sorted(groups.items(), key=lambda kv: int(kv[0][1:])))
        spread = (daily_sig - daily_non).dropna()
        if cost:
            spread = spread - 2.0 * float(cost)
        tb_stat = _return_stats(spread, ann_factor)
        tb_stat["directed_ann_pct"] = _num((tb_stat.get("ann_pct") or 0.0) * direction, 2) \
            if tb_stat.get("ann_pct") is not None else None
        return {"method": "binary_signal_vs_non_signal", "n_groups": int(n_groups),
                "tie_break": None, "groups": groups, "top_minus_bottom": tb_stat,
                "_spread_series": spread, "n_obs": int(pair.sum())}

    groups: dict[str, dict] = {}
    daily: dict[int, pd.Series] = {}
    ranks = _rank_2d(np.where(pair, f, np.nan), average=False)
    n = pair.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.ceil(ranks / np.where(n > 0, n, np.nan)[:, None] * n_groups)
    q = np.clip(q, 1, n_groups)
    for g in range(1, n_groups + 1):
        sel = pair & (q == g)
        daily[g] = _daily_mean(car, sel, dates)
        stat = _return_stats(daily[g], ann_factor)
        stat["n_obs"] = int(sel.sum())
        stat["avg_members_per_day"] = _num(float(sel.sum(axis=1).mean()) if len(dates) else 0.0, 1)
        groups[f"Q{g}"] = stat
    top = daily.get(n_groups, pd.Series(dtype=float))
    bottom = daily.get(1, pd.Series(dtype=float))
    spread = (top - bottom).dropna()
    if cost:
        spread = spread - 2.0 * float(cost)
    tb_stat = _return_stats(spread, ann_factor)
    tb_stat["directed_ann_pct"] = _num((tb_stat.get("ann_pct") or 0.0) * direction, 2) \
        if tb_stat.get("ann_pct") is not None else None
    return {"method": "quantile", "n_groups": int(n_groups), "tie_break": "ts_code_order",
            "groups": groups, "top_minus_bottom": tb_stat,
            "_spread_series": spread, "n_obs": int(pair.sum())}


def _event_split(f: np.ndarray, car: np.ndarray, elig: np.ndarray, dates: list[str],
                 ann_factor: float, cost: float = 0.0) -> dict:
    """事件因子（0/1）：信号组 vs 非信号组的按日 CAR 与净超额。"""
    pair = elig & np.isfinite(f) & np.isfinite(car)
    sig = pair & (f == 1.0)
    non = pair & (f == 0.0)
    daily_sig = _daily_mean(car, sig, dates)
    daily_non = _daily_mean(car, non, dates)
    excess = (daily_sig - daily_non - 2.0 * float(cost)).dropna()
    sig_stat = _return_stats(daily_sig, ann_factor)
    non_stat = _return_stats(daily_non, ann_factor)
    exc_stat = _return_stats(excess, ann_factor)
    n_days = len(dates)
    return {
        "signal": sig_stat,
        "non_signal": non_stat,
        "excess": exc_stat,
        "n_signal_obs": int(sig.sum()),
        "n_signal_days": int((sig.sum(axis=1) > 0).sum()),
        "avg_signals_per_day": _num(float(sig.sum()) / n_days, 1) if n_days else None,
        "signal_share_pct": _num(float(sig.sum()) / max(1, int(pair.sum())) * 100.0, 2),
    }


def _bootstrap(series: pd.Series, n_boot: int, seed: int, label: str,
               block: int = DEFAULT_BLOCK) -> dict:
    """块自助（复用 stat_utils 口径）：返回均值>0 的 p 值。"""
    res = stat_utils.block_bootstrap_p(pd.Series(series, dtype=float).dropna(),
                                       block=block, n_boot=n_boot, seed=seed)
    return {"series": label, "block": int(block),
            "n_boot": int(res.get("n_boot") or 0),
            "mean_pct": _num(res.get("mean_pct"), 4),
            "p_le_0": _num(res.get("p_le_0"), 4)}


def _empty_factor_entry(spec: FactorSpec) -> dict:
    empty_ic = _series_stats(pd.Series(dtype=float))
    empty_ret = _return_stats(pd.Series(dtype=float), 252.0 / DEFAULT_HORIZON)
    groups = {f"Q{g}": dict(empty_ret) for g in range(1, 6)}
    is_event = spec.kind == "event"
    return {
        "name": spec.name, "family": spec.family, "kind": spec.kind,
        "direction": spec.direction, "description": spec.description, "source": spec.source,
        "n_obs": 0, "n_days": 0, "signal": None,
        "ic": dict(empty_ic), "ic_directed": dict(empty_ic),
        "by_year": {},
        "is_oos": {"split_date": None, "source": "none",
                   "IS": dict(empty_ic), "OOS": dict(empty_ic)},
        "bootstrap": {"ic_directed": _bootstrap(pd.Series(dtype=float), 0, DEFAULT_SEED, "directed_daily_ic"),
                      "long_short_spread": _bootstrap(pd.Series(dtype=float), 0, DEFAULT_SEED, "daily_top_minus_bottom")},
        "quintiles": {"method": "binary_signal_vs_non_signal" if is_event else "quantile",
                      "n_groups": 5, "tie_break": None if is_event else "ts_code_order",
                      "groups": groups, "top_minus_bottom": dict(empty_ret), "n_obs": 0},
        "event_split": None,
    }


def _eval_one_factor(spec: FactorSpec, fmat: pd.DataFrame, car_w: np.ndarray, elig_w: np.ndarray,
                     dates_w: list[str], horizon: int, cost: float, seed: int,
                     min_cross_section: int, n_boot: int, split_date: str | None,
                     split_source: str) -> dict:
    f = _to_np(fmat)
    if f.shape != car_w.shape:
        raise ValueError(f"因子 {spec.name} 输出形状 {f.shape} 与面板 {car_w.shape} 不一致")
    ann_factor = 252.0 / float(horizon)
    pair = elig_w & np.isfinite(f) & np.isfinite(car_w)

    ic = _spearman_ic(np.where(elig_w, f, np.nan), car_w, min_cross_section)
    ic_series = pd.Series(ic, index=pd.Index(dates_w))
    directed = ic_series * spec.direction
    raw_stats = _series_stats(ic_series, hac_lags=horizon)
    dir_stats = _series_stats(directed, hac_lags=horizon)

    years = sorted({d[:4] for d in dates_w})
    by_year = {}
    for year in years:
        sub = directed[[d[:4] == year for d in dates_w]]
        by_year[year] = _series_stats(sub, hac_lags=horizon)

    if split_date is None:
        is_mask = np.zeros(len(dates_w), dtype=bool)
    else:
        is_mask = np.asarray([d <= split_date for d in dates_w])
    is_oos = {"split_date": split_date, "source": split_source,
              "IS": _series_stats(directed.iloc[is_mask], hac_lags=horizon),
              "OOS": _series_stats(directed.iloc[~is_mask], hac_lags=horizon)}

    quant = _quantile_stats(f, car_w, elig_w, dates_w, spec.direction, ann_factor,
                            spec.kind, cost=cost)
    spread_series = quant.pop("_spread_series")

    signal = None
    event_split = None
    if spec.kind == "event":
        ev = _event_split(f, car_w, elig_w, dates_w, ann_factor, cost=cost)
        event_split = ev
        signal = {"n_events": ev["n_signal_obs"], "n_signal_days": ev["n_signal_days"],
                  "avg_signals_per_day": ev["avg_signals_per_day"],
                  "signal_share_pct": ev["signal_share_pct"]}

    return {
        "name": spec.name, "family": spec.family, "kind": spec.kind,
        "direction": spec.direction, "description": spec.description, "source": spec.source,
        "n_obs": int(pair.sum()), "n_days": int(ic_series.notna().sum()),
        "signal": signal,
        "ic": raw_stats,
        "ic_directed": dir_stats,
        "by_year": by_year,
        "is_oos": is_oos,
        "bootstrap": {
            "ic_directed": _bootstrap(directed, n_boot, seed, "directed_daily_ic",
                                      block=max(int(horizon), DEFAULT_BLOCK)),
            "long_short_spread": _bootstrap(spread_series, n_boot, seed, "daily_top_minus_bottom",
                                            block=max(int(horizon), DEFAULT_BLOCK)),
        },
        "quintiles": quant,
        "event_split": event_split,
    }


def _json_safe_meta(meta: dict) -> dict:
    """把 Panel.load_meta 转成可 JSON 序列化的摘要（不把 Series/大 dict 原样写进报告）。"""
    out: dict = {}
    for key, value in meta.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, pd.Series):
            head = []
            for idx, item in value.head(5).items():
                head.append([str(idx), None if pd.isna(item) else str(item)])
            out[key] = {"len": int(len(value)), "nunique": int(value.nunique()), "head": head}
        elif isinstance(value, dict):
            out[key] = {"n": len(value), "keys": sorted(str(k) for k in value.keys())[:10]}
        elif isinstance(value, (list, tuple)):
            out[key] = [str(x) for x in list(value)[:10]]
        else:
            out[key] = str(value)
    return out


def evaluate_panel(panel: Panel, factor_names: list[str] | str | None = None,
                   horizon: int = DEFAULT_HORIZON, cost: float = DEFAULT_COST,
                   start: str | None = None, end: str | None = None,
                   is_end: str | None = None, seed: int = DEFAULT_SEED,
                   min_cross_section: int = DEFAULT_MIN_CROSS_SECTION,
                   n_boot: int = 300, universe: str = "",
                   factor_specs: dict[str, "FactorSpec"] | None = None,
                   neutralization: dict | None = None) -> dict:
    """在给定面板上评测因子（纯计算，不做数据 IO，便于单测与复现）。

    factor_names: 因子名列表 / 逗号分隔字符串 / None（全部）。
    is_end: IS/OOS 切分日（含 IS）；None 时自动取信号窗口交易日中点，并在 meta 标注 source。
    factor_specs: 可选的临时因子定义（如中性化残差）；同名时优先于 FACTORS。
    neutralization: 可选的中性化元数据，原样写入报告 meta。
    """
    t0 = time.time()
    if factor_names is None:
        names = list(FACTORS)
    elif isinstance(factor_names, str):
        names = [x.strip() for x in factor_names.split(",") if x.strip()]
    else:
        names = [str(x) for x in factor_names]
    extra_specs = factor_specs or {}
    unknown = [x for x in names if x not in FACTORS and x not in extra_specs]
    if unknown:
        raise KeyError(f"未注册因子: {unknown}；可用: {list(FACTORS)}")
    if not names:
        raise ValueError("factors 为空")

    requested = {"start": None if start is None else str(start),
                 "end": None if end is None else str(end),
                 "factors": names, "horizon": int(horizon), "cost": float(cost),
                 "universe": universe or panel.load_meta.get("universe", "unknown"),
                 "is_end": None if is_end is None else str(is_end),
                 "seed": int(seed), "min_cross_section": int(min_cross_section),
                 "n_boot": int(n_boot)}
    meta = {
        "lib": "factor_lib", "version": LIB_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
        "requested": requested,
        "horizon": int(horizon), "cost": float(cost),
        "entry": "T+1 收盘入场", "exit": f"T+{1 + int(horizon)} 收盘卖出",
        "market": "equal_weight_daily_rebalanced（等权市场日收益复利，与 H24/H26/H29 同口径）",
        "seed": int(seed), "min_cross_section": int(min_cross_section), "block": DEFAULT_BLOCK,
        "data": _json_safe_meta(panel.load_meta),
        "notes": [
            "PIT：因子只用 T 日及以前数据；golden_pillar 等确认型形态信号索引=确认完成日，T+1 入场。",
            "CAR = 个股 T+1→T+1+h 复权买入持有收益 - 等权市场同期复利收益 - 30bp。",
            "等权市场为日频再平衡，与个股买入持有存在再平衡差，故全样本无条件 CAR 均值不等于 -cost，见 baseline。",
            "连续因子按日横截面五分组（同值时按 ts_code 顺序破并列）；0/1 事件因子二值分组：Q5=信号组、"
            "Q1=非信号组、Q2~Q4 无定义（事件组明细另见 event_split）。",
            "年化 = h 日平均 CAR × 252/h（重叠窗口近似，仅用于量级比较；显著性看 t 与 block bootstrap）。",
            "可复现：bootstrap 固定 seed；除 generated_at / runtime_sec / data.load_sec 等耗时字段外，"
            "同数据同参数两次运行因子数值完全一致（已验证）。",
        ],
        "runtime_sec": None,
    }
    if neutralization is not None:
        meta["neutralization"] = neutralization
    window = _resolve_window(panel.dates, start, end, horizon)
    empty_report = {"meta": meta, "factors": {}}
    if window is None or panel.shape[0] == 0:
        meta["effective"] = {"start": None, "end": None, "signal_days": 0,
                             "panel_dates": int(panel.shape[0]), "codes": int(panel.shape[1]),
                             "reason": "面板为空或区间内无法计算满持有期收益"}
        meta["is_end"] = None
        meta["is_end_source"] = "none"
        meta["baseline"] = {"n_obs": 0, "mean_pct": None, "median_pct": None}
        meta["runtime_sec"] = round(time.time() - t0, 3)
        for name in names:
            empty_report["factors"][name] = _empty_factor_entry(extra_specs.get(name) or FACTORS[name])
        return empty_report

    car = forward_car(panel, horizon=horizon, cost=cost)
    car_np = _to_np(car)
    elig_np = _to_np(panel.eligible_mask(), dtype=bool)
    pos = np.arange(window["start_i"], window["end_i"] + 1)
    car_w = car_np[pos]
    elig_w = elig_np[pos]
    dates_w = [str(d) for d in panel.dates[pos]]

    # IS/OOS 切分：未显式给定时取信号窗口中点（并在 meta 标注），保证任意区间都可做样本内外对照
    if is_end is not None:
        split_date, split_source = str(is_end), "arg"
    else:
        split_date, split_source = dates_w[len(dates_w) // 2], "auto_midpoint"
    meta["is_end"] = split_date
    meta["is_end_source"] = split_source
    meta["effective"] = {
        "start": window["start"], "end": window["end"], "signal_days": int(window["n"]),
        "panel_dates": int(panel.shape[0]), "codes": int(panel.shape[1]),
        "panel_first": window["panel_first"], "panel_last": window["panel_last"],
        "requested_start": window["requested_start"], "requested_end": window["requested_end"],
    }
    base_pairs = elig_w & np.isfinite(car_w)
    base_vals = car_w[base_pairs]
    meta["baseline"] = {
        "n_obs": int(base_pairs.sum()),
        "mean_pct": _num(float(np.mean(base_vals)) * 100.0, 3) if base_vals.size else None,
        "median_pct": _num(float(np.median(base_vals)) * 100.0, 3) if base_vals.size else None,
        "note": "无条件下（全部合格股票等权）20日 CAR 均值；含 30bp 成本与等权市场再平衡差",
    }

    for name in names:
        spec = extra_specs.get(name) or FACTORS[name]
        try:
            fmat = spec.compute(panel)
        except Exception as exc:  # 单因子失败不影响其他因子，但显式报错（研究可复现优先）
            raise RuntimeError(f"因子 {name} 计算失败: {exc}") from exc
        if not isinstance(fmat, pd.DataFrame):
            raise TypeError(f"因子 {name} 必须返回 DataFrame，实际 {type(fmat)}")
        meta["factors_evaluated"] = meta.get("factors_evaluated", 0) + 1
        empty_report["factors"][name] = _eval_one_factor(
            spec, fmat.iloc[pos], car_w, elig_w, dates_w, horizon, cost, seed,
            min_cross_section, n_boot, split_date, split_source)
    meta["factors_evaluated"] = len(names)
    meta["runtime_sec"] = round(time.time() - t0, 3)
    return empty_report


# ---------------------------------------------------------------- 数据加载（真实数据）
def _read_frames(root: Path, dates: list[str], subdir: str, columns: list[str]) -> list[pd.DataFrame]:
    frames = []
    folder = root / subdir
    for dt in dates:
        path = folder / f"d_{dt}.parquet"
        if not path.exists():
            continue
        df = pq.read_table(str(path), columns=columns).to_pandas()
        df["trade_date"] = dt
        frames.append(df)
    return frames


def _load_stock_basic(root: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(root / "stock_basic" / "*.parquet")))
    if not files:
        return pd.DataFrame(columns=["ts_code", "name", "industry", "list_date"])
    parts = [pq.read_table(f, columns=["ts_code", "name", "industry", "list_date"]).to_pandas()
             for f in files]
    sb = pd.concat(parts, ignore_index=True)
    sb["ts_code"] = sb["ts_code"].astype(str)
    return sb.drop_duplicates("ts_code")


def _load_index_meta(root: Path, load_dates: list[str]) -> dict:
    """指数日收益/成交量（000300.SH/000001.SH），供 beta/alpha/rsrs/量能因子使用。

    返回 Series（index=trade_date）而不是 DataFrame，避免在 date×code 面板里广播
    两个 5000 列的大矩阵（约 80MB）。
    """
    empty = {"index_returns": {}, "index_volume": {}}
    frames = _read_frames(root, load_dates, "index_daily", ["ts_code", "close", "vol"])
    if not frames:
        return empty
    long = pd.concat(frames, ignore_index=True)
    long["ts_code"] = long["ts_code"].astype(str)
    long = long[long["ts_code"].isin(("000300.SH", "000001.SH"))]
    if long.empty:
        return empty
    long = long.drop_duplicates(["trade_date", "ts_code"])
    close = long.pivot(index="trade_date", columns="ts_code", values="close")
    vol = long.pivot(index="trade_date", columns="ts_code", values="vol")
    dates = pd.Index(load_dates)
    returns: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    for code in close.columns:
        c = pd.to_numeric(close[code], errors="coerce").reindex(dates)
        returns[str(code)] = c / c.shift(1) - 1.0
        v = pd.to_numeric(vol[code], errors="coerce").reindex(dates)
        volumes[str(code)] = v
    return {"index_returns": returns, "index_volume": volumes}


def _load_fina_roe(root: Path, panel_dates: pd.Index, codes: pd.Index) -> pd.DataFrame:
    """PIT ROE 矩阵：取 ann_date ≤ 该日的最新一期 **年化** ROE（fina_indicator.roe_yearly）。

    原始 `roe` 是报告期累计值；披露季内 Q1/H1/Q3/FY 混排会让截面排名反映披露时点。
    优先 roe_yearly，缺失时才回退到 roe；同报告日多期取 end_date 最大。
    """
    files = sorted(glob.glob(str(root / "fina_indicator" / "*.parquet")))
    if not files:
        return pd.DataFrame(np.nan, index=panel_dates, columns=codes)
    roe_col = "roe"
    rows = []
    for f in files:
        names = set(pq.read_schema(f).names)
        cols = ["ts_code", "ann_date", "end_date",
                "roe_yearly" if "roe_yearly" in names else "roe"]
        rows.append(pq.read_table(f, columns=cols).to_pandas())
        if "roe_yearly" in names:
            roe_col = "roe_yearly"
    fin = pd.concat(rows, ignore_index=True)
    fin["ts_code"] = fin["ts_code"].astype(str)
    fin["ann_date"] = fin["ann_date"].astype(str)
    fin["end_date"] = fin["end_date"].astype(str)
    fin = fin.rename(columns={roe_col: "roe"})
    # 某文件缺 roe_yearly 时，该文件的行在 concat 后由 roe_yearly 列承载；统一回退。
    if "roe_yearly" in fin.columns and "roe" not in fin.columns:
        fin = fin.rename(columns={"roe_yearly": "roe"})
    elif "roe_yearly" in fin.columns:
        fin["roe"] = fin["roe"].where(fin["roe"].notna(), fin["roe_yearly"])
    fin = fin[(fin["ann_date"].str.len() == 8) & fin["roe"].notna()].copy()
    fin = fin[fin["ts_code"].isin(set(codes))]
    if fin.empty:
        return pd.DataFrame(np.nan, index=panel_dates, columns=codes)
    fin = fin.sort_values(["ts_code", "ann_date", "end_date"])
    fin = fin.drop_duplicates(["ts_code", "ann_date"], keep="last")
    fin["_ann_int"] = fin["ann_date"].astype("int64")
    left = pd.MultiIndex.from_product([list(panel_dates), list(codes)],
                                      names=["trade_date", "ts_code"]).to_frame(index=False)
    left["_date_int"] = left["trade_date"].astype(str).astype("int64")
    left["ts_code"] = left["ts_code"].astype(object)
    left = left.sort_values("_date_int")
    right = fin[["ts_code", "_ann_int", "roe"]].copy()
    right["ts_code"] = right["ts_code"].astype(object)
    right = right.sort_values(["_ann_int", "ts_code"])
    merged = pd.merge_asof(left, right, left_on="_date_int", right_on="_ann_int",
                           by="ts_code", direction="backward")
    roe = merged.pivot(index="trade_date", columns="ts_code", values="roe")
    return roe.reindex(index=panel_dates, columns=codes)


def load_panel(start: str, end: str, horizon: int = DEFAULT_HORIZON, universe: str = "tradable",
               data_root: str | Path | None = None, warmup: int = WARMUP_DAYS,
               load_roe: bool = True) -> Panel:
    """载入 [start, end] 信号区间所需面板（自动多载 warmup 个历史交易日 + 持有期尾部）。"""
    t0 = time.time()
    root = Path(data_root) if data_root else DATA
    files = sorted(glob.glob(str(root / "daily" / "d_*.parquet")))
    if not files:
        raise FileNotFoundError(f"行情数据不存在: {root / 'daily'}")
    all_dates = [Path(f).stem[2:] for f in files]
    all_dates.sort()
    start_s, end_s = str(start), str(end)
    i0 = bisect.bisect_left(all_dates, start_s)
    i1 = bisect.bisect_right(all_dates, end_s) - 1
    if i0 >= len(all_dates) or i1 < 0 or i0 > i1:
        raise ValueError(f"区间 {start_s}~{end_s} 在行情文件中无数据（文件范围 "
                         f"{all_dates[0]}~{all_dates[-1]}）")
    lo = max(0, i0 - int(warmup))
    hi = min(len(all_dates) - 1, i1 + int(horizon) + 2)
    load_dates = all_dates[lo:hi + 1]

    # 行情
    frames = _read_frames(root, load_dates, "daily",
                          ["ts_code", "open", "high", "low", "close", "pre_close",
                           "pct_chg", "vol", "amount"])
    if not frames:
        raise FileNotFoundError(f"区间内 daily 文件为空: {load_dates[0]}~{load_dates[-1]}")
    long = pd.concat(frames, ignore_index=True)
    long["ts_code"] = long["ts_code"].astype(str)
    long = long[long["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$", na=False)]
    long = long.drop_duplicates(["trade_date", "ts_code"])
    if long.empty:
        raise ValueError("过滤后行情为空（ts_code 正则 ^\\d{6}\\.(SH|SZ|BJ)$）")
    fields: dict[str, pd.DataFrame] = {}
    for col in DAILY_FIELDS:
        fields[col] = long.pivot(index="trade_date", columns="ts_code", values=col)
    del long

    # 复权因子（后复权口径：adj_close = close × adj_factor）
    adj_frames = _read_frames(root, load_dates, "adj_factor", ["ts_code", "adj_factor"])
    if not adj_frames:
        raise FileNotFoundError(f"复权因子数据不存在: {root / 'adj_factor'}")
    adj_long = pd.concat(adj_frames, ignore_index=True)
    adj_long["ts_code"] = adj_long["ts_code"].astype(str)
    adj_long = adj_long.drop_duplicates(["trade_date", "ts_code"])
    adj = adj_long.pivot(index="trade_date", columns="ts_code", values="adj_factor")
    codes = fields["close"].columns.union(adj.columns).sort_values()
    dates_idx = fields["close"].index
    fields = {k: v.reindex(index=dates_idx, columns=codes) for k, v in fields.items()}
    adj = adj.reindex(index=dates_idx, columns=codes)
    fields["adj_close"] = fields["close"] * adj

    # 估值/换手/股本/市值（daily_basic；缺失日期留 NaN，不影响其他因子）
    db_cols = ["ts_code", *DAILY_BASIC_FIELDS]
    db_frames = _read_frames(root, load_dates, "daily_basic", db_cols)
    if db_frames:
        db_long = pd.concat(db_frames, ignore_index=True)
        db_long["ts_code"] = db_long["ts_code"].astype(str)
        db_long = db_long.drop_duplicates(["trade_date", "ts_code"])
        for col in DAILY_BASIC_FIELDS:
            if col in db_long.columns:
                fields[col] = (db_long.pivot(index="trade_date", columns="ts_code", values=col)
                               .reindex(index=dates_idx, columns=codes))
            else:
                fields[col] = pd.DataFrame(np.nan, index=dates_idx, columns=codes)
    else:
        for col in DAILY_BASIC_FIELDS:
            fields[col] = pd.DataFrame(np.nan, index=dates_idx, columns=codes)

    # 财务 ROE（PIT；只有 roe / quality_trap 需要时才加载，5586 个小文件约 10~15s）
    if load_roe:
        fields["roe"] = _load_fina_roe(root, dates_idx, codes)
    else:
        fields["roe"] = pd.DataFrame(np.nan, index=dates_idx, columns=codes)

    # 可交易股票池（只用 T 日可知信息 + 静态上市日期）
    sb = _load_stock_basic(root)
    list_date = None
    list_date_missing = 0
    if not sb.empty:
        list_date = pd.to_numeric(sb.set_index("ts_code")["list_date"].reindex(codes), errors="coerce")
        list_date_missing = int(list_date.isna().sum())
        # stock_basic 只含在市股票；退市股在 daily 中仍可能有历史行情。
        # 缺失 list_date 视为“上市已久”，避免把退市股整段历史剔出样本（幸存者偏差）。
        list_date = list_date.fillna(0)
    if universe == "all":
        eligible = None
    elif universe == "tradable":
        price_ok = fields["close"] >= MIN_PRICE
        liq_ok = fields["amount"] * AMOUNT_UNIT >= MIN_AMOUNT_YUAN
        if list_date is not None and list_date.notna().any():
            thr = (pd.to_datetime(pd.Index(dates_idx), format="%Y%m%d")
                   - pd.Timedelta(days=MIN_LIST_DAYS)).strftime("%Y%m%d").astype("int64")
            ld_np = list_date.to_numpy(dtype=float)
            with np.errstate(invalid="ignore"):
                listing_ok = pd.DataFrame(np.less_equal(ld_np[None, :], thr.to_numpy()[:, None]),
                                          index=dates_idx, columns=codes)
        else:
            listing_ok = True
        eligible = (price_ok & liq_ok & listing_ok).fillna(False)
    else:
        raise ValueError(f"未知 universe: {universe}（可选 tradable/all）")

    index_meta = _load_index_meta(root, load_dates)
    industry = None
    if not sb.empty and "industry" in sb.columns:
        industry = sb.set_index("ts_code")["industry"].reindex(codes)

    meta = {
        "data_root": str(root), "universe": universe,
        "load_dates": [load_dates[0], load_dates[-1]], "load_days": len(load_dates),
        "warmup_days": int(warmup), "horizon": int(horizon),
        "panel_dates": int(len(dates_idx)), "codes": int(len(codes)),
        "list_date_missing_codes": int(list_date_missing),
        "eligible_avg_per_day": _num(float(eligible.sum(axis=1).mean()), 1) if eligible is not None else None,
        "eligible_share": _num(float(eligible.to_numpy().mean()), 4) if eligible is not None else None,
        "load_sec": round(time.time() - t0, 2),
        **index_meta,
        "industry": industry,
    }
    return Panel(fields, eligible=eligible, meta=meta)


def run_eval(start: str, end: str, factor_names: list[str] | str | None = None,
             horizon: int = DEFAULT_HORIZON, cost: float = DEFAULT_COST,
             is_end: str | None = None, universe: str = "tradable",
             data_root: str | Path | None = None, seed: int = DEFAULT_SEED,
             min_cross_section: int = DEFAULT_MIN_CROSS_SECTION, n_boot: int = 300,
             neutralize: str = "") -> dict:
    """加载真实数据并评测（CLI eval 的实现；返回可直接落盘的 JSON dict）。

    neutralize: ""/"none" 不中性化；"industry,size" 对因子做逐日行业+市值残差化。
    """
    if factor_names is None:
        names = list(FACTORS)
    elif isinstance(factor_names, str):
        names = [x.strip() for x in factor_names.split(",") if x.strip()]
    else:
        names = [str(x) for x in factor_names]
    unknown = [x for x in names if x not in FACTORS]
    if unknown:
        raise KeyError(f"未注册因子: {unknown}；可用: {list(FACTORS)}")
    need_roe = any("roe" in FACTORS[name].requires for name in names)
    panel = load_panel(start, end, horizon=horizon, universe=universe,
                       data_root=data_root, load_roe=need_roe)
    factor_specs = None
    neutralization = None
    if neutralize and neutralize.strip().lower() not in ("", "none", "off"):
        from factor_neutralize import build_neutralized_specs, parse_modes
        by_industry, by_size = parse_modes(neutralize)
        industry_map = None
        if by_industry:
            # 通用 CLI 原先默默使用 stock_basic 当前行业快照（前视/幸存偏差）；
            # 改为默认加载 PIT L2（含快照 fallback），失败则 fail-visible。
            try:
                from pit_industry import load_sw_l2_industry_with_snapshot_fallback
                industry_map = load_sw_l2_industry_with_snapshot_fallback()
            except Exception as exc:  # noqa: BLE001 - 明确暴露数据/口径问题
                raise RuntimeError(
                    "行业中性化需要 PIT 行业映射（sw_l2 + snapshot fallback），加载失败: "
                    f"{type(exc).__name__}: {exc}") from exc
        factor_specs, neutralization = build_neutralized_specs(
            panel, names, industry=by_industry, size=by_size, min_obs=min_cross_section,
            industry_map=industry_map)
    return evaluate_panel(panel, factor_names=names, horizon=horizon, cost=cost,
                          start=start, end=end, is_end=is_end, seed=seed,
                          min_cross_section=min_cross_section, n_boot=n_boot,
                          universe=universe, factor_specs=factor_specs,
                          neutralization=neutralization)


# ---------------------------------------------------------------- CLI
def factor_rows() -> list[dict]:
    """因子清单（list 子命令输出）。"""
    rows = []
    for spec in FACTORS.values():
        rows.append({
            "name": spec.name,
            "family": spec.family,
            "kind": spec.kind,
            "direction": spec.direction,
            "description": spec.description,
            "source": spec.source,
            "requires": list(spec.requires),
        })
    return rows


def _print_factor_list() -> None:
    print(f"# 因子库 factor_lib v{LIB_VERSION} · 共 {len(FACTORS)} 个因子")
    print(f"# {'name':<15}{'family':<8}{'kind':<11}{'dir':>4}  description")
    for spec in FACTORS.values():
        print(f"  {spec.name:<15}{spec.family:<8}{spec.kind:<11}{spec.direction:>4}  {spec.description}")
    print("# 用法: python tools/research/factor_lib.py eval --start 20260101 --end 20261030 "
          "--factors mom20,double_vol,golden_pillar")


def _print_summary(report: dict) -> None:
    meta = report.get("meta", {})
    eff = meta.get("effective", {})
    base = meta.get("baseline", {})
    print(f"# 因子库 factor_lib v{meta.get('version')} · {len(report.get('factors', {}))} 因子")
    print(f"# 信号窗 {eff.get('start')}~{eff.get('end')}（{eff.get('signal_days')} 个交易日）"
          f" | 面板 {eff.get('panel_dates')} 日 × {eff.get('codes')} 只"
          f" | 持有 {meta.get('horizon')} 日（T+1 收盘入场，T+{1 + int(meta.get('horizon') or 0)} 卖出）")
    print(f"# 成本 {float(meta.get('cost') or 0) * 10000:.0f}bp | 基准 {meta.get('market')}")
    print(f"# IS/OOS 切分 {meta.get('is_end')}（{meta.get('is_end_source')}）"
          f" | 池={meta.get('requested', {}).get('universe')}"
          f" | 无条件 CAR 基线 {base.get('mean_pct')}%（n={base.get('n_obs')}）")
    header = (f"# {'因子':<14}{'族':<6}{'类型':<10}{'先验':>3}{'有效日':>6}{'事件数':>8}"
              f"{'IC':>9}{'IC_t':>7}{'多空年化':>10}{'p_le_0':>8}")
    print(header)
    for name, entry in report.get("factors", {}).items():
        ic = entry.get("ic", {})
        dir_ic = entry.get("ic_directed", {})
        tb = entry.get("quintiles", {}).get("top_minus_bottom", {})
        boot = entry.get("bootstrap", {}).get("ic_directed", {})
        n_events = (entry.get("signal") or {}).get("n_events")
        n_events_s = "-" if n_events is None else str(n_events)
        ic_m = ic.get("mean")
        t_d = dir_ic.get("t")
        ann = tb.get("directed_ann_pct")
        p = boot.get("p_le_0")
        print(f"  {name:<14}{entry.get('family'):<6}{entry.get('kind'):<10}{entry.get('direction'):>3}"
              f"{entry.get('n_days'):>6}{n_events_s:>8}"
              f"{('%.4f' % ic_m) if ic_m is not None else '-':>9}"
              f"{('%+.2f' % t_d) if t_d is not None else '-':>7}"
              f"{('%+.2f%%' % ann) if ann is not None else '-':>10}"
              f"{('%.3f' % p) if p is not None else '-':>8}")


def cmd_list(as_json: bool) -> int:
    if as_json:
        print(json.dumps(factor_rows(), ensure_ascii=False, indent=1))
    else:
        _print_factor_list()
    return 0


def cmd_eval(args) -> int:
    cost = float(args.cost_bp) / 10000.0
    factors = None if args.factors in ("", "all", "*") else args.factors
    try:
        report = run_eval(start=args.start, end=args.end, factor_names=factors,
                          horizon=args.horizon, cost=cost,
                          is_end=(args.is_end or None), universe=args.universe,
                          data_root=(args.data_root or None), seed=args.seed,
                          min_cross_section=args.min_cross_section, n_boot=args.n_boot,
                          neutralize=args.neutralize)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"[factor_lib] eval 失败: {exc}", file=sys.stderr)
        return 2
    out_path = Path(args.out) if args.out else (OUT / f"factor_lib_{args.start}_{args.end}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_summary(report)
    print(f"# 输出: {out_path}")
    return 0


def cmd_map(args) -> int:
    """Tushare factor_list 272 因子的映射/批次/实现状态。"""
    try:
        from factor_lib_tushare import TUSHARE_FACTOR_MAP, factor_map_summary
    except ImportError as exc:  # pragma: no cover
        print(f"[factor_lib] map 失败: {exc}", file=sys.stderr)
        return 2
    summary = factor_map_summary()
    rows = list(TUSHARE_FACTOR_MAP.values())
    if args.batch:
        rows = [r for r in rows if r["batch"] == args.batch]
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    if args.json:
        print(json.dumps({"summary": summary, "factors": rows}, ensure_ascii=False, indent=1))
        return 0
    print(f"# Tushare factor_list 映射：{summary['total']} 因子 | "
          f"已实现 {summary['implemented']} | 批次 {summary['by_batch']}")
    print(f"# 类型分布: {summary['by_type']}")
    print(f"# {'name':<26}{'type':<11}{'batch':<22}{'status':<12}requires")
    for row in rows:
        print(f"  {row['name']:<26}{row['factor_type']:<11}{row['batch']:<22}"
              f"{row['status']:<12}{','.join(row['requires'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="factor_lib.py",
        description="因子库 v1.1：因子注册表 + PIT 特征 + 20 日 CAR IC/分组评测（研究用途）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出已注册因子")
    p_list.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_map = sub.add_parser("map", help="Tushare factor_list 272 因子映射/批次状态")
    p_map.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_map.add_argument("--batch", default="", help="只看某批次（B1_price_volume / B2_alpha101_expression / B3_financial_pit）")
    p_map.add_argument("--status", default="", choices=["", "implemented", "mapped", "blocked_missing_data"], help="按实现状态过滤")

    p_eval = sub.add_parser("eval", help="评测因子（真实数据）")
    p_eval.add_argument("--start", required=True, help="信号起始日 YYYYMMDD（含）")
    p_eval.add_argument("--end", required=True, help="信号结束日 YYYYMMDD（含，实际受行情末端裁剪）")
    p_eval.add_argument("--factors", default="all", help="逗号分隔因子名，或 all（默认全部）")
    p_eval.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="持有期（交易日）")
    p_eval.add_argument("--cost-bp", type=float, default=DEFAULT_COST * 10000.0, help="单边成本（bp）")
    p_eval.add_argument("--is-end", default="", help="IS/OOS 切分日 YYYYMMDD（含 IS）；默认取窗口中点")
    p_eval.add_argument("--universe", choices=["tradable", "all"], default="tradable", help="股票池")
    p_eval.add_argument("--out", default="", help="输出 JSON 路径（默认 research/reports/factor_lib_<start>_<end>.json）")
    p_eval.add_argument("--data-root", default="", help="tushare 数据根（默认 quant_backtest/data/tushare）")
    p_eval.add_argument("--min-cross-section", type=int, default=DEFAULT_MIN_CROSS_SECTION,
                        help="单日横截面最少有效股票数")
    p_eval.add_argument("--n-boot", type=int, default=300, help="block bootstrap 次数")
    p_eval.add_argument("--seed", type=int, default=DEFAULT_SEED, help="bootstrap 随机种子")
    p_eval.add_argument("--neutralize", default="", help="中性化：industry,size / industry / size / none")
    p_eval.add_argument("--json", action="store_true", help="stdout 打印完整 JSON")

    args = ap.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.json)
    if args.cmd == "map":
        return cmd_map(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    ap.print_help()
    return 1


# ---------------------------------------------------------------- 外部批次因子
# Tushare factor_list B1 批次在 factor_lib 全部定义完成后注册（避免循环导入）。
try:  # pragma: no cover - 导入失败时退化为内置 17 因子
    from factor_lib_tushare import register_batch as _register_tushare_batch
except ImportError:  # pragma: no cover
    _register_tushare_batch = None
if _register_tushare_batch is not None:
    _register_tushare_batch(register, FACTORS)


if __name__ == "__main__":
    sys.exit(main())
