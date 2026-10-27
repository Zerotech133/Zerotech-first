# -*- coding: utf-8 -*-
"""vol targeting 单测（[20261014_voltarget]）：波动率目标仓位缩放。

覆盖：
① enabled 缺省/False 时与改动前（backtest.py.bak_20261014_vt）字节级等价；
② 高波动期缩仓、低波动期 scale 触 cap；
③ 无未来函数（改信号日之后的数据不影响信号日 scale）；
④ floor/cap 生效。
全部合成数据，不读真实数据（纪律：不动 data/）。
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest  # noqa: E402

BAK = os.path.join(HERE, "backtest.py.bak_20261014_vt")

VT_ON = {"enabled": True, "target_vol": 0.15, "lookback": 20, "floor": 0.25, "cap": 1.0}


def _load_bak():
    """加载改动前备份模块（.bak 扩展名需显式 SourceFileLoader）。"""
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("_backtest_bak_vt", BAK)
    spec = importlib.util.spec_from_file_location(
        "_backtest_bak_vt", BAK, loader=loader
    )
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _synth_df(px_values, start="2024-01-01"):
    """合成 df（判例：必须用 .values/list 构造，防 reindex 全 NaN 列）。"""
    n = len(px_values)
    return pd.DataFrame(
        {"close": list(px_values)}, index=pd.date_range(start, periods=n, freq="B")
    )


def _const_vol_px(n, daily_ret, seed=7, sigma=0.02):
    """给定日收益率均值/波动的确定性价格序列（判例可复算）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(daily_ret, sigma, n)
    return list(100.0 * np.cumprod(1 + rets))


# ── ① 关闭时与改动前完全一致 ────────────────────────────────
def test_disabled_matches_preglobal():
    """enabled 缺省/False：多组合成序列上与改动前备份实现逐指标、
    逐笔交易、净值序列完全一致（字节级等价）。"""
    bak = _load_bak()
    cases = [
        _const_vol_px(120, 0.001, seed=1),
        [100 * (1.01**i) for i in range(60)],  # 单边上涨
        [100 - 0.5 * i for i in range(80)],  # 单边下跌
        [100, 105, 98, 110, 95, 120, 90, 130] * 10,  # 高频震荡
    ]
    for px in cases:
        df = _synth_df(px)
        sig = pd.Series((np.arange(len(px)) % 17 < 9).astype(int), index=df.index)
        for t1 in (True, False):
            m_new = backtest.run(df, sig, t_plus_one=t1)
            m_old = bak.run(df, sig, t_plus_one=t1)
            for k in (
                "total_return",
                "cagr",
                "annual_vol",
                "sharpe",
                "sortino",
                "max_drawdown",
                "calmar",
                "win_rate",
                "trades",
                "max_loss_streak",
                "bar_count",
            ):
                assert m_new[k] == m_old[k], (k, px[:3], t1)
            assert (m_new["equity"] == m_old["equity"]).all()
            assert (m_new["net_ret"] == m_old["net_ret"]).all()
            assert (m_new["position"] == m_old["position"]).all()
            assert m_new["trade_list"] == m_old["trade_list"]


def test_vol_target_key_absent_defaults_off():
    """旧 config 无 backtest.vol_target 键 → load 回退默认=关闭。"""
    vt = backtest.load_vol_target_config(cfg={})
    assert vt["enabled"] is False
    assert vt["target_vol"] == 0.15 and vt["lookback"] == 20
    assert vt["floor"] == 0.25 and vt["cap"] == 1.0
    # 半缺键：逐项回退默认
    vt2 = backtest.load_vol_target_config(
        cfg={"backtest": {"vol_target": {"enabled": True}}}
    )
    assert vt2["target_vol"] == 0.15 and vt2["floor"] == 0.25


def test_disabled_explicit_key_unchanged():
    """config 有 vol_target 但 enabled=False：结果与不传完全一致。"""
    px = _const_vol_px(120, 0.001, seed=3)
    df = _synth_df(px)
    sig = pd.Series((np.arange(len(px)) % 13 < 6).astype(int), index=df.index)
    m_off = backtest.run(df, sig, vol_target=dict(VT_ON, enabled=False))
    m_none = backtest.run(df, sig)
    assert m_off["total_return"] == m_none["total_return"]
    assert (m_off["position"] == m_none["position"]).all()
    assert m_off["trade_list"] == m_none["trade_list"]


# ── ② 高波动缩仓 / 低波动触 cap ─────────────────────────────
def test_high_vol_scales_down_low_vol_hits_cap():
    """高波动期 scale<1（降仓），极低波动期 scale 触 cap（不放大）。
    同日收益均值、不同波动：满仓低波组合净值应高于缩仓高波组合
    （同期收益同向，差异完全来自仓位差）。"""
    n = 60
    hi = _synth_df(_const_vol_px(n, 0.001, seed=11))  # 日波动 ~2%
    lo = _synth_df(_const_vol_px(n, 0.001, seed=12, sigma=0.0005))  # ~0.05%
    ones_hi = pd.Series(1, index=hi.index)
    ones_lo = pd.Series(1, index=lo.index)
    m_hi = backtest.run(hi, ones_hi, vol_target=VT_ON)
    m_lo = backtest.run(lo, ones_lo, vol_target=VT_ON)
    # 高波动：窗口充足后的持仓日 scale 应明显 < 1
    held_hi = m_hi["position"].iloc[25:]
    assert (held_hi < 1.0).all() and (held_hi > 0).all()
    # 近零波动：target/realized 巨大 → 触 cap=1.0（只降仓不加杠杆）
    held_lo = m_lo["position"].iloc[25:]
    assert (held_lo == 1.0).all()
    # 同收益均值下，低波动满仓 > 高波动缩仓（差额来自仓位）
    assert m_lo["total_return"] > m_hi["total_return"]


