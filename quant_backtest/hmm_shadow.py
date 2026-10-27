#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HMM 软闸门影子读数（W8 议题二前置，20261015，研究口径·只读）。

复用 hmm_gate_research 的纯 numpy 高斯 HMM 与判据（BIC 选 K、
"标准化宽度均值最低=风险态"、迟滞双阈值 0.6/0.4），滚动窗 504
交易日重拟合，输出当前风险态后验与迟滞门状态——供影子周报"市况·
正确路径"节透出，**不接任何产线行为**。口径标注：
- filtered 后验（只用截至末锚数据，无未来函数）；
- "风险态"标签来自全窗研究拟合（含末段信息），属研究口径标注、
  非可交易信号——W8 评审材料已明确按"硬阈值管常规 + HMM 管极端"
  并联影子推进。
"""

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hmm_gate_research as hgr  # noqa: E402  纯 numpy，无新增依赖

SERIES_FP = hgr.BREADTH_JSON
WINDOW = 504  # 滚动两年（W8 口径"滚动拟合攒影子"）


def load_series(fp=SERIES_FP):
    """宽度序列 → (dates[list], b[np.array float])，跳 null 锚。"""
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    dates, brs = d.get("dates") or [], d.get("breadth") or []
    pairs = [
        (str(a), float(b)) for a, b in zip(dates, brs) if isinstance(b, (int, float))
    ]
    if len(pairs) < 60:
        raise ValueError("breadth series too short: %d" % len(pairs))
    return [p[0] for p in pairs], np.array([p[1] for p in pairs])


def _features(b):
    """研究同款特征 [宽度水平, 5 日动量] + 标准化。返回 (Xz, mu, sd)。"""
    X_raw = np.column_stack(
        [b, np.r_[np.full(hgr.MOM_LAG, np.nan), np.diff(b, n=hgr.MOM_LAG)]]
    )
    valid = ~np.isnan(X_raw).any(axis=1)
    Xv = X_raw[valid]
    mu, sd = Xv.mean(axis=0), Xv.std(axis=0)
    return (Xv - mu) / sd, valid


def compute_shadow(series_fp=SERIES_FP, window=WINDOW):
    dates, b = load_series(series_fp)
    if window and len(b) > window + hgr.MOM_LAG:
        b, dates = b[-(window + hgr.MOM_LAG) :], dates[-(window + hgr.MOM_LAG) :]
    Xz, valid = _features(b)
    vd = [d for d, v in zip(dates, valid) if v]

    # BIC 选 K（研究同款判据）
    models, bics = {}, {}
    T = len(Xz)
    for K in (2, 3):
        m = hgr.fit_hmm_best(Xz, K)
        models[K] = m
        bics[K] = hgr.bic(m.loglik_, T, hgr.n_hmm_params(K, 2))
    best_K = min(bics, key=bics.get)
    best = models[best_K]

    # 风险态 = 标准化宽度均值最低的状态（研究同款标注）
    gamma = best.predict_proba(Xz, smoothed=True)
    state_breadth = [float(Xz[gamma[:, k] > 0.5, 0].mean()) for k in range(best.K)]
    risk_label = int(np.argsort(state_breadth)[0])

    # filtered 后验（无未来函数）+ 迟滞门（研究参数 0.6/0.4）
    p_risk_f = best.predict_proba(Xz, smoothed=False)[:, risk_label]
    gate = hgr.hysteresis_gate(p_risk_f, hgr.HYST_HI, hgr.HYST_LO)

    return {
        "date": vd[-1],
        "K": int(best_K),
        "bic": {str(k): round(float(v), 1) for k, v in bics.items()},
        "window_days": int(T),
        "breadth": round(float(b[-1]), 4),
        "state_breadth_mean": [round(x, 3) for x in state_breadth],
        "p_risk_filtered": round(float(p_risk_f[-1]), 4),
        "hysteresis_on": bool(gate[-1]),
        "hysteresis": {"hi": hgr.HYST_HI, "lo": hgr.HYST_LO},
        "hard_line": {"cross": 0.45, "fake": 0.40},
        "note": "研究口径·影子：风险态标签含全窗信息，非可交易信号；"
        "filtered 后验与迟滞门无未来函数。",
    }


if __name__ == "__main__":
    print(json.dumps(compute_shadow(), ensure_ascii=False))
