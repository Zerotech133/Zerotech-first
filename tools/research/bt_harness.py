# -*- coding: utf-8 -*-
"""统一回测口径 bt_harness v1（2026-11-01 · 支线 C）。

把散落在 selection_engine / portfolio_build / shadow_ledger 里的复权、T+1、
涨跌停、停牌、成本、调仓口径收口为一个可被因子库/策略库批量调用的回测工具。

核心口径（完整说明见 docs/plans/2026-11-01_统一回测口径_v1.md）：
  * 信号：factor_df[date, ts_code, score]，date 收盘后可知（PIT）；
  * T+1：信号日 T 的下一交易日 T+1 **收盘**建仓，T 当日收益绝不计入组合；
  * 持有：至下一个调仓日 T'+1 收盘，等权 top_n；周频=每周最后一个交易日信号；
  * 复权：复权价 = close × adj_factor（同一口径算收益，消除除权除息跳空）；
  * 涨停不可买：pct_chg ≥ 阈值（主板 9.5 / 创业板·科创板 19.5 / 北交所 29.5 / ST 4.5）
    或 limit_list_d 标记 limit=U；跌停不可卖（对称）；停牌（缺价）不可买卖；
  * 不可卖（跌停/停牌）持仓保留原估值继续持有，**占用 top_n 名额**（组合持仓数恒 ≤ top_n），
    并逐条记录 errors；
  * 成本：换手 × cost_bp（bp）；换手 = Σ|w_target − w_drift|（双边口径，0~2，全换仓=2.0；
    与既有 shadow_ledger.py 的 COST=0.003×换手 一致，cost_bp 为单边成本，全换仓收 2×cost_bp 往返）；
  * IC：默认仅当因子近似全市场（单日截面中位数 ≥ min_ic_cross_section=100）才计算，
    日度截面 Spearman/Pearson（score_t vs 复权收益 ret_{t+1}），否则 None；
  * 基准：同窗口「等权全市场（复权价、日频等权再平衡）」，输出 benchmark 与 excess_return。

Python API：
    from bt_harness import run_backtest, load_market, compute_named_factor
    market = load_market("20260101", "20261030", lookback_days=25)
    factor, desc = compute_named_factor("mom20", market)
    rep = run_backtest(factor, "20260101", "20261030", top_n=50, cost_bp=30, rebalance="weekly",
                       market=market, label="mom20")

CLI：
    python tools/research/bt_harness.py run --factor mom20 --start 20260101 --end 20261030 \
        --top 50 --cost-bp 30 --rebalance weekly
  默认输出 research/reports/bt_harness_demo_20261101.json

本文件只做研究输出，不构成投资建议；实盘前须过四关+影子盘+人工批准。
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import inspect
import json
import re
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]  # tools/research/bt_harness.py -> 仓库根
DATA = REPO / "quant_backtest" / "data" / "tushare"
REPORTS = REPO / "research" / "reports"
DEFAULT_DEMO_OUT = REPORTS / "bt_harness_demo_20261101.json"

SCHEMA_VERSION = "bt_harness.v1"
TRADING_DAYS_PER_YEAR = 252
REBALANCE_MODES = ("weekly", "daily")
MIN_IC_NAMES_PER_DAY = 5
MIN_IC_DAYS = 3

_ST_PATTERN = re.compile(r"ST|退", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"^\d{8}$")

# 内置因子：name -> (回看交易日数, 方向)。score = sign × (adj_close_t / adj_close_{t-n} - 1)
_BUILTIN_FACTORS: dict[str, tuple[int, int]] = {
    "mom5": (5, 1),
    "mom10": (10, 1),
    "mom20": (20, 1),
    "mom60": (60, 1),
    "rev5": (5, -1),
    "rev20": (20, -1),
}

__all__ = [
    "MarketData",
    "load_market",
    "compute_market_benchmark",
    "run_backtest",
    "compute_named_factor",
    "limit_threshold",
    "main",
]


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _norm_date(value: Any) -> str:
    """归一化日期为 YYYYMMDD 字符串；非法输入直接报错（不静默吞）。"""
    text = str(value).strip().replace("-", "").replace("/", "")
    if not _DATE_PATTERN.fullmatch(text):
        raise ValueError(f"日期格式非法（需 YYYYMMDD）：{value!r}")
    return text


def _normalize_date_series(series: pd.Series) -> pd.Series:
    """向量化日期归一化（factor_df 可能有百万行，不能逐行 map Python 函数）。"""
    out = (
        series.astype("string")
        .str.strip()
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )
    bad = ~out.str.fullmatch(r"\d{8}")
    if bool(bad.fillna(True).any()):
        sample = series[bad.fillna(True)].head(3).tolist()
        raise ValueError(f"factor_df.date 含非法日期（需 YYYYMMDD），例：{sample}")
    return out.astype(str)


def limit_threshold(ts_code: str, name: str | None = None) -> float:
    """涨跌停判定阈值（百分点，单边）。主板 9.5（10% 板），创业板/科创板 19.5，北交所 29.5，ST 4.5。"""
    code = str(ts_code).split(".")[0]
    is_st = bool(name and _ST_PATTERN.search(str(name)))
    if code.startswith(("300", "301", "688", "689")):
        return 19.5
    if code.startswith(("4", "8", "920")):
        return 29.5
    if is_st:
        return 4.5
    return 9.5


def _build_thresholds(codes: Sequence[str], names: Mapping[str, str] | None) -> np.ndarray:
    names = names or {}
    return np.array([limit_threshold(code, names.get(code)) for code in codes], dtype=float)


def _safe_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except Exception:
        return str(path)


# --------------------------------------------------------------------------- #
# 市场数据
# --------------------------------------------------------------------------- #
class MarketData:
    """回测所需的行情矩阵（一次构建、多次复用）。

    属性：
      dates      交易日字符串升序列表
      codes      股票代码列表
      adj        [date, code] 复权收盘价（close × adj_factor），缺价为 NaN
      pct        [date, code] 当日涨跌幅（%），缺价为 NaN
      thresholds [code] 涨跌停阈值（百分点，单边）
      names      code -> 名称（仅用于 ST 阈值识别）
      limit_up / limit_down  list[set[int]]，按交易日下标给出 limit_list_d 名单
    """

    def __init__(
        self,
        dates: Sequence[str],
        codes: Sequence[str],
        adj: np.ndarray,
        pct: np.ndarray,
        thresholds: np.ndarray,
        names: Mapping[str, str] | None = None,
        limit_up_by_date: Mapping[str, Iterable[str]] | None = None,
        limit_down_by_date: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.dates: list[str] = [str(d) for d in dates]
        self.codes: list[str] = [str(c) for c in codes]
        self.adj = np.asarray(adj, dtype=float)
        self.pct = np.asarray(pct, dtype=float)
        self.thresholds = np.asarray(thresholds, dtype=float)
        if self.adj.shape != (len(self.dates), len(self.codes)):
            raise ValueError("adj 形状与 dates×codes 不一致")
        if self.pct.shape != self.adj.shape:
            raise ValueError("pct 形状与 adj 不一致")
        self.names: dict[str, str] = dict(names or {})
        self._date_idx = {d: i for i, d in enumerate(self.dates)}
        self._code_idx = {c: j for j, c in enumerate(self.codes)}
        self.limit_up = self._index_limit_sets(limit_up_by_date)
        self.limit_down = self._index_limit_sets(limit_down_by_date)

    def _index_limit_sets(self, mapping: Mapping[str, Iterable[str]] | None) -> list[set[int]]:
        out: list[set[int]] = [set() for _ in self.dates]
        for raw_date, codes in (mapping or {}).items():
            try:
                date = _norm_date(raw_date)
            except ValueError:
                continue
            pos = self._date_idx.get(date)
            if pos is None:
                continue
            for code in codes:
                j = self._code_idx.get(str(code))
                if j is not None:
                    out[pos].add(j)
        return out

    # -- 查询 ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self.dates)

    def date_index(self, date: str) -> int | None:
        return self._date_idx.get(str(date))

    def code_index(self, code: str) -> int | None:
        return self._code_idx.get(str(code))

    def next_trade_date(self, date: str) -> str | None:
        pos = self.date_index(date)
        if pos is None or pos + 1 >= len(self.dates):
            return None
        return self.dates[pos + 1]

    def is_limit_up_idx(self, date_pos: int, code_pos: int) -> bool:
        if code_pos in self.limit_up[date_pos]:
            return True
        value = self.pct[date_pos, code_pos]
        return bool(np.isfinite(value) and value >= self.thresholds[code_pos])

    def is_limit_down_idx(self, date_pos: int, code_pos: int) -> bool:
        if code_pos in self.limit_down[date_pos]:
            return True
        value = self.pct[date_pos, code_pos]
        return bool(np.isfinite(value) and value <= -self.thresholds[code_pos])

    def is_limit_up(self, date: str, code: str) -> bool:
        date_pos, code_pos = self.date_index(date), self.code_index(code)
        if date_pos is None or code_pos is None:
            return False
        return self.is_limit_up_idx(date_pos, code_pos)

    def is_limit_down(self, date: str, code: str) -> bool:
        date_pos, code_pos = self.date_index(date), self.code_index(code)
        if date_pos is None or code_pos is None:
            return False
        return self.is_limit_down_idx(date_pos, code_pos)

    # -- 构建 ------------------------------------------------------------- #
    @classmethod
    def from_long(
        cls,
        daily: pd.DataFrame,
        *,
        names: Mapping[str, str] | None = None,
        limit_up: Mapping[str, Iterable[str]] | None = None,
        limit_down: Mapping[str, Iterable[str]] | None = None,
    ) -> "MarketData":
        """由长表行情构建矩阵。

        必需列：trade_date, ts_code, close；可选：adj_factor（缺省=1）、pct_chg、pre_close。
        缺价（停牌）就是该 (trade_date, ts_code) 不存在，矩阵中即 NaN。
        """
        if not isinstance(daily, pd.DataFrame):
            raise TypeError("daily 必须是 pandas.DataFrame")
        missing = {"trade_date", "ts_code", "close"} - set(daily.columns)
        if missing:
            raise ValueError(f"行情长表缺列：{sorted(missing)}")
        df = daily.copy()
        df["trade_date"] = _normalize_date_series(df["trade_date"])
        df["ts_code"] = df["ts_code"].astype(str).str.strip()
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        if "adj_factor" in df.columns:
            df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
        else:
            df["adj_factor"] = 1.0
        df["adj_factor"] = df["adj_factor"].fillna(1.0)
        df = df.dropna(subset=["close"])
        df["adj_close"] = df["close"] * df["adj_factor"]
        if "pct_chg" in df.columns:
            df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
        elif "pre_close" in df.columns:
            pre = pd.to_numeric(df["pre_close"], errors="coerce")
            df["pct_chg"] = (df["close"] / pre - 1.0) * 100.0
        else:
            df["pct_chg"] = np.nan
        df = df.sort_values(["trade_date", "ts_code"], kind="stable")
        df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
        dates = sorted(df["trade_date"].unique().tolist())
        codes = sorted(df["ts_code"].unique().tolist())
        if not codes or not dates:
            raise ValueError("行情长表为空")
        adj = (
            df.pivot(index="trade_date", columns="ts_code", values="adj_close")
            .reindex(index=dates, columns=codes)
            .to_numpy(dtype=float)
        )
        pct = (
            df.pivot(index="trade_date", columns="ts_code", values="pct_chg")
            .reindex(index=dates, columns=codes)
            .to_numpy(dtype=float)
        )
        thresholds = _build_thresholds(codes, names)
        return cls(dates, codes, adj, pct, thresholds, names=names,
                   limit_up_by_date=limit_up, limit_down_by_date=limit_down)


def _load_stock_names(data_dir: Path) -> dict[str, str]:
    """读 stock_basic（分片 parquet），返回 code -> name。仅用于 ST 涨跌停阈值识别。"""
    files = sorted(glob.glob(str(data_dir / "stock_basic" / "*.parquet")))
    if not files:
        return {}
    frames = []
    for path in files:
        try:
            frames.append(pd.read_parquet(path, columns=["ts_code", "name"]))
        except Exception:
            continue
    if not frames:
        return {}
    raw = pd.concat(frames, ignore_index=True)
    raw["ts_code"] = raw["ts_code"].astype(str)
    raw = raw.drop_duplicates(subset=["ts_code"], keep="last")
    return dict(zip(raw["ts_code"], raw["name"].fillna("").astype(str)))


def _load_limit_list(limit_dir: Path, dates: Sequence[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """读 limit_list_d：limit=U -> 涨停名单，D -> 跌停名单（Z=炸板，不算收盘涨跌停）。"""
    up: dict[str, set[str]] = {}
    down: dict[str, set[str]] = {}
    if not limit_dir.is_dir():
        return up, down
    for date in dates:
        path = limit_dir / f"d_{date}.parquet"
        if not path.exists():
            continue
        try:
            frame = pd.read_parquet(path, columns=["ts_code", "limit"])
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        flags = frame["limit"].astype(str).str.upper()
        codes = frame["ts_code"].astype(str)
        u = set(codes[flags == "U"].tolist())
        d = set(codes[flags == "D"].tolist())
        if u:
            up[date] = u
        if d:
            down[date] = d
    return up, down


def load_market(
    start: str,
    end: str,
    *,
    lookback_days: int = 0,
    data_dir: str | Path | None = None,
    with_limits: bool = True,
    with_names: bool = True,
) -> MarketData:
    """从 quant_backtest/data/tushare 日线仓加载 [start-lookback_days, end] 行情矩阵。

    lookback_days 为「交易日」缓冲，仅用于因子计算（如 mom20 需要 start 前 20 个交易日），
    回测区间仍由 run_backtest(start, end) 决定。
    """
    data_root = Path(data_dir) if data_dir else DATA
    daily_dir = data_root / "daily"
    files = sorted(glob.glob(str(daily_dir / "d_*.parquet")))
    if not files:
        raise FileNotFoundError(f"未找到日线数据：{daily_dir}")
    all_dates = [Path(f).stem[2:] for f in files]
    start = _norm_date(start)
    end = _norm_date(end)
    if start > end:
        raise ValueError(f"start({start}) 不能晚于 end({end})")
    lo = bisect_left(all_dates, start)
    hi = bisect_right(all_dates, end)
    if hi <= lo:
        raise ValueError(f"{start}~{end} 无日线数据")
    lo = max(0, lo - max(0, int(lookback_days)))
    selected = all_dates[lo:hi]

    adj_dir = data_root / "adj_factor"
    frames: list[pd.DataFrame] = []
    want = ["ts_code", "trade_date", "close", "pre_close", "pct_chg"]
    for date in selected:
        daily_path = daily_dir / f"d_{date}.parquet"
        try:
            frame = pd.read_parquet(daily_path, columns=want)
        except Exception:
            frame = pd.read_parquet(daily_path)
            frame = frame[[c for c in want if c in frame.columns]]
        frame = frame.copy()
        frame["trade_date"] = date  # 以文件名日期为准，防脏 trade_date
        adj_path = adj_dir / f"d_{date}.parquet"
        if adj_path.exists():
            adj = pd.read_parquet(adj_path, columns=["ts_code", "adj_factor"])
            frame = frame.merge(adj, on="ts_code", how="left")
        else:
            frame["adj_factor"] = 1.0
        frames.append(frame)
    daily = pd.concat(frames, ignore_index=True)
    names = _load_stock_names(data_root) if with_names else {}
    up, down = _load_limit_list(data_root / "limit_list_d", selected) if with_limits else ({}, {})
    return MarketData.from_long(daily, names=names, limit_up=up, limit_down=down)


# --------------------------------------------------------------------------- #
# 基准
# --------------------------------------------------------------------------- #
def _benchmark_daily_returns(market: MarketData, start: str, end: str) -> tuple[list[str], np.ndarray]:
    """等权全市场日收益：每个交易日对当日有价个股取复权收益等权平均（日频再平衡）。"""
    range_dates = [d for d in market.dates if start <= d <= end]
    if len(range_dates) < 2:
        return [], np.zeros(0, dtype=float)
    start_pos = market.date_index(range_dates[0])
    end_pos = market.date_index(range_dates[-1])
    assert start_pos is not None and end_pos is not None
    returns: list[float] = []
    for pos in range(start_pos + 1, end_pos + 1):
        prev_price = market.adj[pos - 1]
        curr_price = market.adj[pos]
        mask = np.isfinite(prev_price) & np.isfinite(curr_price) & (prev_price > 0)
        count = int(mask.sum())
        if count >= 10:
            returns.append(float(np.mean(curr_price[mask] / prev_price[mask] - 1.0)))
        else:
            returns.append(0.0)
    return range_dates[1:], np.asarray(returns, dtype=float)


def compute_market_benchmark(market: MarketData, start: str, end: str) -> dict[str, Any]:
    """同窗口等权全市场基准（复权价、日频等权再平衡），用于与因子组合对照。"""
    start = _norm_date(start)
    end = _norm_date(end)
    dates, returns = _benchmark_daily_returns(market, start, end)
    nav = np.concatenate([[1.0], np.cumprod(1.0 + returns)]) if len(returns) else np.array([1.0])
    total_return = float(nav[-1] - 1.0)
    running_max = np.maximum.accumulate(nav)
    mdd = float(np.min(nav / running_max - 1.0))
    annual_return = None
    vol_ann = None
    if len(returns) > 0:
        annual_return = float((1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / len(returns)) - 1.0)
    if len(returns) >= 2:
        vol_ann = float(np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    return {
        "name": "等权全市场（复权价、日频等权再平衡）",
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6) if annual_return is not None else None,
        "mdd": round(mdd, 6),
        "vol_ann": round(vol_ann, 6) if vol_ann is not None else None,
        "trading_days": len(dates) + 1,
    }


# --------------------------------------------------------------------------- #
# 因子
# --------------------------------------------------------------------------- #
def _coerce_external_factor(raw: Any) -> pd.DataFrame | None:
    """把外部因子库返回值尽量规整为 [date, ts_code, score]。"""
    if isinstance(raw, pd.DataFrame):
        frame = raw.reset_index() if raw.index.name in ("date", "trade_date") else raw
        rename = {}
        for col in frame.columns:
            low = str(col).lower()
            if low in ("trade_date", "date") and "date" not in rename.values():
                rename[col] = "date"
            elif low in ("ts_code", "code", "symbol") and "ts_code" not in rename.values():
                rename[col] = "ts_code"
            elif low in ("score", "value", "factor") and "score" not in rename.values():
                rename[col] = "score"
        frame = frame.rename(columns=rename)
        if {"date", "ts_code", "score"} <= set(frame.columns):
            return frame[["date", "ts_code", "score"]]
    return None


def _compute_from_factor_lib(name: str, market: MarketData, start: str, end: str) -> tuple[pd.DataFrame, str] | None:
    """可选复用 tools/research/factor_lib.py（支线 A）；不存在/接口不符则返回 None 由调用方兜底。"""
    path = Path(__file__).resolve().parent / "factor_lib.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("factor_lib_for_bt", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # 因子库并行开发中，失败不应拖垮回测
        print(f"[bt_harness] factor_lib 加载失败，退回内置因子：{exc}", file=sys.stderr)
        return None
    for attr in ("compute_factor", "compute", "build_factor", "factor_values", "build"):
        func = getattr(module, attr, None)
        if not callable(func):
            continue
        try:
            params = inspect.signature(func).parameters
        except (TypeError, ValueError):
            continue
        kwargs: dict[str, Any] = {}
        if "market" in params:
            kwargs["market"] = market
        if "start" in params:
            kwargs["start"] = start
        if "end" in params:
            kwargs["end"] = end
        try:
            raw = func(name, **kwargs)
        except TypeError:
            try:
                raw = func(name)
            except Exception:
                continue
        except Exception:
            continue
        frame = _coerce_external_factor(raw)
        if frame is not None and not frame.empty:
            return frame, f"{name}（复用 tools/research/factor_lib.py）"
    return None


def compute_named_factor(
    name: str,
    market: MarketData,
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """按名称计算因子，返回 (factor_df, 口径描述)。

    内置：mom5/mom10/mom20/mom60（动量）、rev5/rev20（反转），全部用复权价 close×adj_factor。
    非内置名称会尝试复用 tools/research/factor_lib.py（存在且接口匹配时）。
    """
    key = str(name).strip().lower()
    if key in _BUILTIN_FACTORS:
        lookback, sign = _BUILTIN_FACTORS[key]
        panel = pd.DataFrame(market.adj, index=market.dates, columns=market.codes)
        score = sign * (panel / panel.shift(lookback) - 1.0)
        score.index.name = "date"
        score.columns.name = "ts_code"
        long = score.stack().rename("score").reset_index()
        long = long.dropna(subset=["score"]).reset_index(drop=True)
        long["date"] = long["date"].astype(str)
        long["ts_code"] = long["ts_code"].astype(str)
        if sign >= 0:
            desc = f"{key} = 复权收盘价(t)/复权收盘价(t-{lookback}) - 1（close×adj_factor）"
        else:
            desc = f"{key} = -(复权收盘价(t)/复权收盘价(t-{lookback}) - 1)（close×adj_factor）"
        return long, desc
    external = _compute_from_factor_lib(key, market, start or market.dates[0], end or market.dates[-1])
    if external is not None:
        return external
    raise ValueError(
        f"未知因子 {name!r}：内置仅支持 {sorted(_BUILTIN_FACTORS)}；"
        "其它因子请显式传入 factor_df，或让 tools/research/factor_lib.py 暴露 compute_factor(name, market=...)"
    )


# --------------------------------------------------------------------------- #
# 错误记录
# --------------------------------------------------------------------------- #
class _ErrorLog:
    """错误/告警记录：明细按 max_errors 截断，summary 始终给全量计数。"""

    def __init__(self, max_items: int = 200) -> None:
        self.max_items = max(1, int(max_items))
        self.items: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()

    def add(self, error_type: str, *, date: str = "", ts_code: str = "", detail: str = "") -> None:
        self.counts[error_type] += 1
        if len(self.items) < self.max_items:
            self.items.append({"type": error_type, "date": str(date), "ts_code": str(ts_code), "detail": detail})

    def summary(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


# --------------------------------------------------------------------------- #
# 核心回测
# --------------------------------------------------------------------------- #
def _normalize_factor_df(factor_df: Any) -> pd.DataFrame:
    """校验并规整因子为 [date, ts_code, score]（date 归一 YYYYMMDD，score 转数值去 NaN 去重）。"""
    if factor_df is None:
        raise ValueError("factor_df 不能为 None；需 DataFrame[date, ts_code, score]")
    frame = pd.DataFrame(factor_df)
    missing = [c for c in ("date", "ts_code", "score") if c not in frame.columns]
    if missing:
        raise ValueError(f"factor_df 缺列：{missing}（需 date, ts_code, score；信号日收盘可知）")
    frame = frame[["date", "ts_code", "score"]].copy()
    frame["date"] = _normalize_date_series(frame["date"])
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip()
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"])
    frame = frame.drop_duplicates(subset=["date", "ts_code"], keep="last")
    if frame.empty:
        return frame.reset_index(drop=True)
    return frame.sort_values(["date", "ts_code"], kind="stable").reset_index(drop=True)


def _rebalance_signal_dates(factor: pd.DataFrame, range_dates: Sequence[str], mode: str) -> list[str]:
    """从因子日期里挑调仓信号日：daily=每个交易日；weekly=每个 ISO 周内最后一个有因子的交易日。"""
    available = set(range_dates)
    factor_dates = sorted(set(factor["date"].tolist()) & available)
    if mode == "daily":
        return factor_dates
    buckets: dict[tuple[int, int], str] = {}
    for date in factor_dates:  # 已升序，同周后者覆盖前者
        iso = datetime.strptime(date, "%Y%m%d").date().isocalendar()
        buckets[(iso.year, iso.week)] = date
    return sorted(buckets.values())


def _candidate_lists(factor: pd.DataFrame) -> dict[str, list[str]]:
    """每个信号日按 score 降序（同分按代码升序，保证确定性）的候选代码列表。"""
    ordered = factor.sort_values(["date", "score", "ts_code"], ascending=[True, False, True], kind="stable")
    return {str(date): group["ts_code"].tolist() for date, group in ordered.groupby("date", sort=False)}


def _compute_ic(
    factor: pd.DataFrame,
    market: MarketData,
    start: str,
    end: str,
    min_cross_section: int,
) -> dict[str, Any] | None:
    """日度截面 IC：score_t vs 复权收益 ret_{t+1}。

    只有因子近似全市场（单日截面中位数 >= min_cross_section）才计算，否则返回 None。
    收益口径：adj_close(t+1)/adj_close(t)-1（T 日收盘信号 → T→T+1 日收益，标准因子日报口径，
    与组合 T+1 收盘建仓、T+1→T+2 起计收益不是同一条，见文档）。
    """
    in_range = factor[(factor["date"] >= start) & (factor["date"] <= end)]
    if in_range.empty:
        return None
    per_date = in_range.groupby("date")["ts_code"].nunique()
    if per_date.empty or float(per_date.median()) < float(min_cross_section):
        return None
    code_pos = {code: j for j, code in enumerate(market.codes)}
    score_rows: dict[str, np.ndarray] = {}
    for date, group in in_range.groupby("date", sort=False):
        mapped = group["ts_code"].map(code_pos)
        mask = mapped.notna().to_numpy()
        if not mask.any():
            continue
        arr = np.full(len(market.codes), np.nan, dtype=float)
        positions = mapped.to_numpy()[mask].astype(int)
        arr[positions] = group["score"].to_numpy(dtype=float)[mask]
        score_rows[str(date)] = arr
    ic_list: list[float] = []
    rank_ic_list: list[float] = []
    for date, scores in score_rows.items():
        pos = market.date_index(date)
        if pos is None or pos + 1 >= len(market.dates):
            continue
        price_now = market.adj[pos]
        price_next = market.adj[pos + 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            forward = price_next / price_now - 1.0
        mask = np.isfinite(scores) & np.isfinite(forward)
        if int(mask.sum()) < MIN_IC_NAMES_PER_DAY:
            continue
        x = scores[mask]
        y = forward[mask]
        if np.std(x) <= 0 or np.std(y) <= 0:
            continue
        ic_list.append(float(np.corrcoef(x, y)[0, 1]))
        rx = pd.Series(x).rank().to_numpy()
        ry = pd.Series(y).rank().to_numpy()
        rank_ic_list.append(float(np.corrcoef(rx, ry)[0, 1]))
    if len(ic_list) < MIN_IC_DAYS:
        return None
    ic_arr = np.asarray(ic_list, dtype=float)
    rank_arr = np.asarray(rank_ic_list, dtype=float)
    ic_std = float(np.std(ic_arr, ddof=1)) if len(ic_arr) >= 2 else 0.0
    rank_std = float(np.std(rank_arr, ddof=1)) if len(rank_arr) >= 2 else 0.0
    return {
        "mean": round(float(np.mean(ic_arr)), 6),
        "mean_rank": round(float(np.mean(rank_arr)), 6),
        "ir": round(float(np.mean(ic_arr) / ic_std), 6) if ic_std > 0 else None,
        "rank_ir": round(float(np.mean(rank_arr) / rank_std), 6) if rank_std > 0 else None,
        "n_days": int(len(ic_arr)),
        "win_rate": round(float(np.mean(ic_arr > 0)), 4),
        "definition": "日度截面 corr(score_t, ret_{t+1})；ret=复权收盘价(t+1)/复权收盘价(t)-1；"
                      "mean=Pearson 均值、mean_rank=Spearman 均值、n_days=有效交易日数",
    }


def run_backtest(
    factor_df: Any,
    start: str,
    end: str,
    top_n: int = 50,
    cost_bp: float = 30.0,
    rebalance: str = "weekly",
    *,
    market: MarketData | None = None,
    label: str | None = None,
    include_holdings: bool = False,
    min_ic_cross_section: int = 100,
    max_errors: int = 200,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """统一口径回测：T+1 收盘建仓、等权 top_n、复权收益、涨跌停/停牌约束、换手成本。

    参数
    ----
    factor_df : DataFrame[date, ts_code, score]，date 为信号日（收盘可知）。
    start, end : YYYYMMDD（含端点）。
    top_n : 组合持股数上限（等权）。
    cost_bp : 单边成本（bp）。成本 = 换手 × cost_bp/10000 × NAV；换手 = Σ|w_target−w_drift|（双边口径）。
    rebalance : "weekly"（每周最后交易日信号）或 "daily"（每日信号）。
    market : MarketData；缺省时自动 load_market(start, end)。
    label : 因子/策略标签，写入 meta。
    include_holdings : True 时每个调仓事件附持仓代码列表（便于校验口径）。
    min_ic_cross_section : IC 计算门槛（单日截面中位数）；低于则 ic=None。

    返回
    ----
    dict：schema_version / label / meta / nav / total_return / annual_return / mdd /
    vol_ann / sharpe / avg_turnover / avg_holdings / ic / ic_mean / rebalance_count /
    errors / error_summary / rebalances / metrics。
    """
    start = _norm_date(start)
    end = _norm_date(end)
    if start > end:
        raise ValueError(f"start({start}) 不能晚于 end({end})")
    if rebalance not in REBALANCE_MODES:
        raise ValueError(f"rebalance 仅支持 {REBALANCE_MODES}，收到 {rebalance!r}")
    top_n = int(top_n)
    if top_n < 1:
        raise ValueError("top_n 必须 >= 1")
    cost_bp = float(cost_bp)
    if cost_bp < 0:
        raise ValueError("cost_bp 不能为负")
    factor = _normalize_factor_df(factor_df)
    if market is None:
        market = load_market(start, end, data_dir=data_dir)
    if not isinstance(market, MarketData):
        raise TypeError("market 必须是 MarketData 实例")
    range_dates = [d for d in market.dates if start <= d <= end]
    if not range_dates:
        raise ValueError(f"{start}~{end} 区间内无交易日数据")

    errors = _ErrorLog(max_errors)
    if factor.empty:
        errors.add("empty_factor", detail="factor_df 为空/全 NaN：不建仓，NAV 恒为 1.0")

    # 调仓信号 -> T+1 建仓日（无下一交易日或 T+1 超出区间则跳过并记录）
    signal_dates = _rebalance_signal_dates(factor, range_dates, rebalance) if not factor.empty else []
    candidates = _candidate_lists(factor) if not factor.empty else {}
    schedule: dict[str, tuple[str, list[int]]] = {}
    for signal_date in signal_dates:
        signal_pos = market.date_index(signal_date)
        if signal_pos is None:
            continue
        if signal_pos + 1 >= len(market.dates):
            errors.add("entry_out_of_range", date=signal_date, detail="信号日无下一交易日数据（期末信号不建仓）")
            continue
        entry_date = market.dates[signal_pos + 1]
        if not (start <= entry_date <= end):
            errors.add("entry_out_of_range", date=signal_date, detail=f"T+1={entry_date} 超出回测区间，不建仓")
            continue
        code_list = candidates.get(signal_date) or []
        code_positions: list[int] = []
        for code in code_list:
            pos = market.code_index(code)
            if pos is not None:
                code_positions.append(pos)
        if not code_positions:
            errors.add("empty_candidates", date=signal_date, detail="该信号日无可交易候选（代码不在行情数据内）")
            continue
        schedule[entry_date] = (signal_date, code_positions)
    rebalance_count = len(schedule)

    holdings: dict[int, float] = {}
    last_price: dict[int, float] = {}
    cash = 1.0  # 初始资金（NAV=1.0 空仓）
    nav_rows: list[dict[str, Any]] = []
    rebalance_rows: list[dict[str, Any]] = []
    first_entry_pos: int | None = None

    start_pos = market.date_index(range_dates[0])
    end_pos = market.date_index(range_dates[-1])
    assert start_pos is not None and end_pos is not None

    for pos in range(start_pos, end_pos + 1):
        date = market.dates[pos]
        # 1) 持仓日收益（复权价；停牌/缺价则估值冻结，并记录）
        if pos > start_pos and holdings:
            for code_pos in list(holdings):
                price = market.adj[pos, code_pos]
                prev_price = last_price.get(code_pos)
                if np.isfinite(price) and prev_price is not None and np.isfinite(prev_price) and prev_price > 0:
                    holdings[code_pos] *= float(price) / float(prev_price)
                    last_price[code_pos] = float(price)
                else:
                    errors.add(
                        "valuation_frozen",
                        date=date,
                        ts_code=market.codes[code_pos],
                        detail="停牌/缺价：该持仓估值保持不变",
                    )

        if date in schedule:
            signal_date, code_positions = schedule[date]
            prices = market.adj[pos]
            held_before = dict(holdings)
            before_set = set(held_before)
            nav_before = cash + sum(held_before.values())

            # 2) 识别不可卖（停牌缺价 / 跌停）持仓：保留原估值继续持有
            unsellable: set[int] = set()
            for code_pos in held_before:
                price = prices[code_pos]
                if not np.isfinite(price) or price <= 0:
                    unsellable.add(code_pos)
                    errors.add(
                        "sell_suspended",
                        date=date,
                        ts_code=market.codes[code_pos],
                        detail="停牌/缺价不可卖：保留持仓",
                    )
                elif market.is_limit_down_idx(pos, code_pos):
                    unsellable.add(code_pos)
                    errors.add(
                        "sell_limit_down",
                        date=date,
                        ts_code=market.codes[code_pos],
                        detail="跌停不可卖：保留持仓",
                    )
            unsellable_value = sum(held_before[c] for c in unsellable)
            # 不可卖保留仓占用 top_n 名额：保证调仓后持仓数 <= top_n
            slots = max(0, top_n - len(unsellable))
            if slots == 0 and code_positions:
                errors.add(
                    "no_buy_slots",
                    date=date,
                    detail=f"不可卖保留仓 {len(unsellable)} 只已占满 top_n={top_n}，本期不再新买",
                )

            # 3) 从信号日排序里挑可买目标（涨停不可买、停牌不可买）
            targets: list[int] = []
            skipped_limit_up = 0
            skipped_suspended = 0
            for code_pos in code_positions:
                if len(targets) >= slots:
                    break
                if code_pos in unsellable:  # 已保留在不可卖仓里，不占新买名额
                    continue
                price = prices[code_pos]
                if not np.isfinite(price) or price <= 0:
                    skipped_suspended += 1
                    errors.add(
                        "buy_suspended",
                        date=date,
                        ts_code=market.codes[code_pos],
                        detail="停牌/缺价不可买：跳过",
                    )
                    continue
                if market.is_limit_up_idx(pos, code_pos):
                    skipped_limit_up += 1
                    errors.add(
                        "buy_limit_up",
                        date=date,
                        ts_code=market.codes[code_pos],
                        detail=f"涨停不可买：pct_chg={market.pct[pos, code_pos]:.2f}",
                    )
                    continue
                targets.append(code_pos)
            target_set = set(targets)

            # 4) 目标市值 + 单边换手
            investable = max(0.0, nav_before - unsellable_value)
            target_value = investable / len(targets) if targets and investable > 0 else 0.0
            new_values: dict[int, float] = {c: held_before[c] for c in unsellable}
            for code_pos, value in held_before.items():
                if code_pos in unsellable:
                    continue
                new_values[code_pos] = target_value if code_pos in target_set else 0.0
            for code_pos in targets:
                new_values.setdefault(code_pos, target_value)
            traded_notional = sum(
                abs(new_values.get(c, 0.0) - held_before.get(c, 0.0)) for c in (before_set | target_set)
            )
            turnover = traded_notional / nav_before if nav_before > 0 else 0.0
            cost = turnover * (cost_bp / 10_000.0) * nav_before

            # 5) 扣成本：从新买入目标等比例缩水（换手估算，二阶误差忽略）
            if targets and investable > 0:
                scale = max(0.0, 1.0 - cost / investable)
                for code_pos in targets:
                    new_values[code_pos] = new_values.get(code_pos, 0.0) * scale
                cash = 0.0
            else:
                cash = max(0.0, nav_before - unsellable_value - cost)
            holdings = {c: v for c, v in new_values.items() if v > 1e-12}
            # 6) 更新最后成交/估值价
            for code_pos in holdings:
                price = prices[code_pos]
                if np.isfinite(price) and price > 0:
                    last_price[code_pos] = float(price)
            last_price = {c: p for c, p in last_price.items() if c in holdings}
            nav_after = cash + sum(holdings.values())

            n_buy = sum(1 for c in targets if c not in before_set)
            n_sell = sum(1 for c in before_set if c not in unsellable and c not in target_set)
            row: dict[str, Any] = {
                "date": date,
                "signal_date": signal_date,
                "turnover": round(turnover, 6),
                "cost_pct": round(cost / nav_before * 100.0, 4) if nav_before > 0 else 0.0,
                "n_target": len(targets),
                "n_slots": slots,
                "n_buy": n_buy,
                "n_sell": n_sell,
                "n_unsellable": len(unsellable),
                "n_skipped_limit_up": skipped_limit_up,
                "n_skipped_suspended": skipped_suspended,
                "n_holdings": len(holdings),
                "nav": round(nav_after, 6),
            }
            if include_holdings:
                row["holdings"] = sorted(market.codes[c] for c in holdings)
                row["target"] = sorted(market.codes[c] for c in targets)
            rebalance_rows.append(row)
            if first_entry_pos is None:
                first_entry_pos = pos - start_pos

        nav = cash + sum(holdings.values())
        nav_rows.append({"date": date, "nav": round(nav, 6), "n": len(holdings)})

    # ------------------------------------------------------------------ #
    # 汇总指标（MDD 逐日对历史峰值比较；与总收益只在「全程未创新高」时相等）
    # ------------------------------------------------------------------ #
    nav_array = np.array([row["nav"] for row in nav_rows], dtype=float)
    total_return = float(nav_array[-1] / nav_array[0] - 1.0) if len(nav_array) else 0.0
    running_max = np.maximum.accumulate(nav_array)
    drawdown = nav_array / running_max - 1.0
    mdd = float(np.min(drawdown)) if len(nav_array) else 0.0
    trough_pos = int(np.argmin(drawdown)) if len(drawdown) else 0
    peak_pos = int(np.argmax(nav_array[: trough_pos + 1])) if len(nav_array) else 0
    mdd_detail: dict[str, Any] | None = None
    if len(nav_array) and mdd < 0:
        mdd_detail = {
            "peak_date": nav_rows[peak_pos]["date"],
            "peak_nav": round(float(nav_array[peak_pos]), 6),
            "trough_date": nav_rows[trough_pos]["date"],
            "trough_nav": round(float(nav_array[trough_pos]), 6),
            "mdd": round(float(mdd), 6),
            "note": "逐日 NAV 对历史峰值回撤的最小值；若 NAV 全程未创新高，则数值上与总收益相等",
        }
    annual_return: float | None = None
    vol_ann: float | None = None
    sharpe: float | None = None
    if first_entry_pos is not None:
        days_after_entry = len(nav_array) - 1 - first_entry_pos
        if days_after_entry > 0:
            annual_return = float((1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / days_after_entry) - 1.0)
        if len(nav_array) >= first_entry_pos + 3:
            daily_rets = nav_array[1:] / nav_array[:-1] - 1.0
            daily_rets = daily_rets[first_entry_pos:]
            daily_rets = daily_rets[np.isfinite(daily_rets)]
            if len(daily_rets) >= 2:
                std = float(np.std(daily_rets, ddof=1))
                vol_ann = std * float(np.sqrt(TRADING_DAYS_PER_YEAR))
                sharpe = float(np.mean(daily_rets) / std * np.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else None
    turnover_values = [row["turnover"] for row in rebalance_rows]
    avg_turnover = float(np.mean(turnover_values)) if turnover_values else None
    avg_holdings = float(np.mean([row["n"] for row in nav_rows])) if nav_rows else 0.0
    ic_payload = _compute_ic(factor, market, start, end, min_ic_cross_section) if not factor.empty else None
    ic_mean = ic_payload["mean"] if ic_payload else None

    first_entry_date = range_dates[first_entry_pos] if first_entry_pos is not None else None

    # 同窗口等权全市场基准 + 策略相对基准的 beta / 超额（解释组合涨跌是否被基准解释）
    benchmark = compute_market_benchmark(market, start, end)
    _, benchmark_returns = _benchmark_daily_returns(market, start, end)
    strategy_returns = nav_array[1:] / nav_array[:-1] - 1.0 if len(nav_array) >= 2 else np.zeros(0)
    beta_vs_benchmark: float | None = None
    corr_vs_benchmark: float | None = None
    align = min(len(strategy_returns), len(benchmark_returns))
    if align >= 3:
        strat = strategy_returns[-align:]
        bench = benchmark_returns[-align:]
        mask = np.isfinite(strat) & np.isfinite(bench)
        if int(mask.sum()) >= 3:
            bench_var = float(np.var(bench[mask], ddof=1))
            if bench_var > 0:
                beta_vs_benchmark = float(np.cov(strat[mask], bench[mask], ddof=1)[0, 1] / bench_var)
                corr_vs_benchmark = float(np.corrcoef(strat[mask], bench[mask])[0, 1])
    excess_return = float(total_return - benchmark["total_return"])

    meta = {
        "start": start,
        "end": end,
        "top_n": top_n,
        "cost_bp": cost_bp,
        "rebalance": rebalance,
        "trading_days": len(nav_rows),
        "rebalance_count": rebalance_count,
        "universe": len(market.codes),
        "first_entry_date": first_entry_date,
        "last_date": nav_rows[-1]["date"] if nav_rows else None,
        "data_start": market.dates[0],
        "data_end": market.dates[-1],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cost_model": "成本 = 换手 × cost_bp/10000 × NAV；换手 = Σ|w_target−w_drift|（双边，全换仓=2.0）",
        "return_model": "复权价 close×adj_factor；T+1 收盘建仓，T+1 当日及之前收益不计入",
        "limit_model": "pct_chg 阈值（主板9.5/双创19.5/北交所29.5/ST4.5）或 limit_list_d(U/D)",
        "ic_min_cross_section": min_ic_cross_section,
    }
    metrics = {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6) if annual_return is not None else None,
        "mdd": round(mdd, 6),
        "mdd_detail": mdd_detail,
        "vol_ann": round(vol_ann, 6) if vol_ann is not None else None,
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        "avg_turnover": round(avg_turnover, 6) if avg_turnover is not None else None,
        "avg_holdings": round(avg_holdings, 4),
        "max_holdings": max(row["n"] for row in nav_rows) if nav_rows else 0,
        "rebalance_count": rebalance_count,
        "trading_days": len(nav_rows),
        "first_entry_date": first_entry_date,
        "benchmark": benchmark,
        "excess_return": round(excess_return, 6),
        "beta_vs_benchmark": round(beta_vs_benchmark, 6) if beta_vs_benchmark is not None else None,
        "corr_vs_benchmark": round(corr_vs_benchmark, 6) if corr_vs_benchmark is not None else None,
        "ic": ic_payload,
        "ic_mean": round(ic_mean, 6) if ic_mean is not None else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "label": label or "factor",
        "meta": meta,
        "nav": nav_rows,
        "total_return": metrics["total_return"],
        "annual_return": metrics["annual_return"],
        "mdd": metrics["mdd"],
        "mdd_detail": mdd_detail,
        "vol_ann": metrics["vol_ann"],
        "sharpe": metrics["sharpe"],
        "avg_turnover": metrics["avg_turnover"],
        "avg_holdings": metrics["avg_holdings"],
        "max_holdings": metrics["max_holdings"],
        "ic": ic_payload,
        "ic_mean": metrics["ic_mean"],
        "rebalance_count": rebalance_count,
        "benchmark": benchmark,
        "excess_return": metrics["excess_return"],
        "beta_vs_benchmark": metrics["beta_vs_benchmark"],
        "corr_vs_benchmark": metrics["corr_vs_benchmark"],
        "errors": errors.items,
        "error_summary": errors.summary(),
        "rebalances": rebalance_rows,
        "metrics": metrics,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bt_harness",
        description="统一回测口径 bt_harness v1（T+1 收盘建仓 / 复权 / 涨跌停 / 停牌 / 成本 / 再平衡 / IC）",
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="跑因子回测并输出 JSON")
    run.add_argument("--factor", default="mom20", help="内置 mom5/mom10/mom20/mom60/rev5/rev20；其它走 factor_lib")
    run.add_argument("--start", default="20260101", help="回测起始日 YYYYMMDD（含）")
    run.add_argument("--end", default="20261030", help="回测截止日 YYYYMMDD（含）")
    run.add_argument("--top", type=int, default=50, help="等权持股数上限（默认 50）")
    run.add_argument("--cost-bp", type=float, default=30.0, help="单边成本 bp（默认 30）")
    run.add_argument("--rebalance", choices=REBALANCE_MODES, default="weekly", help="调仓频率（默认 weekly）")
    run.add_argument("--lookback", type=int, default=25, help="因子前置交易日缓冲（默认 25）")
    run.add_argument("--out", default=str(DEFAULT_DEMO_OUT), help="输出 JSON 路径")
    run.add_argument("--min-ic-cs", type=int, default=100, help="IC 全市场判定门槛（单日截面中位数）")
    run.add_argument("--include-holdings", action="store_true", help="调仓事件附持仓/目标代码列表")
    run.add_argument("--quiet", action="store_true", help="只写文件不打印摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 2

    start = _norm_date(args.start)
    end = _norm_date(args.end)
    market = load_market(start, end, lookback_days=max(0, int(args.lookback)))
    factor_df, factor_desc = compute_named_factor(args.factor, market, start=start, end=end)
    report = run_backtest(
        factor_df,
        start,
        end,
        top_n=args.top,
        cost_bp=args.cost_bp,
        rebalance=args.rebalance,
        market=market,
        label=args.factor,
        include_holdings=args.include_holdings,
        min_ic_cross_section=args.min_ic_cs,
    )
    report["meta"]["factor"] = args.factor
    report["meta"]["factor_desc"] = factor_desc
    report["meta"]["data_dir"] = _safe_relative(DATA)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        summary = {
            key: report.get(key)
            for key in (
                "label",
                "total_return",
                "annual_return",
                "mdd",
                "vol_ann",
                "sharpe",
                "avg_turnover",
                "avg_holdings",
                "max_holdings",
                "ic_mean",
                "rebalance_count",
                "excess_return",
                "beta_vs_benchmark",
            )
        }
        summary["benchmark"] = report.get("benchmark")
        summary["error_summary"] = report.get("error_summary")
        print(json.dumps(summary, ensure_ascii=False))
        print(f"# 输出: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
