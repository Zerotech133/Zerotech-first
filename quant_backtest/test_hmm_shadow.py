# -*- coding: utf-8 -*-
"""hmm_shadow 影子读数测试（合成数据，不触网、不依赖真实序列文件）。"""

import json
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hmm_shadow as hs


def _synthetic_series(n_a=220, n_b=90):
    """两段式合成：低波正位段 + 深弱势段（研究判例同构）。"""
    rng = np.random.default_rng(hs.hgr.SEED)
    a = np.clip(0.55 + rng.normal(0, 0.02, n_a).cumsum() * 0.001, 0.4, 0.8)
    b = np.clip(0.25 + rng.normal(0, 0.03, n_b).cumsum() * 0.001, 0.08, 0.38)
    b = np.minimum.accumulate(b[::-1])[::-1] * 0 + b  # 保持原序
    brs = np.r_[a, b]
    dates = ["%07d" % (9000000 + i) for i in range(len(brs))]
    return dates, brs


def _mk_series_fp(tmp, dates, brs):
    fp = os.path.join(tmp, "series.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(
            {"dates": dates, "breadth": [None if x is None else float(x) for x in brs]},
            f,
        )
    return fp


def test_compute_shadow_shape_and_keys():
    dates, brs = _synthetic_series()
    with tempfile.TemporaryDirectory() as tmp:
        st = hs.compute_shadow(_mk_series_fp(tmp, dates, brs))
    assert st["date"] == dates[-1]
    assert st["K"] in (2, 3)
    assert 0.0 <= st["p_risk_filtered"] <= 1.0
    assert isinstance(st["hysteresis_on"], bool)
    assert abs(st["breadth"] - brs[-1]) < 1e-4  # 输出按 4 位小数舍入
    assert len(st["state_breadth_mean"]) == st["K"]


def test_risk_state_posterior_high_in_weak_segment():
    """深弱势段末尾：风险态后验应显著高于 0.5（合成判例）。"""
    dates, brs = _synthetic_series()
    with tempfile.TemporaryDirectory() as tmp:
        st = hs.compute_shadow(_mk_series_fp(tmp, dates, brs))
    assert st["p_risk_filtered"] > 0.5


def test_null_anchors_skipped_and_short_series_raises():
    dates, brs = _synthetic_series()
    dates2 = dates + ["x1", "x2"]
    brs2 = list(brs) + [None, None]
    with tempfile.TemporaryDirectory() as tmp:
        fp = _mk_series_fp(tmp, dates2, brs2)
        st = hs.compute_shadow(fp)
        assert st["date"] == dates[-1]  # null 尾锚已跳过
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"dates": ["a", "b", "c"], "breadth": [0.3, 0.3, 0.3]}, f)
        try:
            hs.compute_shadow(fp)
            raised = False
        except ValueError:
            raised = True
        assert raised  # <60 锚拒绝拟合
