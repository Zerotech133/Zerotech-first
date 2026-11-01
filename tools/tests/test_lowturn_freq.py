# -*- coding: utf-8 -*-
"""tools/tests/test_lowturn_freq.py — 支线 G 低频/成本优化验证单元测试。

覆盖（任务验收 ≥4 项，全部合成数据、不跑全量回测）：
  1. 信号日生成：月频（每月最后一个交易日）、等距交易日切片（60 日/20 日错开）；
  2. 缓冲带中性权重：与 factor_validate.neutral_bucket_weights（wave2 口径）等价、
     Σ权重=1、桶总权重=市场股票数占比、已有持仓在放宽阈值内被保留；
  3. 换手约束：单次双边换手上限按比例生效、分段（tranche）换手、首次建仓换手≈1；
  4. 成本敏感解析折算：0bp = 毛净值、拖累随费率单调、与直接逐笔复利一致；
  5. 指标/判定：合成 4 年日频数据下 OOS 超额>0、t≥2、逐年同向≥3/4 → pass_net；
     净超额为负 → fail；年度超额计算取上年末基准。
  6. 报告 schema：JSON 可序列化、关键字段齐全。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_lowturn_freq.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_validate as fv  # noqa: E402
import lowturn_freq_validate as lv  # noqa: E402


# ------------------------------------------------------------------ 合成小样本
def _toy(n: int = 60, n_ind: int = 3):
    codes = [f"{i:06d}.SZ" for i in range(n)]
    scores = pd.Series(np.arange(n, dtype="float64"), index=codes)
    eligible = pd.Series(True, index=codes)
    circ_mv = pd.Series(np.linspace(10.0, 1000.0, n), index=codes)
    industry = pd.Series([f"IND{i % n_ind}" for i in range(n)], index=codes)
    return codes, scores, eligible, circ_mv, industry


def _trading_dates(start: str = "2023-01-02", end: str = "2026-10-30") -> list[str]:
    return [d.strftime("%Y%m%d") for d in pd.bdate_range(start, end)]


# ------------------------------------------------------------------ 1. 信号日
def test_monthly_signal_dates():
    dates = ["20230103", "20230131", "20230201", "20230228", "20230227", "20230331"]
    ds = lv.monthly_signal_dates(dates)
    assert ds == ["20230131", "20230228", "20230331"]
    assert lv.monthly_signal_dates([]) == []


def test_every_nth_signal_dates():
    dates = [f"2023{m:02d}{d:02d}" for m in range(1, 5) for d in (3, 10, 17, 24)]
    every4 = lv.every_nth_signal_dates(dates, 4)
    assert every4 == dates[::4]
    assert lv.every_nth_signal_dates(dates, 2, offset=1) == dates[1::2]
    assert lv.every_nth_signal_dates([], 5) == []
    with pytest.raises(ValueError):
        lv.every_nth_signal_dates(dates, 0)


# ------------------------------------------------------------------ 2. 缓冲带权重
def test_buffered_weights_equivalent_to_wave2_without_buffer():
    """buffer=0 且无历史持仓 → 与 factor_validate.neutral_bucket_weights 完全一致。"""
    codes, scores, eligible, circ_mv, industry = _toy()
    base = fv.neutral_bucket_weights(scores, eligible, circ_mv, industry, hold_frac=0.5)
    mine = lv.buffered_bucket_weights(scores, eligible, circ_mv, industry,
                                      prev_held=None, hold_frac=0.5, buffer_frac=0.0)
    assert len(base) == len(mine) > 0
    pd.testing.assert_series_equal(base.sort_index(), mine.sort_index(), check_exact=False,
                                   rtol=1e-9)


def test_buffered_weights_market_exposure_and_retention():
    """Σ权重=1；桶总权重=市场股票数占比；已有持仓在 +10pp 放宽阈值内被保留。"""
    codes, scores, eligible, circ_mv, industry = _toy(n=120, n_ind=3)
    # 桶：市值十分位 × 行业；n=120 → 每桶 4 只，hold_frac=0.5 → 买 2 只 / 保留阈值 top60% → 3 只
    base = lv.buffered_bucket_weights(scores, eligible, circ_mv, industry,
                                      prev_held=None, hold_frac=0.5, buffer_frac=0.0)
    assert abs(float(base.sum()) - 1.0) < 1e-9
    # 桶总权重校验：每个桶内选中股权重和 == 该桶市场股票数占比
    dec = pd.qcut(circ_mv, 10, labels=False, duplicates="drop")
    bucket = pd.Series([f"{dec[c]}|{industry[c]}" for c in codes], index=codes)
    counts = bucket.value_counts()
    shares = counts / float(counts.sum())
    for b, w_b in shares.items():
        picked = base.index[[bucket[c] == b for c in base.index]]
        got = float(base.loc[picked].sum())
        assert abs(got - float(w_b)) < 1e-9, (b, got, float(w_b))
    # 构造历史持仓：每个桶里「分数最低但仍在 top60%」的股票（base 只买 top50% 不含它）
    picks: list[str] = []
    for b, group in scores.groupby(bucket):
        group = group.sort_values(ascending=False)
        n_b = len(group)
        k_buy = max(1, math.ceil(0.5 * n_b))
        k_hold = max(k_buy, math.ceil(0.6 * n_b))
        if k_hold > k_buy:
            picks.append(str(group.index[k_hold - 1]))
    assert picks, "合成数据应构造出位于 50%~60% 区间的历史持仓"
    buffered = lv.buffered_bucket_weights(scores, eligible, circ_mv, industry,
                                          prev_held=picks, hold_frac=0.5,
                                          buffer_frac=0.10)
    kept = [c for c in picks if c in buffered.index]
    assert len(kept) == len(picks), "缓冲带内已有持仓应全部保留"
    assert len(buffered) > len(base), "缓冲带应使持仓数增加"


# ------------------------------------------------------------------ 3. 换手约束
def test_rebalance_weights_first_build_and_cap():
    target = pd.Series({"a": 0.5, "b": 0.5})
    final, turnover = lv.rebalance_weights(pd.Series(dtype=float), target)
    assert abs(turnover - 1.0) < 1e-12
    assert abs(float(final.sum()) - 1.0) < 1e-12
    # 现有持仓 a/b 全换成 c/d（drift 与 target 不相交）→ 原始双边换手 = 2.0，上限 0.2
    drift = pd.Series({"a": 0.5, "b": 0.5})
    new_target = pd.Series({"c": 0.5, "d": 0.5})
    _, raw_turn = lv.rebalance_weights(drift, new_target)
    assert abs(raw_turn - 2.0) < 1e-12
    final, capped = lv.rebalance_weights(drift, new_target, max_turnover=0.20)
    assert abs(capped - 0.20) < 1e-12
    # 只换 10%：旧持仓仍占 90%
    assert final.get("a", 0.0) + final.get("b", 0.0) == pytest.approx(0.90, abs=1e-9)
    assert final.get("c", 0.0) + final.get("d", 0.0) == pytest.approx(0.10, abs=1e-9)
    assert abs(float(final.sum()) - 1.0) < 1e-9


def test_rebalance_weights_tranche_fraction():
    drift = pd.Series({"a": 1.0})
    target = pd.Series({"a": 0.5, "b": 0.5})
    final, turnover = lv.rebalance_weights(drift, target, tranche_frac=1.0 / 3.0)
    assert turnover == pytest.approx(1.0 / 3.0, abs=1e-9)  # 只换 1/3 仓
    assert final["b"] == pytest.approx(1.0 / 6.0, abs=1e-9)
    assert final["a"] == pytest.approx(5.0 / 6.0, abs=1e-9)
    # 空目标 → 不交易
    empty_w, t0 = lv.rebalance_weights(drift, pd.Series(dtype=float))
    assert t0 == 0.0 and empty_w.empty


# ------------------------------------------------------------------ 4. 成本敏感折算
def test_net_nav_map_cost_conversion():
    gross = {"d1": 1.0, "d2": 1.10, "d3": 1.21}
    turns = [{"date": "d2", "turnover": 0.5}]
    # 0bp → 净=毛
    assert lv.net_nav_map(gross, turns, 0.0) == pytest.approx(gross)
    # 30bp：d2 之后净值 × (1 − 0.5×0.003)
    net30 = lv.net_nav_map(gross, turns, 30.0)
    assert net30["d1"] == pytest.approx(1.0)
    assert net30["d2"] == pytest.approx(1.10 * (1 - 0.5 * 0.003))
    assert net30["d3"] == pytest.approx(1.21 * (1 - 0.5 * 0.003))
    # 单调：费率越高净值越低；累计拖累 = 1 − Π(1−turnover×bp)
    net50 = lv.net_nav_map(gross, turns, 50.0)
    assert net50["d3"] < net30["d3"] < gross["d3"]
    assert lv.cost_drag(gross, turns, 30.0) == pytest.approx(0.5 * 0.003, abs=1e-9)
    with pytest.raises(ValueError):
        lv.net_nav_map(gross, turns, -1.0)


# ------------------------------------------------------------------ 5. 指标与判定
def _synthetic_nav(dates, daily_excess: float = 0.0004, bench_daily: float = 0.0001):
    """合成 4 年日频：策略日收益 = 基准 + 固定超额（叠加微小交替波动以避免 std=0）。"""
    strat = {dates[0]: 1.0}
    bench = {dates[0]: 1.0}
    for i, d in enumerate(dates[1:], start=1):
        noise = 0.0001 if i % 2 == 0 else -0.0001
        strat[d] = strat[dates[i - 1]] * (1.0 + bench_daily + daily_excess + noise)
        bench[d] = bench[dates[i - 1]] * (1.0 + bench_daily)
    return strat, bench


def test_variant_metrics_pass_and_fail():
    dates = _trading_dates()
    strat, bench = _synthetic_nav(dates)
    turns = [{"date": dates[i], "turnover": 0.1} for i in range(5, len(dates), 20)]
    m = lv.variant_metrics(strat, bench, turns, 30.0, adtv_yi=1.0, avg_holdings=100.0)
    assert m["gross_oos_excess"] > 0 and m["net_oos_excess"] > 0
    assert m["net_oos_t"] >= 2.0
    assert m["annual_net_positive"] >= 3
    assert m["verdict_net"] == "pass"
    assert m["verdict_gross"] == "pass"
    assert m["avg_turnover"] == pytest.approx(0.1)
    assert m["capacity_est_yi"] is not None and m["capacity_est_yi"] > 0
    # 负超额 → fail（逐年同向不达标）
    bad_strat, _ = _synthetic_nav(dates, daily_excess=-0.0002)
    m_bad = lv.variant_metrics(bad_strat, bench, turns, 30.0, adtv_yi=1.0,
                               avg_holdings=100.0)
    assert m_bad["net_oos_excess"] < 0
    assert m_bad["verdict_net"] == "fail"


def test_variant_metrics_cost_erosion():
    """同一毛路径下，成本越高净 OOS 越低（成本敏感表单调性）。"""
    dates = _trading_dates()
    strat, bench = _synthetic_nav(dates)
    turns = [{"date": dates[i], "turnover": 0.6} for i in range(5, len(dates), 20)]
    vals = [lv.variant_metrics(strat, bench, turns, bp, 1.0, 100.0)["net_oos_excess"]
            for bp in (0.0, 10.0, 30.0, 50.0)]
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
    assert vals[0] >= vals[-1]


# ------------------------------------------------------------------ 6. 报告 schema
def test_report_json_serializable():
    rep = {
        "meta": {"schema_version": lv.SCHEMA_VERSION,
                 "generated_at": "2026-11-01T00:00:00+08:00",
                 "years": list(lv.YEARS), "oos_years": list(lv.OOS_YEARS),
                 "is_years": list(lv.IS_YEARS)},
        "variant_meta": {"monthly": {"label": "月频全换仓", "freq_desc": "月频",
                                     "buffer_frac": 0.0, "max_turnover": None,
                                     "desc": "x"}},
        "factors": {}, "conclusion": {"tradeable_after_cost": False},
        "notes": lv.build_notes(),
    }
    txt = json.dumps(fv._clean(rep), ensure_ascii=False)
    back = json.loads(txt)
    assert back["meta"]["schema_version"] == "lowturn_freq_validate.v1"
    assert back["conclusion"]["tradeable_after_cost"] is False
    assert any("成本" in n for n in back["notes"])
    # 变体注册表健全：默认变体都有 schedule/buffer/cap 定义
    for v in lv.DEFAULT_VARIANTS:
        spec = lv.VARIANTS[v]
        assert spec["schedule"] in {"weekly", "monthly", "every20", "every60"}
        assert 0.0 <= spec["buffer_frac"] <= 0.5
    assert {"monthly", "hold60", "hold60_stagger",
            "weekly_buffercap", "monthly_buffercap"} <= set(lv.DEFAULT_VARIANTS)