# ── ③ 无未来函数 ────────────────────────────────────────────
def test_no_lookahead_scale_uses_only_past_window():
    """波动率突变合成数据：改信号日之后的价格不影响信号日 scale——
    scale(t) 只由截至 t 的 lookback 窗决定（与引擎 shift(1) 叠加后无未来）。"""
    # 前 30 日完全一致的平静段；之后 A 段 5% 大波动、B 段 2% 小波动
    calm = [100 * (1.001**i) for i in range(30)]
    updn = [1, -1] * 25
    px_a = calm + list(100 * np.cumprod(1 + np.array(updn) * 0.05))
    px_b = calm + list(100 * np.cumprod(1 + np.array(updn) * 0.02))
    assert px_a[:30] == px_b[:30]
    df_a, df_b = _synth_df(px_a), _synth_df(px_b)
    s_a = backtest._vol_scale(df_a["close"], VT_ON)
    s_b = backtest._vol_scale(df_b["close"], VT_ON)
    # 信号日 < 30（窗口只含平静段）的 scale 必须完全一致——
    # 尽管第 30 日之后的波动路径天差地别，不影响此前任何信号日
    for i in range(30):
        assert s_a.iloc[i] == s_b.iloc[i], i
    # 窗口吃进突变段后（i≥30+lookback）scale 应不同 → 证明测试灵敏、
    # 机制真实生效（不是恒等假阳性）
    assert not np.allclose(s_a.iloc[50:60], s_b.iloc[50:60])
    # 端到端：突变前的持仓（pos 已 shift(1)，含 index30）与突变前的
    # 净收益不受未来突变影响（index30 的收益本身两边数据不同，不比）
    sig = pd.Series(1, index=df_a.index)
    m_a = backtest.run(df_a, sig, vol_target=VT_ON)
    m_b = backtest.run(df_b, sig, vol_target=VT_ON)
    assert (m_a["position"].iloc[:31] == m_b["position"].iloc[:31]).all()
    assert (m_a["net_ret"].iloc[:30] == m_b["net_ret"].iloc[:30]).all()


# ── ④ floor / cap 生效 ──────────────────────────────────────
def test_floor_and_cap_bounds():
    """极端波动：scale 被 floor 托底、被 cap 封顶。"""
    # 超高波动 → target/rv 远小于 floor → scale 恰为 floor
    wild = np.random.default_rng(2).normal(0, 0.08, 60)
    df_hi = _synth_df(list(100 * np.cumprod(1 + wild)))
    s_hi = backtest._vol_scale(df_hi["close"], VT_ON).iloc[25:]
    assert np.allclose(s_hi, 0.25)
    # 超低波动 → 触 cap
    df_lo = _synth_df([100 * (1.00001**i) for i in range(60)])
    s_lo = backtest._vol_scale(df_lo["close"], VT_ON).iloc[25:]
    assert np.allclose(s_lo, 1.0)
    # 自定义 cap=0.8：低波动也不得超过 0.8（不杠杆）
    vt_cap08 = dict(VT_ON, cap=0.8, floor=0.1)
    s_c = backtest._vol_scale(df_lo["close"], vt_cap08).iloc[25:]
    assert np.allclose(s_c, 0.8)
    # cap 生效于 run()：cap<1 时持仓日 pos == cap
    m_c = backtest.run(df_lo, pd.Series(1, index=df_lo.index), vol_target=vt_cap08)
    assert np.allclose(m_c["position"].iloc[1:25], 0.8)
    # floor 生效于 run()：高波动持仓日 pos == floor
    m_f = backtest.run(df_hi, pd.Series(1, index=df_hi.index), vol_target=VT_ON)
    assert np.allclose(m_f["position"].iloc[25:], 0.25)


def test_scaled_trade_pnl_proportional():
    """单笔收益按缩放后仓位折算：pnl = 平均仓位 × (出价/入价-1)。"""
    df_lo = _synth_df([100 * (1.00001**i) for i in range(60)])
    vt = dict(VT_ON, cap=0.8, floor=0.1)
    sig = pd.Series(1, index=df_lo.index)
    m = backtest.run(df_lo, sig, vol_target=vt)
    tl = m["trade_list"]
    assert len(tl) == 1  # 全程持仓一笔
    raw = tl[0]["out_px"] / tl[0]["in_px"] - 1.0
    assert abs(tl[0]["pnl"] - 0.8 * raw) < 1e-12
    # 成本按名义金额计：缩放后换手成本比例不变（0→0.8 一进一出两段换手）
    # 等价验证：cap=1.0 与 cap=0.8 的成本差 = 缩放幅度 × 单位成本
    m_full = backtest.run(df_lo, sig, vol_target=VT_ON)
    # 全程持仓且 scale 恒定 → 换手只有建仓一次：0.8 vs 1.0
    # （净值差完全来自仓位差，非成本口径漂移——粗校验方向即可）
    assert m_full["final_value"] > m["final_value"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
