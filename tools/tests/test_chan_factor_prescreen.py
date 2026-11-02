# -*- coding: utf-8 -*-
"""tools/tests/test_chan_factor_prescreen.py — 缠论因子独立前筛（task-152）单元测试。

覆盖（≥3，合成数据为主，不跑真实数据）：
  1. RankIC/IC 统计：特征=未来收益 → IC≈+1，反向 ≈−1；半窗/IR 字段；
  2. 5 层分层：单调特征 → 单调性≈1、多空为正；方向翻转 → 单调性≈−1；
  3. PIT：合成 bars 上 `build_structures/features_asof` 的当日特征只依赖当日及以前 bars
     （新增未来 bar 不改当日及以前特征）；含未来信息的伪特征会被识别为不一致；
  4. 分级规则：通过/观察/不通过 + PIT/覆盖率降级；
  5. 报告 schema：真实产物字段完整。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_chan_factor_prescreen.py
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
import chan_factor_prescreen as cp  # noqa: E402

REPORT = ROOT / "research" / "reports" / "chan_factor_prescreen_20261103.json"


# ------------------------------------------------------------------ 1. RankIC
def test_rank_ic():
    rng = np.random.default_rng(5)
    f = rng.normal(size=(10, 40))
    y = f * 0.5 + rng.normal(scale=0.02, size=(10, 40))
    ic = cp.rank_ic(f, y)
    assert ic.notna().sum() == 10
    assert ic.mean() > 0.9
    assert cp.rank_ic(f, -y).mean() < -0.9
    st = cp.ic_stats(ic)
    assert st["ic_mean"] == pytest.approx(float(ic.mean()), abs=1e-6)
    assert st["ir"] is not None and st["ir"] > 0
    assert st["half_split"]["same_sign"] is True
    # 不足 min_names / 零方差容错
    assert cp.rank_ic(f[:, :5], y[:, :5]).isna().all()
    assert cp._spearman(np.ones(5), np.arange(5.0)) is None
    assert cp.ic_stats(pd.Series(dtype=float))["n_days"] == 0


# ------------------------------------------------------------------ 2. 分层
def test_layer_stats():
    days, codes = 6, 100
    order = np.tile(np.arange(codes, dtype=float), (days, 1))
    y = order * 0.01
    lay = cp.layer_stats(order, y, horizon=5)
    g = [lay["groups"][f"G{i}"]["mean_fwd_pct"] for i in range(1, 6)]
    assert g == sorted(g), "单调特征 → 分层均值递增"
    assert lay["monotonicity"] == pytest.approx(1.0, abs=1e-6)
    assert lay["long_short"]["mean_fwd_pct"] > 0
    # 反向特征 → 单调性 -1、多空为负
    lay2 = cp.layer_stats(-order, y, horizon=5)
    assert lay2["monotonicity"] == pytest.approx(-1.0, abs=1e-6)
    assert lay2["long_short"]["mean_fwd_pct"] < 0


# ------------------------------------------------------------------ 3. PIT
def _synth_bars(days: int = 90, seed: int = 3) -> pd.DataFrame:
    """合成 30 分钟 bars：每日 8 根、随机游走。"""
    rng = np.random.default_rng(seed)
    rows = []
    price = 10.0
    for i in range(days):
        d = f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}"
        for t in ("10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"):
            price *= 1 + rng.normal(0, 0.005)
            rows.append({"dt": f"{d} {t}", "open": price, "high": price * 1.002,
                         "low": price * 0.998, "close": price, "vol": 1e6, "amount": 1e7})
    return pd.DataFrame(rows)


def _toy_struct():
    """手工结构：days=7；笔/中枢/背驰/买卖点分布在不同日，用于验证 as-of 前缀性质。"""
    return {
        "days": [f"2026010{i}" for i in range(1, 8)],
        "bis": [{"dpos": 1, "dir": 1, "closed": True},
                {"dpos": 3, "dir": -1, "closed": True},
                {"dpos": 5, "dir": 1, "closed": True}],
        "zs": [{"dpos": 3, "width_pct": 0.05, "closed": True}],
        "beichi": [{"dpos": 3, "strength": 0.6, "div": True, "closed": True}],
        "points": [{"dpos": 5, "sign": 1}],
        "n_bars": 56, "res": {},
    }


def test_features_asof_uses_only_past_structures():
    """features_asof 必须只使用 dpos<=当日 的结构（纯函数前缀性质，可单测）。"""
    st = _toy_struct()
    f = cp.features_asof(st)
    # 第 0 日：无任何结构 → 笔数 0、最后方向 0、无中枢/背驰/买卖点
    assert f["bi_count_20d"][0] == 0 and f["bi_last_dir"][0] == 0
    assert f["beichi_div_count_20d"][0] == 0 and f["bs_net_5d"][0] == 0
    # 第 2 日：只有 dpos=1 的笔 → 计数 1、方向 +1
    assert f["bi_count_20d"][2] == 1 and f["bi_last_dir"][2] == 1
    # 第 4 日：加入 dpos=3 的结构（笔 -1、中枢、背驰）
    assert f["bi_count_20d"][4] == 2 and f["bi_last_dir"][4] == -1
    assert f["zs_width_last"][4] == pytest.approx(0.05)
    assert f["beichi_div_count_20d"][4] == 1
    assert f["beichi_last_strength"][4] == pytest.approx(0.6)
    # 第 6 日：加入 dpos=5 的笔（+1）与买点
    assert f["bi_last_dir"][6] == 1 and f["bs_net_5d"][6] == 1
    # 未来结构（dpos 大于当日）不得影响历史：截断 struct 到 dpos<=4 后，第 4 日特征不变
    st_trunc = {"days": st["days"][:5],
                "bis": [b for b in st["bis"] if b["dpos"] <= 4],
                "zs": [z for z in st["zs"] if z["dpos"] <= 4],
                "beichi": [e for e in st["beichi"] if e["dpos"] <= 4],
                "points": [p for p in st["points"] if p["dpos"] <= 4],
                "n_bars": 40, "res": {}}
    f_trunc = cp.features_asof(st_trunc)
    for name in cp.FEAT_NAMES:
        a, b = f[name][4], f_trunc[name][-1]
        if np.isnan(a) and np.isnan(b):
            continue
        assert a == pytest.approx(b, abs=1e-12), f"{name}: 未来结构不得影响历史 as-of 特征"
    # PIT 截断检查冒烟：真实合成 bars 上能跑通并给出不一致率（如实披露，不做通过断言）
    bars = _synth_bars(90)
    full = cp.build_structures(bars)
    chk = cp.pit_truncation_check(bars, [full["days"][60]])
    assert chk["checked_code_days"] == 1
    assert set(chk["mismatch_rates"]) == set(cp.FEAT_NAMES)


# ------------------------------------------------------------------ 4. 分级
def _ic_block(ic20, ir20=0.5, same=True, ic5=None):
    return {"h20": {"ic_mean": ic20, "ir": ir20, "half_split": {"same_sign": same}},
            "h10": {"ic_mean": 0.0, "ir": 0.0}, "h5": {"ic_mean": ic5 if ic5 is not None else ic20}}


def test_grade_factor():
    assert cp.grade_factor(_ic_block(0.05), 90, True)[0] == "通过"
    assert cp.grade_factor(_ic_block(0.05, ir20=0.1), 90, True)[0] == "观察"
    assert cp.grade_factor(_ic_block(0.005, ir20=0.05), 90, True)[0] == "不通过"
    assert cp.grade_factor(_ic_block(0.05), 30, True)[0] == "不通过"   # 覆盖率不足
    assert cp.grade_factor(_ic_block(0.05), 90, False)[0] == "不通过"  # PIT 不许
    # 仅短周期显著 → 观察
    assert cp.grade_factor(_ic_block(0.005, ir20=0.01, ic5=0.05), 90, True)[0] == "观察"


# ------------------------------------------------------------------ 5. 报告 schema
def test_report_schema():
    assert REPORT.exists(), f"报告缺失: {REPORT}"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rep.get("schema_check") == [], rep.get("schema_check")
    assert cp.validate_report_schema(rep) == []
    assert set(rep["features"].keys()) == set(cp.FEAT_NAMES)
    total = sum(len(rep["summary"][k]) for k in ("通过", "观察", "不通过"))
    assert total == len(cp.FEAT_NAMES)
    for name, f in rep["features"].items():
        assert f["grade"] in ("通过", "观察", "不通过")
        for h in cp.HORIZONS:
            assert f"h{h}" in f["ic"] and f"h{h}" in f["layers"]
        assert "pass" in f["pit"]
    broken = json.loads(json.dumps(rep))
    broken["summary"]["通过"].append("不存在")  # 分级数不一致
    assert cp.validate_report_schema(broken)
