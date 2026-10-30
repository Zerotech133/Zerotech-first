# -*- coding: utf-8 -*-
"""统计修正工具（判例 2026-10-30：H22 前视 / H23 重叠）。

低频信号 × 多日持有（如周频信号 × 20 日收益）会有严重重叠，普通 t 值虚高。
本模块提供标准修正：
- non_overlap_t(series, step)：每 step 个观测取 1（多偏移平均/范围），给出保守 t；
- block_bootstrap_p(series, block, n_boot)：块自助，返回均值>0 的 p 值（与 OOS 同向检验）；
- overlap_aware_t(series, step)：非重叠修正 t（返回 4 个偏移的 t 列表与均值）。

用法：
    from stat_utils import non_overlap_t, block_bootstrap_p
    res = non_overlap_t(df["spread"], step=4, splits={"OOS": oos_mask})
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _tstat(s: pd.Series):
    """t 值；样本不足/零波动返回 None（而不是 0，避免与「真实零 t」混淆）。"""
    s = pd.Series(s, dtype=float).dropna()
    if len(s) < 5 or s.std(ddof=1) == 0:
        return None
    return float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))


def non_overlap_t(series: pd.Series, step: int = 4) -> dict:
    """每 step 个观测取 1（step 个偏移），返回各偏移 t 与均值/最大。"""
    t = []
    for off in range(step):
        sub = series.iloc[off::step].dropna()
        tv = _tstat(sub)
        t.append(round(tv, 2) if tv is not None else None)
    vals = [x for x in t if x is not None]
    return {"values": t, "mean": round(float(np.mean(vals)), 2) if vals else None,
            "max": max(vals) if vals else None, "min": min(vals) if vals else None}


def block_bootstrap_p(series: pd.Series, block: int = 4, n_boot: int = 500, seed: int = 42) -> dict:
    """块自助：重抽样 block 长度块的均值，返回均值与 P(均值<=0)。"""
    x = pd.Series(series, dtype=float).dropna().to_numpy()
    if len(x) < block * 3:
        return {"n_boot": 0, "mean_pct": None, "p_le_0": None}
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(x) // block)
    means = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(1, len(x) - block), size=n_blocks)
        sample = np.concatenate([x[s:s + block] for s in starts])[: len(x)]
        means.append(sample.mean())
    means = np.array(means)
    return {"n_boot": int(n_boot), "mean_pct": round(float(means.mean()) * 100, 3),
            "p_le_0": round(float((means <= 0).mean()), 4)}


def overlap_aware_verdict(series: pd.Series, step: int = 4, block: int = 4,
                          n_boot: int = 500) -> dict:
    """综合判定：非重叠平均 t ≥ 2 且 bootstrap p<0.05 才算通过（保守口径）。"""
    no = non_overlap_t(series, step)
    bs = block_bootstrap_p(series, block, n_boot)
    ok = (no.get("mean") is not None and no["mean"] >= 2
          and bs.get("p_le_0") is not None and bs["p_le_0"] < 0.05)
    return {"non_overlap": no, "bootstrap": bs, "pass": bool(ok)}
