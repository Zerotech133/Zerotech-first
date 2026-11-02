# -*- coding: utf-8 -*-
"""tools/tests/test_chan_asof_engine.py · W2-6b 缠论 PIT 安全引擎测试（≥5）。

覆盖：
  1. 输出 schema / 8 特征齐全 / 两次运行逐位一致（可复现）；
  2. PIT 未来不变性：追加未来 bar 不改变历史日特征（按日截断即 PIT 定义）；
  3. 引擎 vs 每日暴力重算（analyze_chan 截断）逐日一致；
  4. 增量逐日喂入 == 一次性全量喂入；
  5. 抽样截断一致性 verify_truncation 不一致率 = 0（≤10% 通过）；
  6. 边界：空 bars / 过短 bars 降级不抛异常；
  7. 特征名/阈值与 tools/research/chan_factor_prescreen.py（只读 import）一致；
  8. 真实 stk_mins 小样本：引擎特征 == 旧前筛 build_structures+features_asof 在同日截断下的值
     （保证 v2 与 v1 特征口径同义，差异只来自 PIT 边界）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_chan_asof_engine.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "quant_backtest" / "factor_lib"))
sys.path.insert(0, str(ROOT / "tools" / "research"))

import chan_asof_engine as eng  # noqa: E402
import chan_factors as cf  # noqa: E402

MINS = ROOT / "quant_backtest" / "data" / "tushare" / "stk_mins"


# ------------------------------------------------------------------ 合成 bars
def make_bars(n_days: int = 40, seed: int = 7) -> pd.DataFrame:
    """合成 30 分钟 bars：每日 8 根，随机游走 + 分段趋势（保证有笔/中枢结构）。"""
    rng = np.random.default_rng(seed)
    times = []
    for i in range(n_days):
        day = f"2026{(i // 21) + 1:02d}{(i % 21) + 1:02d}"
        times += [f"{day} {h:02d}:{m:02d}" for h, m in
                  [(10, 0), (10, 30), (11, 0), (11, 30), (13, 30), (14, 0), (14, 30), (15, 0)]]
    n = len(times)
    drift = np.tile(rng.normal(0, 0.02, size=(n_days, 1)), (1, 8)).ravel()
    ret = drift + rng.normal(0, 0.01, size=n)
    close = 10.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.004, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"dt": times, "open": open_, "high": high, "low": low,
                         "close": close, "vol": 1000.0})


def _nan_safe_equal(a, b) -> bool:
    a_nan = a is None or (isinstance(a, float) and np.isnan(a))
    b_nan = b is None or (isinstance(b, float) and np.isnan(b))
    if a_nan and b_nan:
        return True
    return (not a_nan) and (not b_nan) and abs(float(a) - float(b)) <= 1e-9


# ------------------------------------------------------------------ 1. schema / 可复现
def test_day_features_schema_and_determinism():
    bars = make_bars(40)
    out1 = eng.day_features(bars)
    out2 = eng.day_features(bars)
    assert len(out1["days"]) == 40
    assert set(out1["features"]) == set(eng.FEAT_NAMES) and len(eng.FEAT_NAMES) == 8
    for name in eng.FEAT_NAMES:
        assert len(out1["features"][name]) == 40
        np.testing.assert_allclose(out1["features"][name], out2["features"][name], equal_nan=True)
    assert out1["raw_bars"] == len(bars) and out1["merged_bars"] > 0
    assert out1["structure_counts"][-1]["day"] == out1["days"][-1]


# ------------------------------------------------------------------ 2. PIT 未来不变性
def test_pit_future_invariance():
    bars = make_bars(60, seed=11)
    cut = 40
    prefix = bars[bars["dt"].astype(str).str[:8] <= eng._day_of(bars["dt"].iloc[0])].copy()
    days = sorted(bars["dt"].astype(str).str[:8].unique().tolist())
    cut_day = days[cut - 1]
    prefix = bars[bars["dt"].astype(str).str[:8] <= cut_day].copy()
    a = eng.day_features(prefix)
    b = eng.day_features(bars)          # 追加 20 天“未来”数据
    assert len(a["days"]) == cut and len(b["days"]) == len(days)
    for name in eng.FEAT_NAMES:
        np.testing.assert_allclose(a["features"][name], b["features"][name][:cut],
                                   equal_nan=True, rtol=0, atol=1e-12,
                                   err_msg=f"{name} 历史日被未来数据修改（PIT 违规）")


# ------------------------------------------------------------------ 3. 引擎 vs 暴力重算
def test_engine_matches_bruteforce_daily():
    bars = make_bars(45, seed=3)
    payload = eng.day_features(bars)
    engine_df = pd.DataFrame(payload["features"], index=payload["days"])
    dt = bars["dt"].astype(str)
    checked = 0
    for k, day in enumerate(payload["days"]):
        if k < 10:
            continue                        # 早期结构不足，NaN 语义可能不同，跳过
        trunc = bars[dt.str[:8] <= day]
        ref = eng._bruteforce_last_day_features(trunc)
        for name in eng.FEAT_NAMES:
            assert _nan_safe_equal(engine_df.loc[day, name], ref[name]), \
                f"{day} {name}: engine={engine_df.loc[day, name]} ref={ref[name]}"
        checked += 1
    assert checked >= 30


# ------------------------------------------------------------------ 4. 增量 == 全量
def test_incremental_equals_batch():
    bars = make_bars(30, seed=5)
    df = cf.norm_bars(bars)
    days = sorted(df["dt"].astype(str).str[:8].unique().tolist())
    engine = eng.ChanAsOfEngine()
    day_series = df["dt"].astype(str).str[:8]
    for day in days:
        engine.append_day(day, df[day_series == day])
    incremental = engine.to_payload()
    batch = eng.day_features(bars)
    assert incremental["days"] == batch["days"]
    for name in eng.FEAT_NAMES:
        np.testing.assert_allclose(incremental["features"][name], batch["features"][name],
                                   equal_nan=True)


# ------------------------------------------------------------------ 5. 抽样截断一致性
def test_verify_truncation_zero_mismatch():
    bars = make_bars(120, seed=9)
    payload = eng.day_features(bars)
    days = payload["days"]
    sample = [days[40], days[80], days[-1]]
    chk = eng.verify_truncation(bars, sample)
    assert chk["checked_code_days"] == 3
    for name in eng.FEAT_NAMES:
        assert chk["mismatch_rates"][name] == pytest.approx(0.0), name
        assert chk["pass_by_feature"][name] is True
    assert chk["pass"] is True
    # 目标阈值 ≤10%
    assert all(v is not None and v <= eng.PIT_MISMATCH_MAX for v in chk["mismatch_rates"].values())


# ------------------------------------------------------------------ 6. 边界
def test_edge_empty_and_short():
    empty = eng.day_features(pd.DataFrame())
    assert empty["days"] == [] and empty["raw_bars"] == 0
    assert all(len(empty["features"][n]) == 0 for n in eng.FEAT_NAMES)
    short = make_bars(2)
    out = eng.day_features(short)
    assert len(out["days"]) == 2
    # 结构不足 → 特征应为 0 / NaN，不抛异常
    assert np.isfinite(out["features"]["bi_count_20d"][-1])
    chk = eng.verify_truncation(short)
    assert chk["checked_code_days"] == 0 and chk["pass"] is False


# ------------------------------------------------------------------ 7. 与前筛脚本契约
def test_feature_contract_matches_prescreen():
    import chan_factor_prescreen as ps
    assert tuple(ps.FEAT_NAMES) == tuple(eng.FEAT_NAMES)
    assert ps.PIT_MISMATCH_MAX == pytest.approx(eng.PIT_MISMATCH_MAX)
    ps_labels = {f[0]: (f[1], f[2]) for f in ps.FEATURES}
    for name in eng.FEAT_NAMES:
        assert ps_labels[name] == (eng.FEATURE_LABELS[name], eng.FEATURE_FAMILIES[name])


# ------------------------------------------------------------------ 8. 真实数据：与旧前筛特征口径同义
@pytest.mark.skipif(not MINS.exists(), reason="stk_mins 数据根不存在")
def test_real_data_matches_v1_definition_on_truncation():
    import pyarrow.parquet as pq
    files = sorted(MINS.glob("000001_SZ_2026*.parquet"))[-4:]
    if not files:
        pytest.skip("000001.SZ 月度文件缺失")
    frames = [pq.read_table(f, columns=["trade_time", "open", "high", "low", "close", "vol",
                                        "amount"]).to_pandas() for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
    df = df[df["trade_time"].notna()]
    dates = sorted(df["trade_time"].dt.strftime("%Y%m%d").unique().tolist())[-60:]
    df = df[df["trade_time"].dt.strftime("%Y%m%d").isin(set(dates))]
    bars = cf.resample_30min(df)
    payload = eng.day_features(bars)
    assert len(payload["days"]) >= 20
    sample_days = [payload["days"][len(payload["days"]) // 2], payload["days"][-1]]
    dt = bars["dt"].astype(str)
    import chan_factor_prescreen as ps
    for day in sample_days:
        trunc = bars[dt.str[:8] <= day]
        struct = ps.build_structures(trunc)
        v1_last = ps.features_asof(struct)
        engine_row = pd.DataFrame(payload["features"], index=payload["days"]).loc[day]
        for name in eng.FEAT_NAMES:
            assert _nan_safe_equal(engine_row[name], v1_last[name][-1]), \
                f"{day} {name}: engine={engine_row[name]} v1={v1_last[name][-1]}"
