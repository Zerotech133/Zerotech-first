# -*- coding: utf-8 -*-
"""tools/tests/test_learning_factors.py · W2 学习因子（8 个日线级）单元测试。

覆盖（任务验收 ≥8）：
  1. 注册表完整（8 个因子、纯函数可调用、PIT 标记正确）；
  2. 均线粘合度：横盘≈0、趋势>0、窗口不足 NaN、DataFrame 向量化一致；
  3. 多头排列强度 + 横截面分位；
  4. MACD 背离强度：与公式手工值一致、有界、无波动→NaN；
  5. 放量突破幅度：构造平台突破精确值、未突破为负、早期 NaN；
  6. 平台整理强度：横盘≈1、趋势/宽幅整理低分、有界、NaN；
  7. 筹码集中度：等权分布手工值、集中分布更小、空表/缺列降级为空；
  8. 涨停后行为：手工 N 日收益/最大回落/次日、样本不足 NaN 行、空事件降级；
  9. 资金流强度：单位归一后手工值、零成交额/窗口不足 NaN；
 10. compute_factor_frame 缺数据降级：无 moneyflow/limit/cyq → 对应列全 NaN、流程不抛异常。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_learning_factors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = ROOT / "quant_backtest" / "factor_lib"
sys.path.insert(0, str(MODULE_DIR))

import learning_factors as lf  # noqa: E402


def make_dates(n: int) -> pd.Index:
    return pd.Index(pd.bdate_range("2026-01-05", periods=n).strftime("%Y%m%d"))


# ------------------------------------------------------------------ 1. 注册表
def test_registry_complete():
    assert len(lf.LEARNING_FACTOR_SPECS) == 8
    assert len(lf.FACTORS) == 8 and len(set(lf.FACTORS)) == 8
    expected = {
        "ma_convergence", "bull_alignment_strength", "macd_divergence_strength",
        "volume_breakout_strength", "platform_consolidation_score", "chip_concentration",
        "post_limit_behavior", "moneyflow_strength",
    }
    assert set(lf.FACTORS) == expected
    rows = lf.factor_rows()
    assert len(rows) == 8
    for spec in lf.LEARNING_FACTOR_SPECS.values():
        assert callable(getattr(lf, spec.func_name))
        assert spec.formula.strip()
        assert spec.data_deps
        assert spec.direction in (-1, 0, 1)
        assert spec.pit_safe is (spec.name != "post_limit_behavior")
    assert lf.LEARNING_FACTOR_SPECS["post_limit_behavior"].pit_safe is False


# ------------------------------------------------------------------ 2. 均线粘合度
def test_ma_convergence():
    n = 80
    idx = make_dates(n)
    flat = pd.Series(10.0, index=idx)
    conv = lf.ma_convergence(flat)
    assert np.isnan(conv.iloc[:59]).all()          # MA60 不足 → NaN
    assert conv.iloc[70] == pytest.approx(0.0, abs=1e-12)

    trend = pd.Series(10.0 * (1.01 ** np.arange(n)), index=idx)
    conv2 = lf.ma_convergence(trend)
    assert np.isfinite(conv2.iloc[70]) and conv2.iloc[70] > 0.01

    frame = pd.DataFrame({"A": flat, "B": trend})
    out = lf.ma_convergence(frame)
    assert list(out.columns) == ["A", "B"]
    np.testing.assert_allclose(out["A"].to_numpy(), conv.to_numpy(), equal_nan=True)
    np.testing.assert_allclose(out["B"].to_numpy(), conv2.to_numpy(), equal_nan=True)


# ------------------------------------------------------------------ 3. 多头排列 + 分位
def test_bull_alignment_and_percentile():
    n = 80
    idx = make_dates(n)
    up = pd.Series(np.linspace(10.0, 20.0, n), index=idx)      # MA5 > MA60 → 正
    down = pd.Series(np.linspace(20.0, 10.0, n), index=idx)    # MA5 < MA60 → 负
    assert lf.bull_alignment_strength(up).iloc[-1] > 0
    assert lf.bull_alignment_strength(down).iloc[-1] < 0
    assert np.isnan(lf.bull_alignment_strength(up).iloc[:59]).all()

    frame = pd.DataFrame({"A": up, "B": down, "C": pd.Series(15.0, index=idx)})
    pct = lf.cross_section_percentile(frame)
    # 最后一日：A 涨最多 → 分位约 1；B 最低 → 约 1/3
    assert pct.iloc[-1, 0] > pct.iloc[-1, 2] > pct.iloc[-1, 1]
    assert 0.0 <= pct.iloc[-1].min() <= pct.iloc[-1].max() <= 1.0


# ------------------------------------------------------------------ 4. MACD 背离
def test_macd_divergence_strength():
    n = 200
    idx = make_dates(n)
    rng = np.random.default_rng(7)
    close = pd.Series(10 + np.cumsum(rng.normal(0.05, 0.3, n)), index=idx)
    div = lf.macd_divergence_strength(close)
    assert np.isnan(div.iloc[:59]).all()
    assert np.isfinite(div.iloc[100]) and -1.0 <= div.iloc[100] <= 1.0

    # 公式身份：背离 = 价格区间分位 − DIF 区间分位
    dif = close.ewm(span=12, adjust=False, min_periods=1).mean() - \
        close.ewm(span=26, adjust=False, min_periods=1).mean()
    def pos(s):
        return (s - s.rolling(60, min_periods=60).min()) / (s.rolling(60, min_periods=60).max() - s.rolling(60, min_periods=60).min())
    expected = pos(close) - pos(dif)
    np.testing.assert_allclose(div.iloc[-20:], expected.iloc[-20:], rtol=1e-10, atol=1e-12)

    # 横盘无波动 → 区间为 0 → NaN
    flat = pd.Series(10.0, index=idx)
    assert np.isnan(lf.macd_divergence_strength(flat).iloc[100])


# ------------------------------------------------------------------ 5. 放量突破
def test_volume_breakout_strength():
    n = 80
    idx = make_dates(n)
    close = pd.Series(100.0, index=idx)
    vol = pd.Series(1000.0, index=idx)
    # 第 70 日：收盘 105（突破前 60 日高点 100，幅度 5%），量 3000（量比 3）
    close.iloc[70] = 105.0
    vol.iloc[70] = 3000.0
    out = lf.volume_breakout_strength(close, vol)
    assert np.isnan(out.iloc[:60]).all()
    vol_ma50 = (49 * 1000.0 + 3000.0) / 50.0   # 含当日 spike
    assert out.iloc[70] == pytest.approx(3000.0 / vol_ma50 * 0.05, rel=1e-9)
    # 未突破：收盘低于平台高点 → 突破幅度为负
    close2 = close.copy()
    close2.iloc[70] = 95.0
    assert lf.volume_breakout_strength(close2, vol).iloc[70] < 0


# ------------------------------------------------------------------ 6. 平台整理
def test_platform_consolidation_score():
    n = 160
    idx = make_dates(n)
    flat = pd.Series(10.0, index=idx)
    score_flat = lf.platform_consolidation_score(flat)
    assert np.isnan(score_flat.iloc[:70]).all()                     # 80 日滚动历史未齐 → NaN
    assert score_flat.iloc[-1] == pytest.approx(1.0, abs=1e-9)      # 完全横盘→最高分

    trend = pd.Series(np.linspace(10.0, 30.0, n), index=idx)        # 单边上涨
    score_trend = lf.platform_consolidation_score(trend)
    assert score_trend.iloc[-1] < 0.3
    valid = score_trend.dropna()
    assert valid.between(0.0, 1.0).all()
    assert score_trend.iloc[-1] < score_flat.iloc[-1]


# ------------------------------------------------------------------ 7. 筹码集中度
def test_chip_concentration():
    df = pd.DataFrame({
        "trade_date": ["20260901"] * 4,
        "price": [1.0, 2.0, 3.0, 4.0],
        "percent": [25.0, 25.0, 25.0, 25.0],
    })
    out = lf.chip_concentration(df)
    # 累积筹码分位：p5=1（首个累积点起）、p50=2、p95=3.8 → (3.8−1)/2 = 1.4
    assert out.loc["20260901"] == pytest.approx(1.4, rel=1e-9)

    # 筹码高度集中在 10.0~10.2 → 宽度/中位 很小
    df2 = pd.DataFrame({
        "trade_date": ["20260902"] * 3,
        "price": [10.0, 10.1, 10.2],
        "percent": [40.0, 40.0, 20.0],
    })
    out2 = lf.chip_concentration(df2)
    assert out2.loc["20260902"] < 0.05

    # 降级：空表 / 缺列 → 空 Series
    assert lf.chip_concentration(pd.DataFrame()).empty
    assert lf.chip_concentration(pd.DataFrame({"price": [1.0]})).empty


# ------------------------------------------------------------------ 8. 涨停后行为
def test_post_limit_behavior():
    idx = make_dates(20)
    close = pd.Series(10.0, index=idx)
    future = [11.0, 10.5, 12.0, 11.5, 11.0]
    close.iloc[11:16] = future
    flags = pd.Series(False, index=idx)
    flags.iloc[10] = True
    out = lf.post_limit_behavior(close, flags, n=5)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["ret_n"] == pytest.approx(11.0 / 10.0 - 1.0)          # 涨停后 5 日累计
    assert row["max_drawdown_n"] == pytest.approx(10.5 / 10.0 - 1.0)  # N 日内最大回落
    assert row["next_day_ret"] == pytest.approx(11.0 / 10.0 - 1.0)
    assert row["n_future"] == 5
    # 样本不足：事件在末尾 → 该类字段 NaN，但事件保留
    flags2 = pd.Series(False, index=idx)
    flags2.iloc[18] = True
    out2 = lf.post_limit_behavior(close, flags2, n=5)
    assert len(out2) == 1 and np.isnan(out2.iloc[0]["ret_n"])
    # 无事件 → 空 DataFrame
    assert lf.post_limit_behavior(close, pd.Series(False, index=idx)).empty


# ------------------------------------------------------------------ 9. 资金流强度
def test_moneyflow_strength():
    idx = make_dates(10)
    net = pd.Series(100.0, index=idx)      # 万元
    amount = pd.Series(1000.0, index=idx)  # 千元
    out = lf.moneyflow_strength(net, amount)
    assert np.isnan(out.iloc[:4]).all()                          # 5 日均不足
    # net_mf×10 = 1000 千元 / amount_ma 1000 = 1.0
    assert out.iloc[5] == pytest.approx(1.0, rel=1e-9)
    # 零成交额 → NaN
    assert np.isnan(lf.moneyflow_strength(net, pd.Series(0.0, index=idx)).iloc[5])


# ------------------------------------------------------------------ 10. 缺数据降级
def test_compute_factor_frame_degraded():
    """无 moneyflow/limit/cyq 时，对应因子列必须全 NaN 且不抛异常（降级记录见报告）。"""
    n, m = 130, 3
    idx = make_dates(n)
    cols = [f"00000{i}.SZ" for i in range(1, m + 1)]
    rng = np.random.default_rng(3)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0, 0.1, (n, m)), axis=0), index=idx, columns=cols)
    sample = {
        "fields": {"close": close,
                   "vol": pd.DataFrame(1000.0, index=idx, columns=cols),
                   "amount": pd.DataFrame(1e5, index=idx, columns=cols),
                   "pre_close": close, "pct_chg": close.pct_change() * 100},
        "window_dates": [str(d) for d in idx[-10:]],
        "all_dates": [str(d) for d in idx],
        "codes": cols, "data_end": str(idx[-1]), "data_root": "synthetic",
        "moneyflow": pd.DataFrame(), "limit_up": pd.DataFrame(), "cyq": pd.DataFrame(),
        "sources": {},
    }
    computed = lf.compute_factor_frame(sample)
    mats = computed["matrices"]
    assert set(mats) == set(lf.FACTORS) - {"post_limit_behavior"}   # 后者输出事件表
    # 无 cyq / moneyflow → 全 NaN；涨停事件表为空
    assert mats["chip_concentration"].isna().all().all()
    assert mats["moneyflow_strength"].isna().all().all()
    assert computed["post_limit_behavior"].empty
    # 其余日线因子在窗口上有值（历史充足）
    assert mats["ma_convergence"].iloc[-1].notna().all()

    # 提供 cyq 时：仅对应代码有值（覆盖有限但不崩）
    cyq = pd.DataFrame({"trade_date": [str(idx[-1])] * 3, "price": [10.0, 10.1, 10.2],
                        "percent": [40.0, 40.0, 20.0], "ts_code": [cols[0]] * 3})
    sample["cyq"] = cyq
    computed2 = lf.compute_factor_frame(sample)
    chip = computed2["matrices"]["chip_concentration"]
    assert chip.iloc[-1][cols[0]] == pytest.approx(
        lf.chip_concentration(cyq).iloc[0], rel=1e-9)
    assert chip[cols[1]].isna().all()


# ------------------------------------------------------------------ 11. 数据根缺失
def test_sample_factors_missing_data_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        lf.sample_factors(data_root=tmp_path / "no_such_root", n_days=5, n_codes=5)
