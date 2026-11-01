# -*- coding: utf-8 -*-
"""tools/research/book_batch_test.py · 量学战法批量四关初筛（2026-11-01，支线 D）。

任务
----
读 `research/strategy_specs/*.yaml`（24 项规格），筛出 `test_status=untested` 的 17 项，
把其中 computability ∈ {A, B} 的 13 项映射为可计算因子：
  - 能在 `tools/research/factor_lib.py` 里直接找到的，直接复用（LX22 凹口淘金 → `aokou`）；
  - 没有的，在**本文件内**实现为 `lxNN_*` 因子（不改 factor_lib.py；评测时以「作用域内临时注册」
    方式复用 factor_lib.evaluate_panel，退出即还原全局注册表，import/单测无副作用）；
  - 规格 computability=C 的 4 项（画线类 / 概念框架）不实现，标 F 并写明原因。
然后按 factor_lib 统一评测口径跑 2023-01-01 ~ 2026-10-30：
  - T 日收盘出信号 → **T+1 收盘入场** → T+21 收盘卖出；20 日 CAR = 个股复权买入持有收益
    − 等权市场同期复利收益 − 单边 30bp；
  - 按日横截面 Spearman IC（先验方向 directed）、事件因子按日聚合「信号组 − 非信号组」超额；
  - IS ≤ 2024-12-31 / OOS ≥ 2025-01-01；分年 IC；block bootstrap（块长 5 ≥ 20 日持有期重叠修正）；
  - 股票池 tradable（价 ≥ 2 元、成交额 ≥ 5000 万、上市 ≥ 250 日，均 T 日可知）。
输出 graded 结论（A/B/C/D/F）+ 建议优先深测清单，落 JSON + Markdown。

评级口径（本文件 `grade_entry`，与报告一致）
  A = OOS 同向显著：OOS directed IC t ≥ 2 且 bootstrap p<0.05 且 OOS 有向超额 > 0 且 IS 不强烈反向；
  B = 方向对但不显著：OOS directed IC t > 0 且 OOS 有向超额不反向；
  C = 不显著（含方向信息不足 / IC 与超额互相矛盾）；
  D = 反向（OOS directed IC t ≤ -2，或 t ≤ -1 且超额为负）；
  F = 不可计算 / 数据不足（C 级画线类，或事件数 < 30、OOS 有效日 < 20、OOS 事件 < 20）。

用法
----
    # 只打印 17 项映射（不载数据，秒级）
    python tools/research/book_batch_test.py --list

    # 全量初筛（约 3~5 分钟：载面板 ~55s + 13 因子评测）
    python tools/research/book_batch_test.py \
        --start 20230101 --end 20261030 --is-end 20241231 \
        --out research/reports/book_batch_test_20261101.json \
        --md research/reports/book_batch_test_20261101.md

PIT 纪律（research/MEETING.md 判例）：
  - 本文件所有因子只用 `shift(+n)` / 滚动过去窗口，无负 lag（见 tools/tests/test_book_batch_test.py 静态检查）；
  - 确认型形态（将军柱/元帅柱零号/黄金线/接力双阳/倍量不穿）信号一律落在**确认完成日**，
    由评测口径 T+1 收盘入场，不存在“用后 N 日数据按形态日索引”的前视；
  - 事件数 ≠ 独立样本：超额按日聚合 + block bootstrap，t 值只作初筛，不做 validated 结论。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 路径/依赖
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import factor_lib as fl          # noqa: E402  统一评测底座（不改本文件）
import stat_utils                # noqa: E402  统计修正工具（block bootstrap）
import strategy_specs            # noqa: E402  规格库 YAML 加载

BATCH_VERSION = "1.0.0"
SPEC_DIR = REPO / "research" / "strategy_specs"
OUT_JSON = REPO / "research" / "reports" / "book_batch_test_20261101.json"
OUT_MD = REPO / "research" / "reports" / "book_batch_test_20261101.md"

DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
DEFAULT_IS_END = "20241231"       # IS ≤ 2024-12-31，OOS ≥ 2025-01-01

HORIZON = fl.DEFAULT_HORIZON      # 20 个交易日
COST = fl.DEFAULT_COST            # 单边 30bp
COST_BP = int(round(COST * 10000))
BLOCK = fl.DEFAULT_BLOCK          # block bootstrap 块长 5（≥ 重叠修正要求）
SEED = fl.DEFAULT_SEED
DEFAULT_N_BOOT = 300
MIN_CROSS_SECTION = fl.DEFAULT_MIN_CROSS_SECTION   # 单日横截面最少 50 只
BLOCK_MIN_LEN = 15                # stat_utils.block_bootstrap_p 的最小样本（block*3）

# 评级阈值（写进报告，便于复核）
MIN_EVENTS = 30                   # 全窗口最少事件数
MIN_OOS_DAYS = 20                 # OOS 最少有效 IC 日
MIN_OOS_EVENTS = 20               # OOS 最少事件数
A_T = 2.0                         # A：OOS directed IC t 阈值
D_T = -2.0                        # D：反向显著阈值
D_WEAK_T = -1.0                   # D：弱反向（需超额同时为负）

GRADES = ("A", "B", "C", "D", "F")
GRADE_LABEL = {
    "A": "OOS 同向显著（建议深测）",
    "B": "方向对但不显著",
    "C": "不显著",
    "D": "反向",
    "F": "不可计算/数据不足",
}

# ================================================================ 因子实现
# 说明：12 个本文件内注册的因子 + 1 个复用 factor_lib（LX22 → aokou）
# 全部只用当日及以前数据；确认型形态信号落在确认完成日（评测口径 T+1 收盘入场）。
FACTOR_IMPLS: dict[str, dict] = {}


def _impl(name: str, kind: str, direction: int, description: str, source: str):
    """收集本文件内实现的因子（lazy 注册进 factor_lib.FACTORS，import 无副作用）。"""
    if kind not in ("event", "continuous"):
        raise ValueError(f"kind 必须为 event/continuous: {kind}")
    if direction not in (1, -1):
        raise ValueError(f"direction 必须为 +1/-1: {direction}")

    def deco(fn: Callable[[fl.Panel], pd.DataFrame]) -> Callable[[fl.Panel], pd.DataFrame]:
        if name in FACTOR_IMPLS:
            raise ValueError(f"因子重复实现: {name}")
        FACTOR_IMPLS[name] = {"kind": kind, "direction": int(direction),
                              "description": description, "source": source, "compute": fn}
        return fn

    return deco


def _build_specs() -> dict[str, fl.FactorSpec]:
    """把本文件实现的因子构造为 FactorSpec（不写入全局 factor_lib.FACTORS，避免污染其它单测）。"""
    return {
        name: fl.FactorSpec(name=name, family="量学形态", kind=meta["kind"],
                            direction=meta["direction"], description=meta["description"],
                            source=meta["source"], requires=("vol",), compute=meta["compute"])
        for name, meta in FACTOR_IMPLS.items()
    }


@contextmanager
def _scoped_registry():
    """临时把本文件实现的因子注册进 factor_lib.FACTORS，退出时恢复原状（幂等、无全局副作用）。"""
    added: list[str] = []
    built = _build_specs()
    try:
        for name, spec in built.items():
            if name not in fl.FACTORS:
                fl.FACTORS[name] = spec
                added.append(name)
        yield built
    finally:
        for name in added:
            fl.FACTORS.pop(name, None)


def all_factor_names() -> set[str]:
    """本批可用的全部因子名（factor_lib 现有 17 个 + 本文件实现 12 个）。"""
    return set(fl.FACTORS) | set(FACTOR_IMPLS)


def factor_meta(name: str) -> dict | None:
    """因子元信息（现有因子取注册表；新增因子取本文件实现表）。"""
    if name in FACTOR_IMPLS:
        meta = FACTOR_IMPLS[name]
        return {"name": name, "family": "量学形态", "kind": meta["kind"],
                "direction": meta["direction"], "description": meta["description"],
                "source": meta["source"], "requires": ["vol"], "impl": "new"}
    spec = fl.FACTORS.get(name)
    if spec is None:
        return None
    return {"name": spec.name, "family": spec.family, "kind": spec.kind,
            "direction": spec.direction, "description": spec.description,
            "source": spec.source, "requires": list(spec.requires), "impl": "existing"}


def compute_factor(name: str, panel: fl.Panel) -> pd.DataFrame:
    """计算任意批次因子（新增因子走本文件实现，现有因子走 factor_lib 注册表）。"""
    if name in FACTOR_IMPLS:
        return FACTOR_IMPLS[name]["compute"](panel)
    if name in fl.FACTORS:
        return fl.FACTORS[name].compute(panel)
    raise KeyError(f"未知因子: {name}")


# ---------------------------------------------------------------- 小工具
def _frame(p: fl.Panel, arr) -> pd.DataFrame:
    return pd.DataFrame(arr, index=p.dates, columns=p.codes)


def _min_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    """逐元素取最小（NaN 传播），保持 index/columns。"""
    arr = np.asarray(frames[0], dtype=float)
    for frame in frames[1:]:
        arr = np.minimum(arr, np.asarray(frame, dtype=float))
    return pd.DataFrame(arr, index=frames[0].index, columns=frames[0].columns)


def _vr_prev(p: fl.Panel) -> pd.DataFrame:
    """量比（前日对比）：vol / vol[T-1]，左推法“就近对比”。"""
    prev = p.vol.shift(1)
    return p.vol / prev.where(prev > 0)


def _vr5(p: fl.Panel) -> pd.DataFrame:
    """量比5：vol / 近5日均量（含当日，与 factor_lib 口径一致）。"""
    ma5 = fl._rolling(p.vol, 5, min_periods=3).mean()
    return p.vol / ma5.where(ma5 > 0)


def _any_shift_true(flag: pd.DataFrame, offsets: range) -> pd.DataFrame:
    """过去 offsets 个偏移内是否存在 True（不含当日）。"""
    acc = pd.DataFrame(False, index=flag.index, columns=flag.columns)
    for k in offsets:
        acc = acc | flag.shift(k).fillna(False).astype(bool)
    return acc


# ---------------------------------------------------------------- LX03 将军柱
@_impl("lx03_general_pillar", kind="event", direction=1,
       description=("将军柱（确认日信号）：基柱 T-3 为阳柱且量比5≥1.5；T-2..T 三日平均收盘 < 基柱收盘"
                    "（实顶）且三日最低收盘 ≥ 基柱实底×0.99 → T 为确认完成日，T+1 收盘入场"),
       source="伏击涨停·第8章 将军柱/卧底/搭梯（简化：双线判定确认日；卧底平底突破未实现）")
def _f_lx03_general_pillar(p: fl.Panel) -> pd.DataFrame:
    vr = _vr5(p)
    base = (p.pct_chg > 0) & (vr >= 1.5)                       # 基柱（阳柱、量比≥1.5）
    base3 = base.astype(float).shift(3) == 1.0                  # 基柱在 T-3
    base_top = p.close.shift(3)                                 # 阳柱实顶≈收盘
    base_low = p.open.shift(3)                                  # 阳柱实底≈开盘
    c2, c1, c0 = p.close.shift(2), p.close.shift(1), p.close
    avg3 = (c2 + c1 + c0) / 3.0
    min3 = _min_frames(c2, c1, c0)
    mask = base3 & (avg3 < base_top) & (min3 >= base_low * 0.99)
    valid = base_top.notna() & base_low.notna() & min3.notna() & vr.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX04 元帅柱·零号
@_impl("lx04_marshal_zero", kind="event", direction=1,
       description=("元帅柱·零号（确认日信号）：T-3 跳空阳柱（open>前日最高×1.005、收阳、量比5≥1.5）；"
                    "T-2..T 收盘守住元帅柱实顶×0.99 且最低不破实底×0.99；T 日放量阳线（vol≥前日×1.5）"
                    "站上实底 = 零号弹起，T+1 收盘入场"),
       source="伏击涨停·第9章 元帅柱零号战法（简化：跳空补空+三日不破+放量阳线确认）")
def _f_lx04_marshal_zero(p: fl.Panel) -> pd.DataFrame:
    vr = _vr5(p)
    gap = p.open > p.high.shift(1) * 1.005
    base = gap & (p.close > p.open) & (vr >= 1.5)               # 跳空阳柱（元帅柱基柱）
    base3 = base.astype(float).shift(3) == 1.0
    base_high = p.high.shift(3)
    base_low = p.low.shift(3)
    base_close = p.close.shift(3)
    c, low = p.close, p.low
    hold_top = ((c.shift(2) >= base_close * 0.99)
                & (c.shift(1) >= base_close * 0.99) & (c >= base_close * 0.99))
    hold_bottom = ((low.shift(2) >= base_low * 0.99)
                   & (low.shift(1) >= base_low * 0.99) & (low >= base_low * 0.99))
    zero = (p.close > p.open) & (_vr_prev(p) >= 1.5) & (c >= base_low * 0.99)
    mask = base3 & hold_top & hold_bottom & zero
    valid = base_high.notna() & base_low.notna() & base_close.notna() & vr.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX05 金线战法
@_impl("lx05_golden_line", kind="event", direction=1,
       description=("金线回踩（黄金线战法三步曲简化）：最近 20 日内有已确认黄金柱（factor_lib.golden_pillar，"
                    "信号=确认日）；以该基柱实顶（收盘）为金线；当日回踩金线不破（low≤line×1.01 且 "
                    "low≥line×0.995）、收盘守住 line×0.99 且收阳 = 碰线即起，T+1 收盘入场"),
       source="伏击涨停·第10-13章 金线战法三步曲（简化：绑定已确认黄金柱实顶，回踩不破）")
def _f_lx05_golden_line(p: fl.Panel) -> pd.DataFrame:
    gp = fl.FACTORS["golden_pillar"].compute(p)                # 确认日 0/1（PIT 已修）
    confirmed = gp.eq(1.0)
    acc = pd.DataFrame(False, index=p.dates, columns=p.codes)
    for k in range(0, 21):                                     # 金线来自 k 日前确认的黄金柱
        line = p.close.shift(3 + k)                            # 基柱在确认日再往前 3 日，实顶=基柱收盘
        touched = ((p.low <= line * 1.01) & (p.low >= line * 0.995)
                   & (p.close >= line * 0.99) & (p.close > p.open))
        acc = acc | (confirmed.shift(k).fillna(False).astype(bool) & touched)
    valid = p.close.notna() & p.open.notna() & p.low.notna()
    return fl._event(acc, valid)


# ---------------------------------------------------------------- LX06 长阴短柱
@_impl("lx06_long_yin_short", kind="event", direction=1,
       description=("长阴短柱（有底版）：T-1 日 pct_chg≤-3 且 vol≤前日×0.7 且收阴（长阴短柱）；"
                    "T-1 收盘 ≥ 近20日最低×1.02（代理“有底”，未贴地板）；T 日阳盖阴（收阳且收盘>"
                    "前阴开盘）→ T+1 收盘入场"),
       source="伏击涨停·第15章 长阴短柱战法（简化：量比≤0.7 代理短柱、20日低点支撑代理有底）")
def _f_lx06_long_yin_short(p: fl.Panel) -> pd.DataFrame:
    prev_pct = p.pct_chg.shift(1)
    prev_vol = p.vol.shift(1)
    prev2_vol = p.vol.shift(2)
    prev_close = p.close.shift(1)
    prev_open = p.open.shift(1)
    long_yin = (prev_pct <= -3.0) & (prev_vol <= prev2_vol * 0.7) & (prev_close < prev_open)
    support = fl._rolling(p.low, 20, min_periods=10).min().shift(1)
    has_bottom = prev_close >= support * 1.02
    cover = (p.close > p.open) & (p.close > prev_open)
    mask = long_yin & has_bottom & cover
    valid = prev_pct.notna() & prev_vol.notna() & prev2_vol.notna() & support.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX09 现场直憋
@_impl("lx09_suffocation", kind="event", direction=1,
       description=("现场直憋突破（简化）：8~33 日内出现大阴（pct_chg≤-3 且收阴）作框架；近20日高低振幅"
                    "≤20% 的箱体 + 5日均量 ≤ 百日（100日）均量×0.8（低量群）；当日 close>箱体上沿（前20日"
                    "最高）且量≥前日×1.9 → 解憋，T+1 收盘入场"),
       source="伏击涨停·第18章 现场直憋战法（简化：大阴框架→窄幅缩量箱体→放量突破）")
def _f_lx09_suffocation(p: fl.Panel) -> pd.DataFrame:
    big_yin = (p.pct_chg <= -3.0) & (p.close < p.open)
    box_top = fl._rolling(p.high, 20, min_periods=10).max().shift(1)
    box_bottom = fl._rolling(p.low, 20, min_periods=10).min().shift(1)
    narrow = box_top <= box_bottom * 1.20
    vma5 = fl._rolling(p.vol, 5, min_periods=3).mean().shift(1)
    vma100 = fl._rolling(p.vol, 100, min_periods=40).mean().shift(1)
    low_vol_group = vma5 <= vma100 * 0.8
    big_recent = fl._rolling(big_yin.astype(float), 26, min_periods=10).sum().shift(8) >= 1.0
    breakout = (p.close > box_top) & (_vr_prev(p) >= 1.9)
    mask = big_recent & narrow & low_vol_group & breakout
    valid = box_top.notna() & box_bottom.notna() & vma5.notna() & vma100.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX10 3121 标尺
@_impl("lx10_ruler3121", kind="event", direction=1,
       description=("3121 标尺（假跌后阳盖阴）：T-1 假跌 = pct_chg<0 且 vol≤前日×0.667（缩量31），"
                    "或 vol 创近100日新低（缩为百低）；T 日阳盖阴（收阳且收盘>前收）且收盘站上前柱"
                    "21 位（前日 (high+low)/2）→ T+1 收盘入场"),
       source="伏击涨停·第5章 3121 战法（简化：缩量比例阈值 2/3 + 百低 + 前柱中位收复）")
def _f_lx10_ruler3121(p: fl.Panel) -> pd.DataFrame:
    prev_pct = p.pct_chg.shift(1)
    prev_vol = p.vol.shift(1)
    prev2_vol = p.vol.shift(2)
    hundred_min = fl._rolling(p.vol, 100, min_periods=40).min().shift(2)
    fake_drop = (prev_pct < 0) & (prev_vol <= prev2_vol * 0.667)
    hundred_low = prev_vol <= hundred_min
    yang_cover = (p.close > p.open) & (p.close > p.close.shift(1))
    mid3121 = (p.high.shift(1) + p.low.shift(1)) / 2.0
    mask = (fake_drop | hundred_low) & yang_cover & (p.close >= mid3121)
    valid = prev_pct.notna() & prev_vol.notna() & prev2_vol.notna() & mid3121.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX11 间谍战法
@_impl("lx11_spy_small_vol", kind="event", direction=1,
       description=("间谍战法（小倍阳实底线回踩）：过去 10 日内小倍阳 ≥2 根（1.0≤vol/前日<1.9 且收阳），"
                    "以最近小倍阳实底（开盘）画水平线；当日回踩该线不破（line×0.99 ≤ low ≤ line×1.02）"
                    "且再收阳、收盘守住 line×0.99 → T+1 收盘入场"),
       source="伏击涨停·卷首图注/第6章 间谍战法（简化：小倍阳+实底水平线回踩）")
def _f_lx11_spy_small_vol(p: fl.Panel) -> pd.DataFrame:
    vr_prev = _vr_prev(p)
    small_double = (vr_prev >= 1.0) & (vr_prev < 1.9) & (p.close > p.open)
    past_count = fl._rolling(small_double.astype(float), 10, min_periods=5).sum().shift(1)
    dense = past_count >= 2                                    # 小倍阳密集（密探）
    acc = pd.DataFrame(False, index=p.dates, columns=p.codes)
    for k in range(1, 11):
        line = p.open.shift(k)
        base = small_double.shift(k).fillna(False).astype(bool)
        touch = (p.low <= line * 1.02) & (p.low >= line * 0.99)
        rebound = (p.close > p.open) & (p.close >= line * 0.99)
        acc = acc | (base & touch & rebound)
    mask = acc & dense
    valid = vr_prev.notna() & past_count.notna() & p.low.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX12 双阴出货
@_impl("lx12_double_yin_exit", kind="event", direction=-1,
       description=("双阴出货（风控/反向信号，direction=-1）：连续两根阴柱（close<open 或 pct_chg<0，"
                    "含低开高走假阳），且第二阴收盘 ≤ 第一阴收盘 或 第一阴跌破左侧阳线实底；"
                    "叠加趋势过滤 close ≤ 20 日收盘（下降途中双阴才有效）→ 先验看空"),
       source="涨停密码·第6章 双阴出货（出场信号；本初筛按反向事件检验）")
def _f_lx12_double_yin_exit(p: fl.Panel) -> pd.DataFrame:
    yin = (p.close < p.open) | (p.pct_chg < 0)                 # 阴柱（含假阳）
    double_yin = yin & yin.shift(1).fillna(False).astype(bool)
    second_break = p.close <= p.close.shift(1)
    first_break = p.close.shift(1) < p.open.shift(2)           # 第一阴跌破左侧阳线实底
    trigger = double_yin & (second_break | first_break)
    trend_down = p.close <= p.close.shift(20)
    mask = trigger & trend_down
    valid = (p.close.notna() & p.open.notna() & p.close.shift(1).notna()
             & p.close.shift(20).notna())
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX14 接力双阳
@_impl("lx14_relay_double_yang", kind="event", direction=1,
       description=("接力双阳（简化）：右阳距今日 3~5 日（保证“后3日不破实底”在 T 日可知）+ 左阳再早 3~5 日；"
                    "两柱均为小阳（1.0≤vol/前日<1.9 且收阳）且实底（开盘）重合（|a/b-1|≤1%）= 精准线；"
                    "右阳后收盘不破其实底×0.99（王牌柱确认）；当日收盘突破右阳实顶（high）→ T+1 收盘入场"),
       source="涨停密码·第5章 接力双阳（简化：小双阳+精准线+右阳王牌确认+突破右阳实顶）")
def _f_lx14_relay_double_yang(p: fl.Panel) -> pd.DataFrame:
    vr_prev = _vr_prev(p)
    small_yang = (p.close > p.open) & (vr_prev >= 1.0) & (vr_prev < 1.9)
    acc = pd.DataFrame(False, index=p.dates, columns=p.codes)
    for b in (3, 4, 5):                                        # 右阳距今日 b 日（b≥3 才有“后3日不破”）
        right = small_yang.shift(b).fillna(False).astype(bool)
        right_low = p.open.shift(b).where(p.open.shift(b) > 0)
        right_high = p.high.shift(b)
        hold_min = fl._rolling(p.close, b, min_periods=1).min()  # 近 b 日收盘最低（含今日）
        hold = hold_min >= right_low * 0.99
        breakout = p.close > right_high
        for gap in (3, 4, 5):
            left = b + gap
            left_ok = small_yang.shift(left).fillna(False).astype(bool)
            left_low = p.open.shift(left).where(p.open.shift(left) > 0)
            precise = ((left_low / right_low) - 1.0).abs() <= 0.01
            acc = acc | (left_ok & right & precise & hold & breakout)
    valid = vr_prev.notna() & p.open.notna() & p.high.notna() & p.close.shift(15).notna()
    return fl._event(acc, valid)


# ---------------------------------------------------------------- LX17 站稳凹峰
@_impl("lx17_concave_peak", kind="event", direction=1,
       description=("站稳凹峰（简化）：凹峰线 = 近20日最高开盘（大阴实顶代理，T-1 前）；近20日 high 触及"
                    "该线（±1%）≥3 次（契合点）；当日回踩不破（line×0.99 ≤ low ≤ line×1.01）、收盘守住"
                    "line×0.99 且收阳 = 站稳凹峰 → T+1 收盘入场"),
       source="涨停密码·第14章 站稳凹峰即将上攻（简化：阶段实顶水平线+多契合点+回踩不破）")
def _f_lx17_concave_peak(p: fl.Panel) -> pd.DataFrame:
    line = fl._rolling(p.open, 20, min_periods=10).max().shift(1)
    touches = pd.DataFrame(0.0, index=p.dates, columns=p.codes)
    for d in range(1, 21):
        h = p.high.shift(d)
        touches = touches + ((h - line).abs() <= line * 0.01).astype(float)
    hold = ((p.low <= line * 1.01) & (p.low >= line * 0.99)
            & (p.close >= line * 0.99) & (p.close > p.open))
    mask = (touches >= 3.0) & hold
    valid = line.notna() & p.low.notna() & p.close.notna()
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX19 牛股量形
@_impl("lx19_bull_shape_launch", kind="event", direction=1,
       description=("牛股量形·启动柱（简化五要素之①③）：百日低量群（20日均量 ≤ 100日均量×0.8）+ "
                    "孕育期横盘（60日高低振幅 ≤50%）+ 启动柱倍阳过左峰（vol≥前日×1.9 且 close>前20日"
                    "最高价）→ T+1 收盘入场（②大起大落/⑤黄金十字未实现）"),
       source="涨停密码·第16章 牛股量形（简化：百日低量+长期横盘+倍量过左峰）")
def _f_lx19_bull_shape_launch(p: fl.Panel) -> pd.DataFrame:
    vr_prev = _vr_prev(p)
    vma20 = fl._rolling(p.vol, 20, min_periods=10).mean()
    vma100 = fl._rolling(p.vol, 100, min_periods=40).mean()
    vol_low = vma20 <= vma100 * 0.8
    hi60 = fl._rolling(p.high, 60, min_periods=30).max()
    lo60 = fl._rolling(p.low, 60, min_periods=30).min()
    sideways = hi60 <= lo60 * 1.5
    left_peak = fl._rolling(p.high, 20, min_periods=10).max().shift(1)
    launch = (vr_prev >= 1.9) & (p.close > left_peak)
    mask = vol_low & sideways & launch
    valid = (vma20.notna() & vma100.notna() & sideways.notna()
             & left_peak.notna() & vr_prev.notna())
    return fl._event(mask, valid)


# ---------------------------------------------------------------- LX20 牛股三绝
@_impl("lx20_three_absolutes", kind="event", direction=1,
       description=("倍量不穿（牛股三绝之一，简化）：过去 1~10 日存在倍量柱（vol≥前日×1.9 且收阳）；"
                    "该柱后至今日收盘最低不破其实体下沿×0.99（不穿）；当日收盘创新高 ≥ 该柱最高价"
                    "（突破确认）→ T+1 收盘入场（高量不破/跳空不补未并联）"),
       source="涨停密码·第17章 牛股三绝（简化：倍量不穿+突破倍量柱实顶）")
def _f_lx20_three_absolutes(p: fl.Panel) -> pd.DataFrame:
    vr_prev = _vr_prev(p)
    pillar = (vr_prev >= 1.9) & (p.close > p.open)             # 倍量柱
    body_low = _min_frames(p.open, p.close)
    acc = pd.DataFrame(False, index=p.dates, columns=p.codes)
    for k in range(1, 11):
        pillar_k = pillar.shift(k).fillna(False).astype(bool)
        body_low_k = body_low.shift(k)
        min_close = fl._rolling(p.close, k, min_periods=1).min()   # 该柱后 k 日收盘最低
        nobreak = min_close >= body_low_k * 0.99
        breakout = p.close >= p.high.shift(k)
        acc = acc | (pillar_k & nobreak & breakout)
    valid = vr_prev.notna() & body_low.notna() & p.close.shift(10).notna()
    return fl._event(acc, valid)


# ================================================================ 规格映射
# 17 个 untested 规格 → 因子 / 不可计算原因。key = YAML id。
SPEC_MAP: dict[str, dict] = {
    "LX03_GENERAL_PILLAR": {
        "factor": "lx03_general_pillar", "impl": "new",
        "simplification": "确认日信号（基柱 T-3 + 三日均收低于实顶、守住实底×0.99）；卧底平底/搭梯突破未实现",
    },
    "LX04_MARSHAL_PILLAR_ZERO": {
        "factor": "lx04_marshal_zero", "impl": "new",
        "simplification": "跳空阳柱+三日不破实顶/实底+确认日放量阳线（零号弹起）；回踩深度与先者优先未实现",
    },
    "LX05_GOLDEN_LINE_TRILOGY": {
        "factor": "lx05_golden_line", "impl": "new",
        "simplification": "金线绑定 factor_lib 已确认黄金柱实顶；回踩±1%不破+收阳；黄金双线/十字未实现",
    },
    "LX06_YIN_LINE_TACTICS": {
        "factor": "lx06_long_yin_short", "impl": "new",
        "simplification": "只实现个股长阴短柱（-3%/量比≤0.7）+有底+阳盖阴；大盘阴线择时未实现",
    },
    "LX07_ONE_SWORD_SEAL_THROAT": {
        "factor": None, "impl": None,
        "uncomputable_reason": "C 级画线类：需拟合 AB 封喉斜衡线（最近波峰/凹底大阴实顶三连线）与底部水平平衡线的三角交叉，依赖人工判读，无法用日线阈值稳定程序化",
    },
    "LX09_LIVE_SUFFOCATION": {
        "factor": "lx09_suffocation", "impl": "new",
        "simplification": "大阴框架→20日窄幅（≤20%）缩量箱体（5日均量≤100日均量×0.8）→放量突破箱体上沿；按买点后置（突破日）计",
    },
    "LX10_3121_RULER": {
        "factor": "lx10_ruler3121", "impl": "new",
        "simplification": "缩量31（≤2/3）或缩为百低判假跌；次日阳盖阴且站上前柱 (high+low)/2 的21位",
    },
    "LX11_SPY_TACTICS": {
        "factor": "lx11_spy_small_vol", "impl": "new",
        "simplification": "小倍阳（1.0~1.9倍、收阳）密集≥2根+最近小倍阳实底水平线回踩不破再收阳",
    },
    "LX12_DOUBLE_YIN_EXIT": {
        "factor": "lx12_double_yin_exit", "impl": "new",
        "simplification": "出场/风控信号按反向事件（direction=-1）检验：双阴（含假阳）+跌破前收/左阳实底+下降趋势过滤",
    },
    "LX14_RELAY_DOUBLE_YANG": {
        "factor": "lx14_relay_double_yang", "impl": "new",
        "simplification": "小双阳间隔3~5日（右阳需距今日≥3日以满足“后3日不破”）+实底精准线±1%+突破右阳实顶",
    },
    "LX16_FLAT_SLANT_CROSS": {
        "factor": None, "impl": None,
        "uncomputable_reason": "C 级画线类：平斜交叉需同时拟合水平平衡线与上行/下行斜衡线并定位交点，斜率/交点对参数极敏感，日线代理无稳定口径",
    },
    "LX17_STAND_ON_CONCAVE_PEAK": {
        "factor": "lx17_concave_peak", "impl": "new",
        "simplification": "凹峰线用近20日最高开盘（大阴实顶代理）+≥3个±1%契合点+当日回踩不破收阳",
    },
    "LX19_BULL_STOCK_VOLUME_SHAPE": {
        "factor": "lx19_bull_shape_launch", "impl": "new",
        "simplification": "五要素中可日线化之①百日低量群+③启动柱倍阳过左峰，叠加60日横盘孕育期；②大起大落/⑤黄金十字画线未实现",
    },
    "LX20_BULL_STOCK_THREE_ABSOLUTES": {
        "factor": "lx20_three_absolutes", "impl": "new",
        "simplification": "只实现“倍量不穿+突破倍量柱实顶”作事件；高量不破/跳空不补与持有过滤未并联",
    },
    "LX21_TEN_MONTH_PREGNANCY": {
        "factor": None, "impl": None,
        "uncomputable_reason": "C 级组合框架：本质是“多个已定义基因在≥60日窗口内密集出现≥3个”的框架，需先有其它基因的稳定实现与窗口密度定标，单独无法事件化",
    },
    "LX22_CONCAVE_NOTCH_GOLD": {
        "factor": "aokou", "impl": "existing",
        "simplification": "直接复用 factor_lib 现有 aokou（凹口收口近似：20日高点回落≥8%后放量收阳站上5日线）；凹底隐形黄金柱/凹口平衡线未实现",
    },
    "LX23_FUSE_TACTICS": {
        "factor": None, "impl": None,
        "uncomputable_reason": "C 级概念：导火索=≤13日内多基因连成形态，基因集合与“火药填充/过峰确认”均需先定标，属组合框架",
    },
}


def _factor_meta(name: str) -> dict | None:
    """取因子元信息（含本文件实现的新因子；不依赖全局注册）。"""
    return factor_meta(name)


def build_spec_mapping(specs: list[dict] | None = None,
                       spec_dir: str | Path | None = None) -> dict[str, dict]:
    """17 个 untested 规格 → 映射表（含不可计算原因）；缺映射直接报错（防规格漂移）。"""
    if specs is None:
        specs = load_untested_specs(spec_dir)
    mapping: dict[str, dict] = {}
    missing: list[str] = []
    for spec in specs:
        sid = str(spec.get("id"))
        entry = SPEC_MAP.get(sid)
        if entry is None:
            missing.append(sid)
            continue
        factor = entry.get("factor")
        fmeta = _factor_meta(factor) if factor else None
        if factor and fmeta is None:
            raise KeyError(f"{sid} 映射的因子未注册: {factor}")
        mapping[sid] = {
            "spec_id": sid,
            "name": spec.get("name"),
            "type": spec.get("type"),
            "computability": spec.get("computability"),
            "source": spec.get("source"),
            "chapter": spec.get("chapter"),
            "entry_rule": spec.get("entry_rule"),
            "test_status": spec.get("test_status"),
            "factor": factor,
            "factor_kind": (fmeta or {}).get("kind"),
            "factor_direction": (fmeta or {}).get("direction"),
            "factor_family": (fmeta or {}).get("family"),
            "factor_source": (fmeta or {}).get("source"),
            "impl": entry.get("impl"),
            "simplification": entry.get("simplification"),
            "uncomputable_reason": entry.get("uncomputable_reason"),
        }
    if missing:
        raise ValueError(f"SPEC_MAP 缺少 untested 规格映射: {missing}（规格库可能已变更）")
    return mapping


def load_untested_specs(spec_dir: str | Path | None = None) -> list[dict]:
    """加载全部规格并筛 test_status=untested。"""
    specs = strategy_specs.load_specs(spec_dir or SPEC_DIR)
    return [s for s in specs if s.get("test_status") == "untested"]


def load_all_specs(spec_dir: str | Path | None = None) -> list[dict]:
    return strategy_specs.load_specs(spec_dir or SPEC_DIR)


# ================================================================ 统计聚合
def _mean_t(values) -> dict:
    """均值/t/胜率（日频序列；NaN 自动剔除）。"""
    s = pd.Series(np.asarray(values, dtype=float)).dropna()
    n = int(len(s))
    if n == 0:
        return {"n_days": 0, "mean_pct": None, "t": None, "positive_rate": None}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    t = mean / std * np.sqrt(n) if std > 0 else None
    return {"n_days": n,
            "mean_pct": round(mean * 100.0, 4),
            "t": round(float(t), 3) if t is not None else None,
            "positive_rate": round(float((s > 0).mean()), 4)}


def _daily_mean(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """按日（axis=0）计算 mask 内等权均值；无样本日 NaN。"""
    cnt = mask.sum(axis=1).astype(float)
    tot = np.nansum(np.where(mask, values, 0.0), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(cnt > 0, tot / np.where(cnt > 0, cnt, 1.0), np.nan)
    return out


def _event_excess_stats(car_w: np.ndarray, f_w: np.ndarray, elig_w: np.ndarray,
                        dates_w: list[str], split_date: str | None, direction: int,
                        n_boot: int = DEFAULT_N_BOOT, seed: int = SEED) -> dict:
    """事件因子「信号组 − 非信号组」按日聚合超额（已乘先验方向）。

    返回结构：
      {n_events, n_signal_days, avg_signals_per_day, signal_share_pct,
       overall: {excess_pct, t, n_days, p_le_0}, IS: {...}, OOS: {...}}
    其中 excess_pct/t 均为 **directed**（× direction）：>0 表示与规格先验方向一致。
    非事件因子返回 None（本批 13 项全为事件因子）。
    """
    pair = elig_w & np.isfinite(f_w) & np.isfinite(car_w)
    sig = pair & (f_w == 1.0)
    non = pair & (f_w == 0.0)
    if not sig.any() or not non.any():
        return None
    daily_sig = _daily_mean(car_w, sig)
    daily_non = _daily_mean(car_w, non)
    excess = (daily_sig - daily_non) * float(direction)        # 有向超额（先验方向）
    is_mask = (np.asarray([d <= split_date for d in dates_w])
               if split_date is not None else np.zeros(len(dates_w), dtype=bool))
    n_days = max(1, len(dates_w))
    out: dict = {
        "n_events": int(sig.sum()),
        "n_signal_days": int((sig.sum(axis=1) > 0).sum()),
        "avg_signals_per_day": round(float(sig.sum()) / n_days, 2),
        "signal_share_pct": round(100.0 * float(sig.sum()) / max(1, int(pair.sum())), 2),
        "signal_daily_car_pct": _mean_t(daily_sig)["mean_pct"],
        "non_signal_daily_car_pct": _mean_t(daily_non)["mean_pct"],
        "overall": _mean_t(excess),
        "IS": dict(_mean_t(excess[is_mask]), n_events=int(sig[is_mask].sum())),
        "OOS": dict(_mean_t(excess[~is_mask]), n_events=int(sig[~is_mask].sum())),
    }
    # OOS 有向超额的 block bootstrap p（块长 5 ≥ 20 日持有期）
    oos_series = pd.Series(excess[~is_mask], dtype=float)
    if int(oos_series.dropna().shape[0]) >= BLOCK_MIN_LEN:
        boot = stat_utils.block_bootstrap_p(oos_series, block=BLOCK, n_boot=n_boot, seed=seed)
        out["OOS"]["p_le_0"] = boot.get("p_le_0")
    else:
        out["OOS"]["p_le_0"] = None
    out["overall"]["p_le_0"] = None
    if int(pd.Series(excess, dtype=float).dropna().shape[0]) >= BLOCK_MIN_LEN:
        boot_all = stat_utils.block_bootstrap_p(pd.Series(excess, dtype=float),
                                                block=BLOCK, n_boot=n_boot, seed=seed)
        out["overall"]["p_le_0"] = boot_all.get("p_le_0")
    return out


def _empty_rich_fields() -> dict:
    """F 级结果的占位字段（schema 统一：IS/OOS 的 IC 口径与事件超额口径分列）。"""
    blank = {"ic_days": 0, "ic_mean": None, "ic_t": None, "ic_positive_rate": None,
             "excess_days": 0, "excess_pct": None, "excess_t": None,
             "excess_positive_rate": None, "excess_p_le_0": None,
             "n_events": 0, "n_signal_days": 0}
    return {
        "ran": False,
        "n_events": 0, "n_signal_days": 0, "avg_signals_per_day": 0.0,
        "signal_share_pct": None,
        "overall": {"ic_directed_mean": None, "ic_t": None,
                    "excess_pct": None, "excess_t": None, "excess_p_le_0": None},
        "is": dict(blank), "oos": dict(blank),
        "by_year_ic_directed": {},
        "bootstrap_ic_p_le_0": None,
    }


def _extract_eval_metrics(ev: dict) -> dict:
    """从 factor_lib 单因子评测结果抽取本报告需要的字段（IC 口径）。"""
    ic_dir = ev.get("ic_directed") or {}
    is_oos = ev.get("is_oos") or {}
    is_ = is_oos.get("IS") or {}
    oos = is_oos.get("OOS") or {}
    by_year = {}
    for year, stat in sorted((ev.get("by_year") or {}).items()):
        if not isinstance(stat, dict) or not stat.get("n_days"):
            continue
        by_year[year] = {"n_days": stat.get("n_days"),
                         "mean": stat.get("mean"), "t": stat.get("t")}
    boot = ((ev.get("bootstrap") or {}).get("ic_directed") or {})
    return {
        "n_obs": ev.get("n_obs"),
        "n_days": ev.get("n_days"),
        "ic_directed_mean": ic_dir.get("mean"),
        "ic_t": ic_dir.get("t"),
        "is": {"ic_days": is_.get("n_days"), "ic_mean": is_.get("mean"),
               "ic_t": is_.get("t"), "ic_positive_rate": is_.get("positive_rate")},
        "oos": {"ic_days": oos.get("n_days"), "ic_mean": oos.get("mean"),
                "ic_t": oos.get("t"), "ic_positive_rate": oos.get("positive_rate")},
        "by_year_ic_directed": by_year,
        "bootstrap_ic_p_le_0": boot.get("p_le_0"),
    }


def grade_entry(res: dict) -> tuple[str, str]:
    """按 A/B/C/D/F 口径给单战法评级，返回 (grade, 中文理由)。"""
    if res.get("factor") is None:
        return "F", res.get("uncomputable_reason") or "不可计算（computability=C，本批未实现）"
    if not res.get("ran"):
        return "F", res.get("reason") or "未运行（数据不足/面板为空）"
    n_events = int(res.get("n_events") or 0)
    oos = res.get("oos") or {}
    is_ = res.get("is") or {}
    n_oos_days = int(oos.get("ic_days") or 0)
    n_oos_events = int(oos.get("n_events") or 0)
    t_oos = oos.get("ic_t")
    t_is = is_.get("ic_t")
    exc = oos.get("excess_pct")
    exc_t = oos.get("excess_t")
    p_oos = oos.get("excess_p_le_0")
    p_ic = res.get("bootstrap_ic_p_le_0")
    p_used = p_oos if p_oos is not None else p_ic
    if n_events < MIN_EVENTS or n_oos_days < MIN_OOS_DAYS or n_oos_events < MIN_OOS_EVENTS:
        return "F", (f"数据不足：全窗口事件 {n_events}、OOS 有效 IC 日 {n_oos_days}、"
                     f"OOS 事件 {n_oos_events}（阈值 {MIN_EVENTS}/{MIN_OOS_DAYS}/{MIN_OOS_EVENTS}）")
    if t_oos is None:
        return "F", "数据不足：OOS directed IC t 值不可算"
    if (t_oos >= A_T and p_used is not None and p_used < 0.05
            and exc is not None and exc > 0
            and (exc_t is None or exc_t > 0)
            and (t_is is None or t_is > -1.0)):
        return "A", (f"OOS 同向显著：OOS IC t={t_oos:.2f}、OOS 有向超额={exc:+.3f}%/次"
                     f"（t={exc_t}）、bootstrap p={p_used:.3f}（OOS 超额，块长 {BLOCK}）；IS IC t={t_is}")
    if t_oos <= D_T or (t_oos <= D_WEAK_T and exc is not None and exc < 0):
        return "D", (f"反向：OOS IC t={t_oos:.2f}（与先验方向相反）、OOS 有向超额={exc}%"
                     f"（t={exc_t}）、bootstrap p={p_oos}；IS IC t={t_is}")
    if t_oos > 0 and (exc is None or exc >= 0):
        return "B", (f"方向对但不显著：OOS IC t={t_oos:.2f}（<{A_T}）、OOS 有向超额={exc}%"
                     f"（t={exc_t}）、bootstrap p={p_used}；IS IC t={t_is}")
    return "C", (f"不显著：OOS IC t={t_oos:.2f}、OOS 有向超额={exc}%（t={exc_t}）、"
                 f"bootstrap p={p_used}，方向证据不足")


def _priority_track(res: dict) -> str:
    """优先深测轨道：forward=A/B 正向候选；unclear=C 方向未定；reverse=D 反向/规避线索。"""
    grade = res.get("grade")
    if grade in ("A", "B"):
        return "forward"
    if grade == "C":
        return "unclear"
    if grade == "D":
        return "reverse"
    return "unusable"


PRIORITY_TRACK_ORDER = {"forward": 0, "unclear": 1, "reverse": 2, "unusable": 9}
TRACK_LABEL = {"forward": "正向候选（A/B）",
               "unclear": "方向未定（C，IS→OOS 翻转/混合）",
               "reverse": "反向/规避线索（D）",
               "unusable": "不可用（F）"}


def _priority_strength(res: dict) -> float:
    """证据强度（同轨道内排序用）：正向看 OOS t+0.5×超额 t；反向/未定看 |t| 与 |超额|。"""
    oos = res.get("oos") or {}
    is_ = res.get("is") or {}
    t = float(oos.get("ic_t") or 0.0)
    exc_t = float(oos.get("excess_t") or 0.0)
    exc = float(oos.get("excess_pct") or 0.0)
    track = _priority_track(res)
    if track == "forward":
        return round(t + 0.5 * exc_t, 4)
    if track == "unclear":
        # 重点看 IS 正向证据 + OOS 翻转幅度（MEETING 第五类陷阱）
        return round(max(0.0, float(is_.get("ic_t") or 0.0)) + abs(t) + 0.5 * abs(exc_t), 4)
    return round(abs(t) + 0.5 * abs(exc_t) + 0.1 * abs(exc), 4)


def build_priority(results: list[dict], top_n: int = 5) -> list[dict]:
    """按证据强度排序的「建议优先深测」清单（排除 F；先 C 后 D，再按强度）。"""
    cand = [r for r in results if r.get("grade") != "F"]
    ordered = sorted(cand, key=lambda r: (PRIORITY_TRACK_ORDER[_priority_track(r)],
                                          -_priority_strength(r)))
    actions = {
        "forward": "优先深测：补 walk-forward 年度序列、参数敏感性、成本后与容量四关",
        "unclear": "深测前先做 walk-forward 年度序列 + 参数敏感性，解释 IS→OOS 方向翻转"
                   "（MEETING 第五类陷阱），确认非前视/过拟合后再谈方向",
        "reverse": "按反向/规避口径深测：验证反向信号（做空/规避/出场）的可交易性、换手与容量；"
                   "不得按原书看涨口径复用",
    }
    out = []
    for i, r in enumerate(ordered[:top_n], 1):
        oos = r.get("oos") or {}
        is_ = r.get("is") or {}
        track = _priority_track(r)
        out.append({
            "rank": i,
            "track": track,
            "track_label": TRACK_LABEL[track],
            "spec_id": r.get("spec_id"),
            "name": r.get("name"),
            "grade": r.get("grade"),
            "score": _priority_strength(r),
            "key_metrics": {
                "n_events": r.get("n_events"),
                "signal_share_pct": r.get("signal_share_pct"),
                "is_ic_t": is_.get("ic_t"),
                "oos_ic_t": oos.get("ic_t"),
                "oos_excess_pct": oos.get("excess_pct"),
                "oos_excess_t": oos.get("excess_t"),
                "oos_excess_p_le_0": oos.get("excess_p_le_0"),
                "ic_p_le_0": r.get("bootstrap_ic_p_le_0"),
            },
            "why": (f"IS IC t={_fmt(is_.get('ic_t'))} → OOS IC t={_fmt(oos.get('ic_t'))}；"
                    f"OOS 有向超额={_fmt(oos.get('excess_pct'))}%（t={_fmt(oos.get('excess_t'))}）、"
                    f"OOS 超额 p={_fmt(oos.get('excess_p_le_0'), 3)}；事件占比 {_fmt(r.get('signal_share_pct'), 1)}%"),
            "action": actions.get(track, "待定"),
        })
    return out


# ================================================================ 批量运行
def _resolve_positions(panel: fl.Panel, start: str, end: str,
                       horizon: int = HORIZON) -> dict | None:
    """复用 factor_lib 的窗口解析（保证与本文件超额统计、factor_lib 评测同一信号窗）。"""
    return fl._resolve_window(panel.dates, start, end, horizon)


def _run_one_spec(panel: fl.Panel, spec: dict, map_entry: dict, eval_entry: dict | None,
                  window: dict, car_w: np.ndarray, elig_w: np.ndarray, dates_w: list[str],
                  split_date: str | None, n_boot: int, seed: int) -> dict:
    """单个战法：合并 factor_lib IC 指标 + 本地事件超额统计 + 评级。"""
    sid = map_entry["spec_id"]
    factor = map_entry.get("factor")
    res = {
        "spec_id": sid,
        "name": map_entry.get("name"),
        "type": map_entry.get("type"),
        "computability": map_entry.get("computability"),
        "source": map_entry.get("source"),
        "chapter": map_entry.get("chapter"),
        "factor": factor,
        "factor_kind": map_entry.get("factor_kind"),
        "factor_direction": map_entry.get("factor_direction"),
        "impl": map_entry.get("impl"),
        "simplification": map_entry.get("simplification"),
        "uncomputable_reason": map_entry.get("uncomputable_reason"),
    }
    res.update(_empty_rich_fields())
    if factor is None:
        res["grade"], res["grade_reason"] = grade_entry(res)
        return res
    if eval_entry is None:
        res["reason"] = "factor_lib 评测缺失（数据不足或评测失败）"
        res["grade"], res["grade_reason"] = grade_entry(res)
        return res

    metrics = _extract_eval_metrics(eval_entry)
    res.update({k: v for k, v in metrics.items() if k not in ("is", "oos")})
    res["is"].update(metrics["is"])
    res["oos"].update(metrics["oos"])

    try:
        fmat = compute_factor(factor, panel)
        f_w = np.asarray(fmat.iloc[window["start_i"]:window["end_i"] + 1], dtype=float)
        excess = _event_excess_stats(car_w, f_w, elig_w, dates_w, split_date,
                                     int(map_entry.get("factor_direction") or 1),
                                     n_boot=n_boot, seed=seed)
    except Exception as exc:  # 单战法失败不阻断整批（报告里标 F）
        res["reason"] = f"事件超额统计失败: {exc}"
        res["grade"], res["grade_reason"] = grade_entry(res)
        return res

    if excess is None:
        res["reason"] = "信号组/非信号组为空（事件不可算）"
        res["grade"], res["grade_reason"] = grade_entry(res)
        return res

    res["ran"] = True
    res["n_events"] = excess["n_events"]
    res["n_signal_days"] = excess["n_signal_days"]
    res["avg_signals_per_day"] = excess["avg_signals_per_day"]
    res["signal_share_pct"] = excess["signal_share_pct"]
    res["signal_daily_car_pct"] = excess["signal_daily_car_pct"]
    res["non_signal_daily_car_pct"] = excess["non_signal_daily_car_pct"]
    res["overall"]["excess_pct"] = excess["overall"].get("mean_pct")
    res["overall"]["excess_t"] = excess["overall"].get("t")
    res["overall"]["excess_p_le_0"] = excess["overall"].get("p_le_0")
    for key in ("is", "oos"):
        src = excess["IS"] if key == "is" else excess["OOS"]
        res[key]["excess_days"] = src.get("n_days")
        res[key]["excess_pct"] = src.get("mean_pct")
        res[key]["excess_t"] = src.get("t")
        res[key]["excess_positive_rate"] = src.get("positive_rate")
        res[key]["excess_p_le_0"] = src.get("p_le_0")
        res[key]["n_events"] = src.get("n_events")
    res["grade"], res["grade_reason"] = grade_entry(res)
    return res


def run_batch(start: str = DEFAULT_START, end: str = DEFAULT_END,
              is_end: str | None = DEFAULT_IS_END, panel: fl.Panel | None = None,
              spec_dir: str | Path | None = None, universe: str = "tradable",
              horizon: int = HORIZON, cost: float = COST,
              n_boot: int = DEFAULT_N_BOOT, min_cross_section: int = MIN_CROSS_SECTION,
              seed: int = SEED, data_root: str | Path | None = None) -> dict:
    """批量四关初筛主流程；返回可直接落盘的 JSON dict。

    panel 传入时不做数据 IO（单测/复现用）；否则用 factor_lib.load_panel 载入真实行情。
    """
    t0 = time.time()
    all_specs = load_all_specs(spec_dir)
    specs = [s for s in all_specs if s.get("test_status") == "untested"]
    mapping = build_spec_mapping(specs)
    factor_names = sorted({m["factor"] for m in mapping.values() if m["factor"]})

    # ---- 数据面板 ----
    load_sec = 0.0
    if panel is None:
        t_load = time.time()
        panel = fl.load_panel(start, end, horizon=horizon, universe=universe,
                              data_root=data_root, load_roe=False)
        load_sec = round(time.time() - t_load, 2)

    # ---- factor_lib 统一评测（IC / IS-OOS / 分年 / bootstrap） ----
    # 新因子以「作用域内临时注册」方式复用 factor_lib 评测，退出即还原全局注册表（不污染其它单测）。
    with _scoped_registry():
        report = fl.evaluate_panel(panel, factor_names, horizon=horizon, cost=cost,
                                   start=start, end=end, is_end=is_end, seed=seed,
                                   min_cross_section=min_cross_section, n_boot=n_boot,
                                   universe=universe)
    eval_meta = report.get("meta") or {}
    window = _resolve_positions(panel, start, end, horizon)

    # ---- 逐战法聚合（事件超额 + 评级） ----
    results: list[dict] = []
    if window is None or panel.shape[0] == 0:
        reason = "面板为空或区间内无法计算满持有期收益"
        for spec in specs:
            sid = str(spec.get("id"))
            map_entry = mapping[sid]
            res = {
                "spec_id": sid, "name": map_entry.get("name"), "type": map_entry.get("type"),
                "computability": map_entry.get("computability"),
                "source": map_entry.get("source"), "chapter": map_entry.get("chapter"),
                "factor": map_entry.get("factor"), "factor_kind": map_entry.get("factor_kind"),
                "factor_direction": map_entry.get("factor_direction"),
                "impl": map_entry.get("impl"), "simplification": map_entry.get("simplification"),
                "uncomputable_reason": map_entry.get("uncomputable_reason"),
                "reason": reason,
            }
            res.update(_empty_rich_fields())
            res["grade"], res["grade_reason"] = grade_entry(res)
            results.append(res)
    else:
        car_w, elig_w, dates_w = _window_arrays(panel, window, horizon, cost)
        split_date = eval_meta.get("is_end") or is_end
        for spec in specs:
            sid = str(spec.get("id"))
            map_entry = mapping[sid]
            eval_entry = None
            if map_entry.get("factor"):
                eval_entry = (report.get("factors") or {}).get(map_entry["factor"])
            results.append(_run_one_spec(panel, spec, map_entry, eval_entry, window,
                                         car_w, elig_w, dates_w, split_date, n_boot, seed))

    # ---- 汇总 ----
    grade_counts = {g: 0 for g in GRADES}
    for res in results:
        grade_counts[res["grade"]] = grade_counts.get(res["grade"], 0) + 1
    priority = build_priority(results, top_n=5)
    n_uncomputable = sum(1 for r in results if r.get("factor") is None)
    n_testable = len(results) - n_uncomputable
    runtime = round(time.time() - t0, 2)

    meta = {
        "report": "book_batch_test",
        "version": BATCH_VERSION,
        "task": "支线 D：17 个未测量学战法批量四关初筛",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec": {
            "spec_dir": str(SPEC_DIR.relative_to(REPO)).replace("\\", "/"),
            "specs_total": len(all_specs),
            "untested": len(specs),
            "testable_ab": n_testable,
            "uncomputable_c": n_uncomputable,
            "tested_rejected": sum(1 for s in all_specs
                                   if s.get("test_status") == "tested_rejected"),
            "concept": sum(1 for s in all_specs if s.get("test_status") == "concept"),
        },
        "eval": {
            "lib": "tools/research/factor_lib.py",
            "lib_version": fl.LIB_VERSION,
            "requested_start": start,
            "requested_end": end,
            "effective_start": (eval_meta.get("effective") or {}).get("start"),
            "effective_end": (eval_meta.get("effective") or {}).get("end"),
            "signal_days": (eval_meta.get("effective") or {}).get("signal_days"),
            "is_end": is_end,
            "is_end_source": eval_meta.get("is_end_source"),
            "horizon_days": horizon,
            "entry": "T 日收盘信号 → T+1 收盘入场 → T+21 收盘卖出",
            "car": "个股复权买入持有收益 − 等权市场（日频再平衡）复利收益 − 单边 30bp",
            "cost_bp_oneway": int(round(cost * 10000)),
            "market": eval_meta.get("market"),
            "universe": universe,
            "min_cross_section": min_cross_section,
            "block_bootstrap": {"block": BLOCK, "n_boot": n_boot, "seed": seed},
            "grade_thresholds": {"A_t": A_T, "D_t": D_T, "D_weak_t": D_WEAK_T,
                                 "min_events": MIN_EVENTS, "min_oos_days": MIN_OOS_DAYS,
                                 "min_oos_events": MIN_OOS_EVENTS},
        },
        "data": eval_meta.get("data"),
        "baseline": eval_meta.get("baseline"),
        "grade_counts": grade_counts,
        "priority_top5": [p["spec_id"] for p in priority],
        "runtime_sec": runtime,
        "load_sec": load_sec,
        "notes": [
            "口径与 factor_lib v1.0.0 一致：T+1 收盘入场、20 日 CAR 相对等权市场、单边 30bp、按日聚合。",
            "事件因子采用「信号组 − 非信号组」按日聚合的有向超额；t 基于日频序列，已缓解同日多事件重叠但不消除横截面相关。",
            "OOS = 2025-01-01 之后的信号日；IS = 2024-12-31 及以前；block bootstrap 块长 5（≥ 20 日持有期重叠修正的最低要求）。",
            "评级 A 的 p 值优先用 OOS 有向超额日频序列的 block bootstrap（p_le_0），样本不足时回退到全局 directed IC 的 block bootstrap；OOS 正向显著用 OOS IC t 衡量。",
            "本报告是「初筛」而非 validated：未做容量/换手/涨跌停可交易性与 walk-forward 年度命中率，A/B 档只代表值得深测。",
            "C 级（画线/概念）4 项未实现，grade=F 并写明原因；B 级规格的简化映射已在 simplification 字段披露。",
        ],
    }
    return {"meta": meta, "mapping": mapping, "results": results,
            "priority": priority, "grade_counts": grade_counts}


def _window_arrays(panel: fl.Panel, window: dict, horizon: int, cost: float):
    """取信号窗内的 CAR / 可交易掩码 / 日期（与 factor_lib 评测同窗口）。"""
    car = fl.forward_car(panel, horizon=horizon, cost=cost)
    pos = np.arange(window["start_i"], window["end_i"] + 1)
    car_w = np.asarray(car.iloc[pos].to_numpy(dtype=float))
    elig_w = np.asarray(panel.eligible_mask().iloc[pos].to_numpy(dtype=bool))
    dates_w = [str(d) for d in panel.dates[pos]]
    return car_w, elig_w, dates_w


# ================================================================ 报告渲染
def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value}{suffix}"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(f):
        return "-"
    return f"{f:+.{digits}f}{suffix}" if suffix else f"{f:.{digits}f}"


def render_markdown(payload: dict) -> str:
    """把 run_batch 结果渲染为 Markdown 报告（汇总表 + 结论 + 优先清单 + 口径局限）。"""
    meta = payload.get("meta") or {}
    results = payload.get("results") or []
    priority = payload.get("priority") or []
    counts = payload.get("grade_counts") or {}
    ev = meta.get("eval") or {}
    spec = meta.get("spec") or {}
    base = meta.get("baseline") or {}
    lines: list[str] = []
    add = lines.append

    add("# 量学战法批量四关初筛 · 17 个未测战法（2026-11-01，支线 D）")
    add("")
    add(f"- 生成时间：{meta.get('generated_at')}")
    add(f"- 规格库：`{spec.get('spec_dir')}` · 共 {spec.get('specs_total')} 项 = "
        f"untested {spec.get('untested')} / tested_rejected {spec.get('tested_rejected')} / "
        f"concept {spec.get('concept')}；本批可计算（A/B）{spec.get('testable_ab')} 项，"
        f"C 级不可计算 {spec.get('uncomputable_c')} 项")
    add(f"- 数据窗口：请求 {ev.get('requested_start')}~{ev.get('requested_end')}；"
        f"**有效信号窗 {ev.get('effective_start')}~{ev.get('effective_end')}**"
        f"（{ev.get('signal_days')} 个交易日，末端按 20 日持有期裁剪）")
    add(f"- 评测口径：factor_lib v{ev.get('lib_version')} · {ev.get('entry')} · "
        f"20 日 CAR = {ev.get('car')} · IS ≤ {ev.get('is_end')} / OOS ≥ 次年首个交易日 · "
        f"block bootstrap 块长 {ev.get('block_bootstrap', {}).get('block')}"
        f"（{ev.get('block_bootstrap', {}).get('n_boot')} 次，seed={ev.get('block_bootstrap', {}).get('seed')}）")
    add(f"- 股票池：{ev.get('universe')}（价 ≥ 2 元、成交额 ≥ 5000 万、上市 ≥ 250 日，均 T 日可知）"
        f" · 无条件 CAR 基线 {base.get('mean_pct')}%（n={base.get('n_obs')}）")
    add(f"- 评级：A=OOS 同向显著（OOS IC t≥{A_T}、OOS 有向超额>0 且 block bootstrap p<0.05）/ "
        f"B=方向对但不显著 / C=不显著 / D=反向（t≤{D_T}，或 t≤{D_WEAK_T} 且超额为负）/ "
        f"F=不可计算或数据不足")
    add("- 超额口径：事件组「信号组 − 非信号组」按日等权聚合的 20 日 CAR 差，已乘先验方向"
        "（direction=-1 的双阴出货已反向）；OOS 超额 p = 该日频序列的 block bootstrap（块长 "
        f"{ev.get('block_bootstrap', {}).get('block')}）P(均值≤0)。")
    add("")

    # ---- 汇总表 ----
    add("## 一、汇总表（17 项 untested）")
    add("")
    add("| # | 规格 ID | 战法 | 类型 | 可计算化 | 映射因子 / 不可计算说明 | 事件数 | OOS IC_t | "
        "OOS 有向超额% | OOS 超额 t | OOS 超额 p | IC p（全窗口）| grade |")
    add("|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|")
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    ordered = sorted(results, key=lambda r: (grade_order.get(r.get("grade"), 9),
                                             str(r.get("spec_id"))))
    for i, r in enumerate(ordered, 1):
        oos = r.get("oos") or {}
        factor = r.get("factor") or "-"
        note = r.get("simplification") if r.get("factor") else (
            r.get("uncomputable_reason") or "")
        note = (note or "")[:46]
        if r.get("factor"):
            mapped = f"`{factor}`"
        else:
            mapped = f"不可计算：{note}"
        add(f"| {i} | `{r.get('spec_id')}` | {r.get('name')} | {r.get('type')} | "
            f"{r.get('computability')} | {mapped} | {r.get('n_events') or 0} | "
            f"{_fmt(oos.get('ic_t'))} | {_fmt(oos.get('excess_pct'))} | "
            f"{_fmt(oos.get('excess_t'))} | {_fmt(oos.get('excess_p_le_0'), 3)} | "
            f"{_fmt(r.get('bootstrap_ic_p_le_0'), 3)} | **{r.get('grade')}** |")
    add("")

    # ---- grade 分布 + 核心结论 ----
    add("## 二、结论（grade 分布与逐项判读）")
    add("")
    add(f"- **A（OOS 同向显著，建议深测）**：{counts.get('A', 0)} 项")
    add(f"- **B（方向对但不显著）**：{counts.get('B', 0)} 项")
    add(f"- **C（不显著/方向未定）**：{counts.get('C', 0)} 项")
    add(f"- **D（反向）**：{counts.get('D', 0)} 项")
    add(f"- **F（不可计算/数据不足）**：{counts.get('F', 0)} 项")
    add("")
    d_list = [r for r in results if r.get("grade") == "D"]
    c_list = [r for r in results if r.get("grade") == "C"]
    d_both = [r for r in d_list
              if (r.get("oos") or {}).get("ic_t") is not None
              and float((r.get("oos") or {}).get("ic_t")) < 0
              and float((r.get("oos") or {}).get("excess_pct") or 0) < 0]
    c_pos_is = [r for r in c_list
                if float(((r.get("is") or {}).get("ic_t")) or 0) > 1.5]
    add("### 核心结论")
    if counts.get("A", 0) + counts.get("B", 0) == 0:
        add(f"- **正向结论为零**：{spec.get('testable_ab')} 个可计算战法在 OOS（2025-01 起）上"
            "**无一达到正向显著门槛**（A=0、B=0），量学单形态看涨信号在全市场等权 20 日 CAR 口径下"
            "没有可复用的正向 alpha。")
    if d_list:
        add(f"- **系统性反向**：D 档 {len(d_list)} 项；其中 {len(d_both)} 项 OOS 的 directed IC t<0 且"
            "有向超额同时为负（双向一致反向），说明这些「看涨形态」在本样本更像反向/规避信号，"
            "与已测 H24–H29（倍量柱/价升量缩/极阴次阳/兵临城下等均 rejected）方向一致。")
    if c_pos_is:
        desc = "；".join(
            f"{r.get('spec_id')} IS IC t={_fmt((r.get('is') or {}).get('ic_t'))} → "
            f"OOS IC t={_fmt((r.get('oos') or {}).get('ic_t'))}、"
            f"OOS 有向超额 {_fmt((r.get('oos') or {}).get('excess_pct'))}%"
            f"（t={_fmt((r.get('oos') or {}).get('excess_t'))}）"
            for r in c_pos_is)
        add(f"- **IS→OOS 衰减/翻转**：C 档 {len(c_pos_is)} 项样本内 directed IC 显著为正，但样本外"
            f"衰减或翻转（{desc}）——典型 regime 依赖/过拟合风险（MEETING.md 第五类陷阱），"
            "只能作「待补证据」不能作正向候选。")
    add(f"- **反向强度排序**（OOS IC t）："
        + "、".join(f"{r.get('spec_id')} {_fmt((r.get('oos') or {}).get('ic_t'))}"
                   for r in sorted(d_list, key=lambda x: float(((x.get("oos") or {}).get("ic_t")) or 0))[:5])
        + "；D 档只作反向/规避线索，不得按原书看涨口径复用。")
    add("")
    for grade in GRADES:
        group = [r for r in results if r.get("grade") == grade]
        if not group:
            continue
        add(f"### {grade} 组 · {GRADE_LABEL[grade]}")
        for r in group:
            is_, oos = r.get("is") or {}, r.get("oos") or {}
            if r.get("factor") is None:
                add(f"- **{r.get('spec_id')} {r.get('name')}**（C 级不可计算）："
                    f"{r.get('uncomputable_reason')}")
                continue
            add(f"- **{r.get('spec_id')} {r.get('name')}**（因子 `{r.get('factor')}`，"
                f"方向 {r.get('factor_direction'):+d}，事件 {r.get('n_events')}，占比 "
                f"{_fmt(r.get('signal_share_pct'), 1)}%）：IS IC t={_fmt(is_.get('ic_t'))} → "
                f"OOS IC t={_fmt(oos.get('ic_t'))}；OOS 有向超额={_fmt(oos.get('excess_pct'))}%"
                f"（t={_fmt(oos.get('excess_t'))}，p={_fmt(oos.get('excess_p_le_0'), 3)}）。"
                f"{r.get('grade_reason')}")
            if grade in ("A", "B") and r.get("simplification"):
                add(f"  - 简化口径：{r.get('simplification')}")
        add("")
    add("> 判读纪律：本批只做「单形态事件 → 20 日 CAR」的四关初筛，A/B 只代表值得进入深测；"
        "不得据此把任何战法升级为 validated（需 OOS + 成本后为正 + 容量，见 MEETING.md）。")
    add("")

    # ---- 优先深测 ----
    add("## 三、建议优先深测 Top5（按证据强度，分轨道）")
    add("")
    forward_n = sum(1 for p in priority if p.get("track") == "forward")
    if forward_n == 0:
        add("> 本批无 A/B 正向候选（forward 轨道为空）。以下 Top5 由「C 档待补证据」与"
            "「D 档最强反向线索」两条轨道组成：C 档需先解释 IS→OOS 翻转，D 档需按反向/规避口径重测。")
        add("")
    if priority:
        add("| 排名 | 轨道 | 规格 | 战法 | grade | 评分 | 关键证据（IS→OOS） | 建议 |")
        add("|---:|---|---|---|---|---:|---|---|")
        for p in priority:
            add(f"| {p.get('rank')} | {p.get('track_label')} | `{p.get('spec_id')}` | "
                f"{p.get('name')} | {p.get('grade')} | {_fmt(p.get('score'), 2)} | "
                f"{p.get('why')} | {p.get('action')} |")
    else:
        add("（无 A/B/C/D 档可排序项）")
    add("")
    add("> 说明：排序先按轨道（A/B > C > D），轨道内正向看 OOS IC t+0.5×超额 t，"
        "反向/未定看 |t| 与 |超额|；仅为深测排序，不构成收益预期。")
    add("")

    # ---- 口径与局限 ----
    add("## 四、口径与局限")
    add("")
    add(f"1. **可计算化边界**：17 项 untested 中 A/B 级 {spec.get('testable_ab')} 项已映射实现；"
        f"C 级 {spec.get('uncomputable_c')} 项（一剑封喉/平斜交叉/十月怀胎/量学导火索）依赖画线算法、"
        "基因窗口密度或人工判读，本批标 F 并写明原因，不作数据结论。")
    add("2. **B 级简化**：原书的画线（平衡线/金线/凹峰线）、多柱确认（王牌柱→黄金十字）、组合框架被"
        "降维为单形态事件代理；simplification 字段逐项披露。初筛只能否证「单形态事件本身」，"
        "不能否定完整战法（组合/画线条件可能改变结果）。")
    add("3. **事件数 ≠ 独立样本**：同一天多只股票同时触发、同一股票信号在持有期内重叠；本报告超额按日聚合"
        f"（每日等权一个观测）+ block bootstrap（块长 {ev.get('block_bootstrap', {}).get('block')}），"
        "已缓解但未消除横截面相关，t 值仍偏乐观，故 A/B 只作初筛线索。")
    add("4. **静态单一切分**：IS/OOS 固定切分可能掩盖 regime（MEETING.md 第五类陷阱），本批 C 档"
        "LX05/LX17 即出现 IS 显著为正、OOS 转负；A/B 项深测时必须补 walk-forward 年度序列与命中率"
        "（本报告已附分年 IC，但未做逐年 walk-forward 命中率）。")
    add("5. **四关未全过**：本批覆盖「时点可知（PIT，静态+截断不变性测试）+ 样本外（OOS）+ 成本后（30bp）」，"
        "**未做容量/换手/涨跌停与停牌可交易性评估**；等权市场为日频再平衡，与个股买入持有存在再平衡差"
        "（见 factor_lib baseline 说明）。")
    add("6. **OOS 长度**：OOS 仅 2025-01 ~ 2026-09（约 21 个月），存在 regime/季节性风险；"
        "事件稀薄战法的 OOS 有效日可能接近阈值。")
    add("7. **高事件占比的近似形态**：本批 4 项事件占比 >10%（LX11 小倍阳回踩、LX12 双阴、"
        "LX20 倍量不穿、LX22 凹口收口），事件组已接近半个市场，横截面区分度低；其「反向」更可能"
        "混入 A 股短期反转效应与阈值宽松，不能直接解释为战法专属 alpha 或专属反向信号。"
        "LX12 本质是出场/风控信号（非做多入场），OOS 有向超额≈0、D 档主要由 IC 侧驱动，"
        "不适合当反向 alpha 深测。")
    add("8. **数据边界**：tushare 日线数据窗口 1991~2026-10-30；股票池用 T 日可知的价格/成交额/上市日期过滤，"
        "不含当前 ST 名称等后验信息；未单独处理退市样本的幸存者偏差（因子库口径）。")
    add("")
    add("## 五、复现")
    add("")
    add("```powershell")
    add("C:\\项目\\py312\\python.exe -X utf8 tools/research/book_batch_test.py --list   # 17 项映射")
    add(f"C:\\项目\\py312\\python.exe -X utf8 tools/research/book_batch_test.py "
        f"--start {ev.get('requested_start')} --end {ev.get('requested_end')} "
        f"--is-end {ev.get('is_end')} --out research/reports/book_batch_test_20261101.json "
        f"--md research/reports/book_batch_test_20261101.md")
    add("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_book_batch_test.py")
    add("```")
    add("")
    add(f"运行耗时：{meta.get('runtime_sec')}s（其中载面板 {meta.get('load_sec')}s）；"
        f"批次版本 {meta.get('version')}。")
    return "\n".join(lines) + "\n"


# ================================================================ 落盘 / CLI
def write_outputs(payload: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> dict:
    """写 JSON + Markdown（自动建目录），返回两个路径字符串。"""
    jp = Path(json_path) if json_path else OUT_JSON
    mp = Path(md_path) if md_path else OUT_MD
    if not jp.is_absolute():
        jp = REPO / jp
    if not mp.is_absolute():
        mp = REPO / mp
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def print_mapping(spec_dir: str | Path | None = None) -> None:
    """打印 17 项规格 → 因子映射（--list，不载数据）。"""
    specs = load_untested_specs(spec_dir)
    mapping = build_spec_mapping(specs)
    print(f"# 量学战法批量初筛映射 · untested {len(specs)} 项（{sum(1 for m in mapping.values() if m['factor'])} "
          f"项可计算 / {sum(1 for m in mapping.values() if not m['factor'])} 项 C 级不实现）")
    print(f"# {'ID':<34}{'可计算化':<6}{'因子':<26}{'实现':<10}说明")
    for spec in specs:
        m = mapping[str(spec.get("id"))]
        factor = m.get("factor") or "—"
        impl = m.get("impl") or "不实现"
        note = (m.get("simplification") if m.get("factor") else m.get("uncomputable_reason")) or ""
        print(f"  {m['spec_id']:<34}{m.get('computability') or '-':<6}{factor:<26}{impl:<10}{note[:58]}")


def _print_summary(payload: dict) -> None:
    meta = payload.get("meta") or {}
    ev = meta.get("eval") or {}
    counts = payload.get("grade_counts") or {}
    print(f"# book_batch_test v{meta.get('version')} · untested {meta.get('spec', {}).get('untested')} 项"
          f"（A/B 可计算 {meta.get('spec', {}).get('testable_ab')}，C 不可计算 "
          f"{meta.get('spec', {}).get('uncomputable_c')}）")
    print(f"# 信号窗 {ev.get('effective_start')}~{ev.get('effective_end')}"
          f"（{ev.get('signal_days')} 日）| IS≤{ev.get('is_end')} | OOS≥2025-01-01 | "
          f"{ev.get('horizon_days')} 日 CAR | 单边 {ev.get('cost_bp_oneway')}bp | block={ev.get('block_bootstrap', {}).get('block')}")
    print(f"# grade 分布：" + " ".join(f"{g}={counts.get(g, 0)}" for g in GRADES))
    print(f"# {'ID':<34}{'可计算化':<6}{'因子':<24}{'事件数':>8}{'OOS_IC_t':>9}"
          f"{'OOS超额%':>10}{'p':>7}{'grade':>7}")
    for r in payload.get("results") or []:
        oos = r.get("oos") or {}
        print(f"  {r.get('spec_id'):<34}{r.get('computability') or '-':<6}"
              f"{(r.get('factor') or '—'):<24}{r.get('n_events') or 0:>8}"
              f"{_fmt(oos.get('ic_t')):>9}{_fmt(oos.get('excess_pct')):>10}"
              f"{_fmt(r.get('bootstrap_ic_p_le_0'), 3):>7}{r.get('grade'):>7}")
    print("# 建议优先深测 Top5：" + ", ".join(p.get("spec_id") for p in payload.get("priority") or []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="book_batch_test",
        description="量学战法批量四关初筛（17 个 untested 规格 → factor_lib 统一评测 → graded A-F）")
    ap.add_argument("--start", default=DEFAULT_START, help=f"信号起始日 YYYYMMDD（默认 {DEFAULT_START}）")
    ap.add_argument("--end", default=DEFAULT_END, help=f"信号结束日 YYYYMMDD（默认 {DEFAULT_END}）")
    ap.add_argument("--is-end", default=DEFAULT_IS_END,
                    help=f"IS/OOS 切分日（IS 含该日；默认 {DEFAULT_IS_END}）")
    ap.add_argument("--universe", choices=["tradable", "all"], default="tradable")
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT, help="block bootstrap 次数")
    ap.add_argument("--spec-dir", default=None, help="规格目录（默认 research/strategy_specs）")
    ap.add_argument("--data-root", default=None, help="tushare 数据根（默认 quant_backtest/data/tushare）")
    ap.add_argument("--out", default=str(OUT_JSON), help="输出 JSON 路径")
    ap.add_argument("--md", default=str(OUT_MD), help="输出 Markdown 路径")
    ap.add_argument("--list", action="store_true", help="只打印 17 项映射（不载数据）")
    ap.add_argument("--json-stdout", action="store_true", help="stdout 额外打印完整 JSON")
    args = ap.parse_args(argv)

    if args.list:
        print_mapping(args.spec_dir)
        return 0
    try:
        payload = run_batch(start=args.start, end=args.end, is_end=(args.is_end or None),
                            spec_dir=args.spec_dir, universe=args.universe,
                            n_boot=args.n_boot, data_root=args.data_root)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"[book_batch_test] 运行失败: {exc}", file=sys.stderr)
        return 2
    paths = write_outputs(payload, args.out, args.md)
    _print_summary(payload)
    print(f"# 输出: {paths['json']}")
    print(f"# 输出: {paths['md']}")
    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
