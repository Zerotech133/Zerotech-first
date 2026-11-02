# -*- coding: utf-8 -*-
"""tools/tests/test_factor_prescreen.py — 学习因子四关前筛（task-150）单元测试。

覆盖（≥3，合成数据为主，不跑真实数据）：
  1. RankIC 计算：因子=未来收益 → IC≈+1；反向 → ≈−1；ic_stats/directed/半窗字段；
  2. 分层：单调因子 → 组均值单调、单调性≈±1、多空方向正确；方向 -1 翻转正确；
  3. PIT 断言：干净因子截断不变性通过；使用未来数据的因子截断不变性失败；
  4. 分级规则：通过/观察/不通过 + PIT/覆盖率/方向先验降级；
  5. 报告 schema：真实产物字段完整、post_limit_behavior 单列不参与 IC。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_factor_prescreen.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
sys.path.insert(0, str(ROOT / "quant_backtest" / "factor_lib"))
import factor_prescreen as fp  # noqa: E402

REPORT = ROOT / "research" / "reports" / "factor_prescreen_20261103.json"


def _toy(days=8, codes=40, seed=7):
    rng = np.random.default_rng(seed)
    f = rng.normal(size=(days, codes))
    y = f * 0.5 + rng.normal(scale=0.05, size=(days, codes))  # 正相关
    return f, y


# ------------------------------------------------------------------ 1. RankIC
def test_rank_ic_and_stats():
    f, y = _toy()
    ic = fp.rank_ic(f, y)
    assert ic.notna().sum() == f.shape[0]
    assert ic.mean() > 0.9, "因子=未来收益近似 → RankIC 应接近 +1"
    # 反向
    ic_rev = fp.rank_ic(f, -y)
    assert ic_rev.mean() < -0.9
    st = fp.ic_stats(ic, direction=1)
    assert st["ic_mean"] == pytest.approx(float(ic.mean()), abs=1e-6)
    assert st["ir"] is not None and st["ir"] > 0
    assert st["half_split"]["same_sign"] is True
    # direction=-1 → directed 取反
    st2 = fp.ic_stats(ic, direction=-1)
    assert st2["directed_ic_mean"] == pytest.approx(-st["ic_mean"], abs=1e-6)
    # 零方差/空容错
    assert fp._spearman(np.ones(5), np.arange(5.0)) is None
    assert fp.ic_stats(pd.Series(dtype=float))["n_days"] == 0


# ------------------------------------------------------------------ 2. 分层
def test_layer_stats():
    days, codes = 6, 100
    order = np.tile(np.arange(codes, dtype=float), (days, 1))
    f = order.copy()
    y = order * 0.01                     # 因子越大未来收益越高（百分比数值）
    lay = fp.layer_stats(f, y, horizon=5, direction=1)
    assert lay["n_days"] == days
    g_means = [lay["groups"][f"G{i}"]["mean_fwd_pct"] for i in range(1, 6)]
    assert g_means == sorted(g_means), "单调递增因子 → 分层均值应递增"
    assert lay["monotonicity"] == pytest.approx(1.0, abs=1e-6)
    assert lay["long_short"]["mean_fwd_pct"] > 0
    # 方向 -1：多空取反
    lay_neg = fp.layer_stats(f, y, horizon=5, direction=-1)
    assert lay_neg["long_short"]["mean_fwd_pct"] < 0
    assert lay_neg["monotonicity"] == pytest.approx(-1.0, abs=1e-6)


# ------------------------------------------------------------------ 3. PIT 截断
def _toy_sample(days=120, codes=20, seed=3):
    rng = np.random.default_rng(seed)
    idx = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(days)]
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, size=(days, codes)), axis=0) * 10,
        index=idx, columns=[f"{i:06d}.SZ" for i in range(codes)])
    empty = pd.DataFrame(columns=["trade_date", "ts_code"])
    return {"fields": {"close": close}, "window_dates": idx,
            "all_dates": idx, "codes": list(close.columns), "data_end": idx[-1],
            "data_root": "", "moneyflow": empty, "limit_up": empty,
            "cyq": pd.DataFrame(columns=["trade_date", "ts_code"])}


def _identity_fn(sample):
    return {"matrices": {"identity": sample["fields"]["close"]}}


def _leak_fn(sample):
    return {"matrices": {"identity": sample["fields"]["close"].shift(-1)}}


def test_pit_truncation_detects_leak():
    sample = _toy_sample()
    t = sample["window_dates"][-5]
    ok = fp.pit_truncation_check(_identity_fn, sample, ["identity"], t)["identity"]
    assert ok["pass"] is True and ok["max_abs_diff"] == 0
    leak = fp.pit_truncation_check(_leak_fn, sample, ["identity"], t)["identity"]
    assert leak["pass"] is False, "使用未来数据的因子必须被 PIT 截断检查抓到"


# ------------------------------------------------------------------ 4. 分级
def _ic_block(ic20, ir20=0.5, same=True, ic5=None):
    return {"h20": {"directed_ic_mean": ic20, "directed_ir": ir20,
                    "half_split": {"same_sign": same}},
            "h5": {"directed_ic_mean": ic5 if ic5 is not None else ic20},
            "h1": {"directed_ic_mean": 0.0}, "h10": {"directed_ic_mean": 0.0}}


def test_grade_factor():
    g, r = fp.grade_factor(_ic_block(0.05), coverage_pct=90, pit_pass=True, direction=1)
    assert g == "通过"
    g, _ = fp.grade_factor(_ic_block(0.05, ir20=0.1), coverage_pct=90, pit_pass=True, direction=1)
    assert g == "观察"
    g, _ = fp.grade_factor(_ic_block(0.005, ir20=0.05), coverage_pct=90, pit_pass=True, direction=1)
    assert g == "不通过"
    g, _ = fp.grade_factor(_ic_block(0.05), coverage_pct=30, pit_pass=True, direction=1)
    assert g == "不通过"  # 覆盖率不足
    g, _ = fp.grade_factor(_ic_block(0.05), coverage_pct=90, pit_pass=False, direction=1)
    assert g == "不通过"  # PIT 不过
    # 方向先验为正但 directed IC 为负 → 不得通过
    g, r = fp.grade_factor(_ic_block(-0.05), coverage_pct=90, pit_pass=True, direction=1)
    assert g in ("观察", "不通过") and r


# ------------------------------------------------------------------ 5. 报告 schema
def test_report_schema():
    assert REPORT.exists(), f"报告缺失: {REPORT}"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rep.get("schema_check") == [], rep.get("schema_check")
    assert fp.validate_report_schema(rep) == []
    assert set(rep["factors"].keys()) == set(fp.PIT_FACTORS)
    assert rep["post_limit_behavior"]["excluded_from_ic"] is True
    total = sum(len(rep["summary"][k]) for k in ("通过", "观察", "不通过"))
    assert total == len(fp.PIT_FACTORS)
    for name, f in rep["factors"].items():
        assert f["grade"] in ("通过", "观察", "不通过")
        for h in fp.HORIZONS:
            assert f"h{h}" in f["ic"] and f"h{h}" in f["layers"]
        assert f["pit"]["static_safe"] is True
        assert "long_short" in f["layers"]["h20"]
    # 坏报告
    broken = json.loads(json.dumps(rep))
    broken["post_limit_behavior"]["excluded_from_ic"] = False
    assert fp.validate_report_schema(broken)
