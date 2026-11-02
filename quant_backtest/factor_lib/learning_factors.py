# -*- coding: utf-8 -*-
"""quant_backtest/factor_lib/learning_factors.py · W2 学习因子落地（8 个日线级因子）。

背景：研发计划 W2 提前启动。从「学习能力图谱」确定 8 个**日线级可立即实现**的因子，
本模块把它们落地为可复用的**纯函数**（no IO / no 全局状态 / 输入即输出），并附真实数据抽样脚本。

设计纪律（与本仓 quant_backtest/baseline_factors.py 等既有基线因子同风格）
--------------------------------------------------------------------------
  · 纯 pandas / numpy；因子函数只做计算，不做数据 IO；批量抽样由 `sample_factors()` 负责；
  · **PIT**：所有可交易因子只用 t 日及以前数据（rolling / shift(≥0) 向后看）；
    唯一例外是 `post_limit_behavior`（涨停后 N 日收益/回落）——它使用未来 N 日数据，
    属**事后描述/学习标签**，严禁作为交易信号按事件日索引（MEETING.md 判例 H29 第三类陷阱）；
  · 缺字段/样本不足一律降级返回 NaN（不抛异常、不静默填 0），并在抽样报告里记录覆盖率与缺口；
  · 不改动既有因子（tools/research/factor_lib.py 等），本模块是新增学习因子层；
  · 每个因子既有「单序列纯函数」，也能对 Panel 型 DataFrame（日期 × 代码）向量化调用。

8 个因子（W2-1..4）
-------------------
1. ma_convergence            均线粘合度 = std(MA5,MA10,MA20,MA60) / MA20（越小越粘合）
2. bull_alignment_strength   多头排列强度 = (MA5 − MA60) / MA60，另有 cross_section_percentile 横截面分位
3. macd_divergence_strength  MACD 背离强度 = 价格 60 日区间分位 − DIF 60 日区间分位（正=顶背离）
4. volume_breakout_strength  放量突破幅度 = (vol/MA(vol,50)) × (close/前60日平台高点 − 1)
5. platform_consolidation_score 平台整理 = 贴近 MA20 天数占比 × (1 − 归一化 60 日极差/均值)
6. chip_concentration        筹码集中度 = (90% 成本上沿 − 下沿) / 中位成本（cyq_chips）
7. post_limit_behavior       涨停后行为 = 涨停后 N 日收益 / N 日内最大回落（事后描述，非 PIT 信号）
8. moneyflow_strength        资金流强度 = 主力净流入 / 成交额 5 日均（moneyflow）

用法
----
    from learning_factors import ma_convergence, compute_factor_frame
    python -X utf8 quant_backtest/factor_lib/learning_factors.py list
    python -X utf8 quant_backtest/factor_lib/learning_factors.py sample \
        --out-prefix research/reports/learning_factors_20261103 --n-days 60 --n-codes 50
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
DEFAULT_DATA_ROOT = REPO / "quant_backtest" / "data" / "tushare"
OUT_DIR = REPO / "research" / "reports"

LIB_VERSION = "1.0.0"
DEFAULT_NDAYS = 60
DEFAULT_NCODES = 50
DEFAULT_SEED = 42
WARMUP_DAYS = 130          # MA60/MACD/平台因子需要的历史交易日
MIN_PERIODS_FACTOR = 60


# ============================================================ 基础工具
def _ma(s: pd.Series | pd.DataFrame, n: int) -> pd.Series | pd.DataFrame:
    return s.rolling(n, min_periods=n).mean()


def _num(x, nd: int = 4):
    """JSON 安全数值（None/NaN → None）。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, nd)


def _range_position(s: pd.Series, window: int) -> pd.Series:
    """当前值在最近 window 日区间中的分位（0=区间最低，1=区间最高）；区间无波动 → NaN。"""
    hi = s.rolling(window, min_periods=window).max()
    lo = s.rolling(window, min_periods=window).min()
    span = (hi - lo).replace(0.0, np.nan)
    return (s - lo) / span


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=1).mean()


# ============================================================ 8 个因子纯函数
def ma_convergence(close: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """均线粘合度 = std(MA5, MA10, MA20, MA60) / MA20（总体标准差，ddof=0）。

    |值| 越小 → 四条均线越粘合（变盘前夜）；MA60 不足 60 日处为 NaN。
    支持 Series（单票）与 DataFrame（日期 × 代码）。
    """
    ma5 = _ma(close, 5)
    ma10 = _ma(close, 10)
    ma20 = _ma(close, 20)
    ma60 = _ma(close, 60)
    if isinstance(close, pd.DataFrame):
        mats = np.stack([np.asarray(ma5), np.asarray(ma10), np.asarray(ma20), np.asarray(ma60)], axis=-1)
        flat = mats.reshape(-1, 4)
        std = pd.DataFrame(flat).std(axis=1, ddof=0, skipna=False).to_numpy()
        std = std.reshape(mats.shape[:2])
        out = std / np.asarray(ma20)
        return pd.DataFrame(out, index=close.index, columns=close.columns)
    mats = pd.concat([ma5, ma10, ma20, ma60], axis=1)
    std = mats.std(axis=1, ddof=0).where(mats.notna().all(axis=1))
    return std / ma20.replace(0.0, np.nan)


def bull_alignment_strength(close: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """多头排列强度 = (MA5 − MA60) / MA60（PIT：只用 t 及以前；MA5<MA60 时为负）。

    横截面分位请用 `cross_section_percentile()` 对该原始强度逐日取 pct 排名。
    """
    ma5 = _ma(close, 5)
    ma60 = _ma(close, 60)
    return (ma5 - ma60) / ma60.replace(0.0, np.nan)


def cross_section_percentile(frame: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """横截面分位：对每个交易日（行）在代码间做 pct rank（0~1）；NaN 保留 NaN。"""
    if isinstance(frame, pd.DataFrame):
        return frame.rank(axis=1, pct=True)
    return frame.rank(pct=True)


def macd_divergence_strength(close: pd.Series | pd.DataFrame, window: int = 60) -> pd.Series | pd.DataFrame:
    """MACD 背离强度 = 价格 60 日区间分位 − DIF 60 日区间分位。

    DIF = EMA12 − EMA26；正 = 价格创新高而 DIF 未同步（顶背离/动能不足），负 = 底背离。
    区间无波动（如长期横盘）返回 NaN；窗口不足 window 日为 NaN。
    """
    def _one(c: pd.Series) -> pd.Series:
        dif = _ema(c, 12) - _ema(c, 26)
        return _range_position(c, window) - _range_position(dif, window)

    if isinstance(close, pd.DataFrame):
        cols = {col: _one(close[col]) for col in close.columns}
        return pd.DataFrame(cols, index=close.index)
    return _one(close)


def volume_breakout_strength(close: pd.Series | pd.DataFrame, vol: pd.Series | pd.DataFrame,
                             window: int = 60, vol_window: int = 50) -> pd.Series | pd.DataFrame:
    """放量突破幅度 = 量比(vol / MA(vol,50)) × 突破幅度(close / 前 window 日平台高点 − 1)。

    平台高点用 shift(1) 取「上一交易日之前」的 window 日最高收盘 → PIT 安全；
    未突破（close ≤ 平台高点）时突破幅度 ≤0，因子同步 ≤0；历史不足为 NaN。
    """
    platform_high = close.rolling(window, min_periods=window).max().shift(1)
    breakout = close / platform_high.replace(0.0, np.nan) - 1.0
    vol_ma = vol.rolling(vol_window, min_periods=vol_window).mean()
    vol_ratio = vol / vol_ma.replace(0.0, np.nan)
    return vol_ratio * breakout


def platform_consolidation_score(close: pd.Series | pd.DataFrame, window: int = 60,
                                 band: float = 0.02) -> pd.Series | pd.DataFrame:
    """平台整理得分 = 贴近 MA20 天数占比 × (1 − min(60 日极差/均值 / 0.30, 1))，∈[0,1]。

    贴近定义：|close/MA20 − 1| ≤ band（默认 2%）；60 日极差/均值越小（越窄）得分越高。
    历史不足 60+60 日处为 NaN。
    """
    ma20 = _ma(close, 20)
    hug = ((close / ma20.replace(0.0, np.nan) - 1.0).abs() <= band).where(ma20.notna())
    hug_ratio = hug.astype(float).rolling(window, min_periods=window).mean()
    hi = close.rolling(window, min_periods=window).max()
    lo = close.rolling(window, min_periods=window).min()
    mean = close.rolling(window, min_periods=window).mean()
    height = (hi - lo) / mean.replace(0.0, np.nan)
    tight = (1.0 - (height / 0.30).clip(upper=1.0))
    score = hug_ratio * tight
    return score.where(np.isfinite(score) & (hug_ratio >= 0))


def chip_concentration(cyq: pd.DataFrame, q_low: float = 0.05, q_high: float = 0.95) -> pd.Series:
    """筹码集中度 = (q_high 成本 − q_low 成本) / 中位成本，基于 cyq_chips 的价位-筹码分布。

    输入：单票 cyq_chips DataFrame（列 trade_date, price, percent，percent 为该价位筹码占比 %）；
    输出：按 trade_date 索引的 Series（90% 成本区间宽度 / 中位成本，越小越集中）。
    缺列/空表/总筹码≤0 → 空 Series（调用方按 NaN 处理）。
    """
    need = {"trade_date", "price", "percent"}
    if not isinstance(cyq, pd.DataFrame) or cyq.empty or not need.issubset(cyq.columns):
        return pd.Series(dtype=float)
    rows: dict[str, float] = {}
    for dt, g in cyq.groupby("trade_date", sort=True):
        g = g[(g["price"] > 0) & (g["percent"] > 0)].sort_values("price")
        if g.empty:
            continue
        price = g["price"].to_numpy(dtype=float)
        weight = g["percent"].to_numpy(dtype=float)
        cum = np.cumsum(weight) / weight.sum()
        p_low = float(np.interp(q_low, cum, price))
        p_mid = float(np.interp(0.5, cum, price))
        p_high = float(np.interp(q_high, cum, price))
        if p_mid > 0:
            rows[str(dt)] = (p_high - p_low) / p_mid
    return pd.Series(rows, dtype=float).sort_index()


def post_limit_behavior(close: pd.Series, limit_up_flags: pd.Series, n: int = 5) -> pd.DataFrame:
    """涨停后行为（**事后描述/学习标签，非 PIT 交易信号**）。

    对每个涨停（limit_up_flags=True）事件日 i（涨停日收盘）计算：
      ret_n          = close[i+n]/close[i] − 1（涨停后 N 日累计收益）
      max_drawdown_n = min(close[i+1..i+n])/close[i] − 1（N 日内最大回落，≤0）
      next_day_ret   = close[i+1]/close[i] − 1（次日收益）
    未来数据不足 n 日的事件返回 NaN 行（仍列出事件日，便于统计样本缺口）。
    """
    close = pd.Series(close, dtype=float).sort_index()
    flags = pd.Series(limit_up_flags, dtype=bool).reindex(close.index).fillna(False)
    idx = list(close.index)
    event_pos = [i for i, flag in enumerate(flags.to_numpy()) if flag]
    rows: dict[str, dict] = {}
    for i in event_pos:
        base = close.iloc[i]
        if not np.isfinite(base) or base <= 0:
            continue
        future = close.iloc[i + 1:i + 1 + n]
        if len(future) >= n:
            ret_n = float(future.iloc[-1] / base - 1.0)
            dd = float(future.min() / base - 1.0)
            nd = float(future.iloc[0] / base - 1.0)
        else:
            ret_n = dd = nd = np.nan
        rows[str(idx[i])] = {"ret_n": ret_n, "max_drawdown_n": dd, "next_day_ret": nd,
                             "n_future": int(len(future))}
    if not rows:
        return pd.DataFrame(columns=["ret_n", "max_drawdown_n", "next_day_ret", "n_future"])
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "trade_date"
    return out


def moneyflow_strength(net_mf_amount: pd.Series, amount: pd.Series, window: int = 5,
                       unit_scale: float = 10.0) -> pd.Series:
    """资金流强度 = 主力净流入 / 成交额 5 日均（无量纲化）。

    unit_scale=10：moneyflow.net_mf_amount 单位万元、daily.amount 单位千元 → 统一到千元；
    该常数不影响横截面排序，仅影响绝对量级。成交额 5 日均 ≤0 / 数据不足 → NaN。
    """
    net = pd.Series(net_mf_amount, dtype=float)
    amt = pd.Series(amount, dtype=float).reindex(net.index)
    amt_ma = amt.rolling(window, min_periods=window).mean()
    return (net * unit_scale) / amt_ma.replace(0.0, np.nan)


# ============================================================ 因子注册表（按既有风格）
@dataclass(frozen=True)
class LearningFactorSpec:
    """学习因子元信息（名称/公式/数据依赖/PIT 属性/方向先验）。"""

    name: str
    title: str
    formula: str
    data_deps: tuple[str, ...]
    pit_safe: bool
    direction: int          # +1 值越大越偏多；-1 值越大越偏空；0 方向未知
    func_name: str
    extra_arg: str = ""
    note: str = ""


LEARNING_FACTOR_SPECS: dict[str, LearningFactorSpec] = {
    "ma_convergence": LearningFactorSpec(
        "ma_convergence", "均线粘合度", "std(MA5,MA10,MA20,MA60)/MA20",
        ("daily.close",), True, 0, "ma_convergence",
        note="值越小=均线越粘合；粘合末端常伴变盘，方向不做先验"),
    "bull_alignment_strength": LearningFactorSpec(
        "bull_alignment_strength", "多头排列强度", "(MA5−MA60)/MA60，另给横截面分位",
        ("daily.close",), True, 1, "bull_alignment_strength",
        note="正值=短期均线在长期之上；训练时用 cross_section_percentile 转分位"),
    "macd_divergence_strength": LearningFactorSpec(
        "macd_divergence_strength", "MACD 背离强度",
        "价格60日区间分位 − DIF(EMA12−EMA26) 60日区间分位",
        ("daily.close",), True, -1, "macd_divergence_strength",
        note="正=价格创新高而 DIF 未同步（顶背离，先验偏空）；负=底背离"),
    "volume_breakout_strength": LearningFactorSpec(
        "volume_breakout_strength", "放量突破幅度",
        "(vol/MA(vol,50)) × (close/前60日平台高点 − 1)",
        ("daily.close", "daily.vol"), True, 1, "volume_breakout_strength",
        note="平台高点用 shift(1) 只取历史（PIT）；未突破时为负"),
    "platform_consolidation_score": LearningFactorSpec(
        "platform_consolidation_score", "平台整理强度",
        "贴近MA20(|close/MA20−1|≤2%)天数占比 × (1−min(60日极差/均值/0.30,1))",
        ("daily.close",), True, 1, "platform_consolidation_score",
        note="值越大=整理越久越窄；历史不足 80 日→NaN"),
    "chip_concentration": LearningFactorSpec(
        "chip_concentration", "筹码集中度",
        "(90%成本上沿 − 90%成本下沿) / 中位成本（按筹码占比加权分位）",
        ("cyq_chips(trade_date,price,percent)",), True, -1, "chip_concentration",
        note="值越小=筹码越集中；数据仓仅 23 只代码 32 个交易日（2026-08~09），覆盖受限"),
    "post_limit_behavior": LearningFactorSpec(
        "post_limit_behavior", "涨停后行为（事后描述）",
        "涨停后 N 日累计收益 / N 日内最大回落（N=5）",
        ("daily.close", "limit_list_d.limit==U"), False, 0, "post_limit_behavior",
        note="**使用未来 N 日数据，仅作学习标签/行为描述，禁止作为交易信号**（判例 H29）"),
    "moneyflow_strength": LearningFactorSpec(
        "moneyflow_strength", "资金流强度",
        "net_mf_amount × 10 / 成交额5日均（万元→千元归一）",
        ("moneyflow.net_mf_amount", "daily.amount"), True, 1, "moneyflow_strength",
        note="正=净流入占成交额比例高；数据仓 moneyflow 覆盖 2023-01 起"),
}
FACTORS = list(LEARNING_FACTOR_SPECS)          # 与既有基线因子模块一致的因子名清单


def factor_rows() -> list[dict]:
    """因子清单（list/report 用）。"""
    return [{
        "name": spec.name, "title": spec.title, "formula": spec.formula,
        "data_deps": list(spec.data_deps), "pit_safe": spec.pit_safe,
        "direction": spec.direction, "func_name": spec.func_name,
        "note": spec.note,
    } for spec in LEARNING_FACTOR_SPECS.values()]


# ============================================================ 真实数据抽样
def _list_daily_dates(daily_dir: Path) -> list[str]:
    return sorted(Path(f).stem[2:] for f in glob.glob(str(daily_dir / "d_*.parquet")))


def _read_daily_window(data_root: Path, dates: list[str], codes: set[str]) -> dict[str, pd.DataFrame]:
    """读取窗口内日线字段并按 (date × code) 透视；沿用数据仓 d_YYYYMMDD.parquet 布局。"""
    frames = []
    for dt in dates:
        path = data_root / "daily" / f"d_{dt}.parquet"
        if not path.exists():
            continue
        df = pq.read_table(path, columns=["ts_code", "trade_date", "close", "vol", "amount",
                                          "pre_close", "pct_chg"]).to_pandas()
        df["trade_date"] = str(dt)
        frames.append(df[df["ts_code"].isin(codes)])
    if not frames:
        raise FileNotFoundError(f"窗口内无 daily 数据: {data_root / 'daily'}")
    long = pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
    return {col: long.pivot(index="trade_date", columns="ts_code", values=col)
            for col in ("close", "vol", "amount", "pre_close", "pct_chg")}


def load_sample_data(data_root: Path | str | None = None, n_days: int = DEFAULT_NDAYS,
                     n_codes: int = DEFAULT_NCODES, warmup: int = WARMUP_DAYS,
                     seed: int = DEFAULT_SEED) -> dict:
    """抽取最近 n_days 个交易日 × n_codes 只股票的日线样本（+ warmup 历史用于滚动窗口）。

    返回 dict：fields（日期×代码矩阵）、window_dates、all_dates、codes、data_end、data_root、
    以及 moneyflow / limit_up / cyq 三类扩展数据（缺失时为空表并在 meta 记录）。
    """
    root = Path(data_root) if data_root else DEFAULT_DATA_ROOT
    all_dates = _list_daily_dates(root / "daily")
    if not all_dates:
        raise FileNotFoundError(f"数据根无日线数据: {root / 'daily'}")
    load_dates = all_dates[-(int(n_days) + int(warmup)):]
    window_dates = load_dates[-int(n_days):]
    # 确定性抽样：取窗口末日有行情的代码中按 seed 抽 n_codes 只
    last_df = pq.read_table(root / "daily" / f"d_{window_dates[-1]}.parquet",
                            columns=["ts_code"]).to_pandas()
    universe = sorted(set(last_df["ts_code"].astype(str)))
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(universe, size=min(int(n_codes), len(universe)), replace=False).tolist())
    fields = _read_daily_window(root, load_dates, set(pick))
    fields = {k: v.reindex(index=load_dates, columns=pick) for k, v in fields.items()}

    # moneyflow（净流入，万元）
    mf_frames = []
    for dt in load_dates:
        path = root / "moneyflow" / f"d_{dt}.parquet"
        if path.exists():
            df = pq.read_table(path, columns=["ts_code", "trade_date", "net_mf_amount"]).to_pandas()
            df["trade_date"] = str(dt)
            mf_frames.append(df[df["ts_code"].isin(pick)])
    moneyflow = (pd.concat(mf_frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
                 if mf_frames else pd.DataFrame(columns=["trade_date", "ts_code", "net_mf_amount"]))

    # limit_list_d（涨停事件，limit == "U"）
    lim_frames = []
    for dt in window_dates:
        path = root / "limit_list_d" / f"d_{dt}.parquet"
        if path.exists():
            df = pq.read_table(path, columns=["ts_code", "trade_date", "limit"]).to_pandas()
            df["trade_date"] = str(dt)
            lim_frames.append(df[(df["limit"] == "U") & (df["ts_code"].isin(pick))])
    limit_up = (pd.concat(lim_frames, ignore_index=True).drop_duplicates(["trade_date", "ts_code"])
                if lim_frames else pd.DataFrame(columns=["trade_date", "ts_code", "limit"]))

    # cyq_chips（按代码分文件，只有部分代码有数据）
    cyq_frames = []
    for code in pick:
        stem = code.replace(".", "_")
        path = root / "cyq_chips" / f"{stem}.parquet"
        if path.exists():
            df = pq.read_table(path, columns=["trade_date", "price", "percent"]).to_pandas()
            df["trade_date"] = df["trade_date"].astype(str)
            df = df[df["trade_date"].isin(set(window_dates))]
            if not df.empty:
                df["ts_code"] = code
                cyq_frames.append(df)
    cyq = (pd.concat(cyq_frames, ignore_index=True) if cyq_frames
           else pd.DataFrame(columns=["trade_date", "price", "percent", "ts_code"]))

    return {
        "fields": fields, "window_dates": window_dates, "all_dates": load_dates,
        "codes": pick, "data_end": window_dates[-1], "data_root": str(root),
        "moneyflow": moneyflow, "limit_up": limit_up, "cyq": cyq,
        "sources": {
            "daily": f"{root / 'daily'}（窗口 {window_dates[0]}~{window_dates[-1]}，{len(pick)} 只）",
            "moneyflow": f"{len(moneyflow)} 行（窗口内净流入字段）",
            "limit_list_d": f"{len(limit_up)} 条涨停事件（limit==U）",
            "cyq_chips": f"{cyq['ts_code'].nunique() if len(cyq) else 0} 只代码 / {cyq['trade_date'].nunique() if len(cyq) else 0} 个交易日",
        },
    }


def _series_or_nan(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series(np.nan, index=index)
    return pd.Series(series, dtype=float).reindex(index)


def compute_factor_frame(sample: dict) -> dict[str, pd.DataFrame]:
    """在抽样数据上计算 8 个因子的「日期 × 代码」矩阵（缺失字段 → NaN 列/全 NaN）。"""
    fields = sample["fields"]
    close = fields["close"]
    vol = fields.get("vol")
    amount = fields.get("amount")
    dates = pd.Index(sample["window_dates"])
    codes = list(sample["codes"])
    win = close.reindex(index=dates, columns=codes)
    out: dict[str, pd.DataFrame] = {}

    full_close = close.reindex(columns=codes)
    out["ma_convergence"] = pd.DataFrame(ma_convergence(full_close)).reindex(index=dates)
    out["bull_alignment_strength"] = pd.DataFrame(bull_alignment_strength(full_close)).reindex(index=dates)
    out["macd_divergence_strength"] = pd.DataFrame(macd_divergence_strength(full_close)).reindex(index=dates)
    out["volume_breakout_strength"] = pd.DataFrame(
        volume_breakout_strength(full_close, vol.reindex(columns=codes))).reindex(index=dates)
    out["platform_consolidation_score"] = pd.DataFrame(
        platform_consolidation_score(full_close)).reindex(index=dates)

    # 6. 筹码集中度（仅覆盖 cyq_chips 有数据的代码/日期）
    chip = pd.DataFrame(np.nan, index=dates, columns=codes)
    cyq = sample.get("cyq", pd.DataFrame())
    if isinstance(cyq, pd.DataFrame) and len(cyq):
        for code, g in cyq.groupby("ts_code"):
            if code not in codes:
                continue
            s = chip_concentration(g)
            if len(s):
                chip.loc[chip.index.intersection(s.index), code] = s.reindex(chip.index.intersection(s.index))
    out["chip_concentration"] = chip

    # 7. 涨停后行为：每个「涨停事件」一行，字段 ret_n/max_drawdown_n/next_day_ret（事后描述）
    limit_rows = []
    limit_up = sample.get("limit_up", pd.DataFrame())
    if isinstance(limit_up, pd.DataFrame) and len(limit_up):
        for code, g in limit_up.groupby("ts_code"):
            if code not in codes or code not in full_close.columns:
                continue
            flags = pd.Series(False, index=full_close.index)
            flags.loc[flags.index.intersection(pd.Index(g["trade_date"]))] = True
            beh = post_limit_behavior(full_close[code], flags, n=5)
            beh = beh.loc[[d for d in beh.index if d in set(dates)]]
            for dt, row in beh.iterrows():
                limit_rows.append({"ts_code": code, "trade_date": str(dt),
                                   "ret_n": row["ret_n"], "max_drawdown_n": row["max_drawdown_n"],
                                   "next_day_ret": row["next_day_ret"], "n_future": row["n_future"]})
    behavior = pd.DataFrame(limit_rows) if limit_rows else pd.DataFrame(
        columns=["ts_code", "trade_date", "ret_n", "max_drawdown_n", "next_day_ret", "n_future"])

    # 8. 资金流强度（net_mf 与 amount 同按日期×代码对齐；缺数据 → NaN）
    all_dates = pd.Index(sample["all_dates"])
    mf = pd.DataFrame(np.nan, index=all_dates, columns=codes)
    moneyflow = sample.get("moneyflow", pd.DataFrame())
    if isinstance(moneyflow, pd.DataFrame) and len(moneyflow):
        mf = moneyflow.pivot(index="trade_date", columns="ts_code", values="net_mf_amount")
        mf = mf.reindex(index=all_dates, columns=codes)
    strength = pd.DataFrame(np.nan, index=all_dates, columns=codes)
    for code in codes:
        if code not in mf.columns or code not in amount.columns:
            continue
        strength[code] = moneyflow_strength(mf[code], amount[code])
    out["moneyflow_strength"] = strength.reindex(index=dates)

    return {"matrices": out, "post_limit_behavior": behavior, "window": win}


def _matrix_stats(mat: pd.DataFrame) -> dict:
    vals = np.asarray(mat, dtype=float).ravel()
    vals = vals[np.isfinite(vals)]
    n_expected = int(mat.size)
    if vals.size == 0:
        return {"n_valid": 0, "n_expected": n_expected, "coverage_pct": 0.0, "stats": None}
    std = float(np.std(vals, ddof=1)) if vals.size > 1 else 0.0
    return {
        "n_valid": int(vals.size), "n_expected": n_expected,
        "coverage_pct": _num(100.0 * vals.size / n_expected, 2),
        "stats": {"mean": _num(float(np.mean(vals)), 4), "std": _num(std, 4),
                  "min": _num(float(np.min(vals)), 4), "max": _num(float(np.max(vals)), 4),
                  "abs_median": _num(float(np.median(np.abs(vals))), 4)},
    }


def _examples(mat: pd.DataFrame, n: int = 5) -> list[dict]:
    """取最近一个有值的交易日，按 |值| 取前 n 个样例。"""
    for dt in reversed(mat.index):
        row = mat.loc[dt].dropna()
        if len(row):
            row = row.reindex(row.abs().sort_values(ascending=False).index)[:n]
            return [{"trade_date": str(dt), "ts_code": str(code), "value": _num(float(v), 6)}
                    for code, v in row.items()]
    return []


def _cyq_probe(data_root: Path, window_dates: list[str]) -> dict:
    """cyq_chips 全量探针：数据仓仅部分代码有筹码数据，单独统计其覆盖率与样例。"""
    files = sorted(glob.glob(str(data_root / "cyq_chips" / "*.parquet")))
    if not files:
        return {"available_codes": 0, "n_valid": 0, "note": "数据仓无 cyq_chips 目录/文件"}
    win = set(window_dates)
    examples = []
    n_valid = 0
    dates = set()
    for f in files:
        code = Path(f).stem.replace("_", ".")
        df = pq.read_table(f, columns=["trade_date", "price", "percent"]).to_pandas()
        df["trade_date"] = df["trade_date"].astype(str)
        df = df[df["trade_date"].isin(win)]
        if df.empty:
            continue
        s = chip_concentration(df)
        if s.empty:
            continue
        n_valid += int(s.notna().sum())
        dates |= set(s.index.astype(str))
        for dt, v in s.head(1).items():
            examples.append({"ts_code": code, "trade_date": str(dt), "value": _num(float(v), 6)})
    return {"available_codes": len(files), "codes_with_window_data": len(examples),
            "dates_with_data": len(dates), "n_valid": n_valid,
            "examples": examples[:8],
            "note": "筹码因子数据仓覆盖有限（仅 cyq_chips 有文件的代码/日期）；50 只随机样本未抽中，"
                    "故样本矩阵覆盖 0%，但因子本身在可用代码上可计算（见本探针）"}


def sample_factors(data_root: str | Path | None = None, n_days: int = DEFAULT_NDAYS,
                   n_codes: int = DEFAULT_NCODES, seed: int = DEFAULT_SEED) -> dict:
    """真实数据抽样 + 计算 8 因子 + 覆盖率统计 → 报告 payload（JSON 可序列化）。"""
    t0 = time.time()
    sample = load_sample_data(data_root=data_root, n_days=n_days, n_codes=n_codes, seed=seed)
    computed = compute_factor_frame(sample)
    root = Path(sample["data_root"])
    chip_probe = _cyq_probe(root, sample["window_dates"])
    close_win = computed["window"]
    missing_cells = int(close_win.isna().sum().sum())
    missing_codes = close_win.columns[close_win.isna().any()].tolist()
    mats = computed["matrices"]
    behavior = computed["post_limit_behavior"]
    report_factors: dict[str, dict] = {}
    data_gaps: list[str] = []
    for spec in LEARNING_FACTOR_SPECS.values():
        if spec.name == "post_limit_behavior":
            n_valid = int(behavior["ret_n"].notna().sum()) if len(behavior) else 0
            entry = {
                "name": spec.name, "title": spec.title, "formula": spec.formula,
                "data_deps": list(spec.data_deps), "pit_safe": spec.pit_safe,
                "direction": spec.direction, "note": spec.note,
                "n_events": int(len(behavior)), "n_valid": n_valid,
                "coverage_pct": _num(100.0 * n_valid / max(1, len(behavior)), 2) if len(behavior) else 0.0,
                "stats": ({"mean_ret_n": _num(behavior["ret_n"].mean(), 4),
                           "mean_max_drawdown_n": _num(behavior["max_drawdown_n"].mean(), 4),
                           "positive_ret_ratio": _num(float((behavior["ret_n"] > 0).mean()), 4)}
                          if n_valid else None),
                "examples": ([{"ts_code": r["ts_code"], "trade_date": r["trade_date"],
                               "ret_n": _num(r["ret_n"], 4),
                               "max_drawdown_n": _num(r["max_drawdown_n"], 4)}
                              for _, r in behavior.dropna(subset=["ret_n"]).head(5).iterrows()]
                             if len(behavior) else []),
            }
            report_factors[spec.name] = entry
            if n_valid == 0:
                data_gaps.append(f"{spec.name}: 窗口内无涨停事件（或 limit_list_d 缺失）→ 全部 NaN")
            continue
        mat = mats[spec.name]
        st = _matrix_stats(mat)
        entry = {
            "name": spec.name, "title": spec.title, "formula": spec.formula,
            "data_deps": list(spec.data_deps), "pit_safe": spec.pit_safe,
            "direction": spec.direction, "note": spec.note,
            "n_valid": st["n_valid"], "n_expected": st["n_expected"],
            "coverage_pct": st["coverage_pct"], "stats": st["stats"],
            "examples": _examples(mat),
        }
        if spec.name == "chip_concentration":
            entry["probe"] = chip_probe
        report_factors[spec.name] = entry
        if st["coverage_pct"] < 100.0:
            if spec.name == "chip_concentration":
                reason = (f"cyq_chips 数据仓仅 {chip_probe.get('available_codes')} 只代码/"
                          f"{chip_probe.get('dates_with_data')} 个窗口内交易日有数据，随机 50 只样本未覆盖；"
                          f"可用代码上 {chip_probe.get('n_valid')} 个有效值（见 probe）")
            else:
                reason = ("窗口内个股缺日线行（停牌/停牌复牌缺口）或滚动窗口历史不足；"
                          f"窗口内 close 缺失 {missing_cells} 格（{len(missing_codes)} 只有缺失）")
            data_gaps.append(f"{spec.name}: 覆盖率 {st['coverage_pct']}%（{reason}）")

    payload = {
        "schema_version": "learning_factors.v1", "lib": "learning_factors", "version": LIB_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": {
            "n_factors": len(LEARNING_FACTOR_SPECS), "implemented": len(LEARNING_FACTOR_SPECS),
            "n_days": int(n_days), "n_codes": int(n_codes), "seed": int(seed),
            "data_root": sample["data_root"], "data_end": sample["data_end"],
            "window_start": sample["window_dates"][0], "window_end": sample["window_dates"][-1],
            "load_dates": [sample["all_dates"][0], sample["all_dates"][-1]],
            "codes_sample": sample["codes"][:10], "sources": sample["sources"],
            "runtime_sec": round(time.time() - t0, 2),
        },
        "factors": report_factors,
        "data_gaps": data_gaps,
        "unimplemented": [],
        "notes": [
            "8 个因子全部实现为纯函数；缺口只来自数据仓覆盖（如 cyq_chips 仅 23 只/32 日）。",
            "post_limit_behavior 使用未来 N 日数据，属事后描述/学习标签，禁止作为交易信号（判例 H29）。",
            "均线/MACD/平台因子窗口 60 日，抽样窗口前段为 NaN 属预期（warmup 用于滚动窗口）。",
            "moneyflow 净流入单位万元、daily.amount 单位千元，ratio 已按 unit_scale=10 归一。",
        ],
    }
    return payload


# ============================================================ 报告渲染 / CLI
def render_markdown(payload: dict) -> str:
    meta = payload.get("meta", {})
    lines = [
        "# W2 学习因子落地报告（8 个日线级因子）", "",
        f"- 生成：{payload.get('generated_at')}；版本 v{payload.get('version')}；"
        f"实现 {meta.get('implemented')}/{meta.get('n_factors')} 个因子",
        f"- 抽样：最近 {meta.get('n_days')} 个交易日（{meta.get('window_start')}~{meta.get('window_end')}）"
        f"× {meta.get('n_codes')} 只（seed={meta.get('seed')}，数据截至 {meta.get('data_end')}）",
        f"- 数据根：`{meta.get('data_root')}`；运行 {meta.get('runtime_sec')}s", "",
        "## 一、因子清单与公式", "",
        "| # | 因子 | 公式 | 数据依赖 | PIT | 先验方向 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, (name, f) in enumerate(payload.get("factors", {}).items(), 1):
        pit = "是" if f.get("pit_safe") else "**否（事后描述）**"
        lines.append(f"| {i} | {name} | {f.get('formula')} | {', '.join(f.get('data_deps', []))} | "
                     f"{pit} | {f.get('direction')} | {f.get('note')} |")
    lines += ["", "## 二、真实数据覆盖率与样例", "",
              "| 因子 | 有效值 | 期望值 | 覆盖率% | 均值 | 中位\\|值\\| | 样例（最近交易日） |",
              "|---|---|---|---|---|---|---|"]
    for name, f in payload.get("factors", {}).items():
        stats = f.get("stats") or {}
        if name == "post_limit_behavior":
            lines.append(f"| {name} | {f.get('n_valid')} | {f.get('n_events')} 事件 | {f.get('coverage_pct')} | "
                         f"ret_n均值 {stats.get('mean_ret_n')} | 回落均值 {stats.get('mean_max_drawdown_n')} | "
                         f"{stats.get('positive_ret_ratio')} 正收益比例 |")
            continue
        ex = "; ".join(f"{e['ts_code']} {e['value']}" for e in f.get("examples", []))
        lines.append(f"| {name} | {f.get('n_valid')} | {f.get('n_expected')} | {f.get('coverage_pct')} | "
                     f"{stats.get('mean')} | {stats.get('abs_median')} | {ex} |")
    chip_probe = (payload.get("factors", {}).get("chip_concentration", {}) or {}).get("probe") or {}
    if chip_probe.get("available_codes"):
        lines += ["", "**筹码因子探针（cyq_chips 可用代码上的真实计算）**："
                  f"可用代码 {chip_probe.get('available_codes')} 只 / 窗口内 {chip_probe.get('dates_with_data')} 个交易日，"
                  f"有效值 {chip_probe.get('n_valid')} 个；样例："
                  + "; ".join(f"{e['ts_code']} {e['trade_date']}={e['value']}" for e in chip_probe.get("examples", [])[:5])]
    lines += ["", "## 三、数据缺口 / 降级记录", ""]
    gaps = payload.get("data_gaps", [])
    if gaps:
        lines += [f"- {g}" for g in gaps]
    else:
        lines.append("- 无（全部因子覆盖 100%）")
    lines += ["", "## 四、未实现项与原因", ""]
    unimpl = payload.get("unimplemented", [])
    lines += [f"- {u}" for u in unimpl] if unimpl else ["- 无：8 个因子全部实现为纯函数"]
    lines += ["", "## 五、口径与使用纪律", ""]
    lines += [f"- {n}" for n in payload.get("notes", [])]
    lines += ["", "## 六、测试", "",
              "- `tools/tests/test_learning_factors.py`（≥8 用例）：每因子小样本精确断言 + 边界/NaN + 注册表完整性。",
              "- 运行：`C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_learning_factors.py`", ""]
    return "\n".join(lines)


def write_outputs(payload: dict, prefix: str | Path) -> tuple[Path, Path]:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(render_markdown(payload), encoding="utf-8")
    return js, md


def cmd_list(as_json: bool) -> int:
    rows = factor_rows()
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"# learning_factors v{LIB_VERSION} · {len(rows)} 个日线级学习因子")
        for row in rows:
            pit = "PIT" if row["pit_safe"] else "非PIT"
            print(f"  {row['name']:<28} {row['title']:<14} [{pit}] {row['formula']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="learning_factors.py", description="W2 学习因子（8 个日线级）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="列出因子")
    p_list.add_argument("--json", action="store_true")
    p_sample = sub.add_parser("sample", help="真实数据抽样并输出报告")
    p_sample.add_argument("--out-prefix", default=str(OUT_DIR / "learning_factors_20261103"))
    p_sample.add_argument("--n-days", type=int, default=DEFAULT_NDAYS)
    p_sample.add_argument("--n-codes", type=int, default=DEFAULT_NCODES)
    p_sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_sample.add_argument("--data-root", default="")
    p_sample.add_argument("--json", action="store_true", help="stdout 打印完整 JSON")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.json)
    try:
        payload = sample_factors(data_root=(args.data_root or None), n_days=args.n_days,
                                 n_codes=args.n_codes, seed=args.seed)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[learning_factors] 抽样失败: {exc}", file=sys.stderr)
        return 2
    js, md = write_outputs(payload, args.out_prefix)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        meta = payload["meta"]
        print(f"# learning_factors v{LIB_VERSION} | {meta['implemented']}/{meta['n_factors']} 因子 | "
              f"抽样 {meta['window_start']}~{meta['window_end']} × {meta['n_codes']} 只 | "
              f"耗时 {meta['runtime_sec']}s")
        for name, f in payload["factors"].items():
            print(f"  {name:<28} coverage={f.get('coverage_pct')}%  "
                  f"n_valid={f.get('n_valid')}")
        for gap in payload["data_gaps"]:
            print(f"# gap: {gap}")
    print(f"# 输出: {js} / {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
