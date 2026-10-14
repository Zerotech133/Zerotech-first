# -*- coding: utf-8 -*-
"""任务B单测：成本分层 + CRYPTO T+0（backtest.load_trade_config / run t_plus_one）。

覆盖：分层读取 / 旧扁平 config 兼容回退 / CRYPTO T+0 shift 差异。
全部用假想 config dict 或合成 df，不读真实数据（纪律：不动 data/）。
"""
import os
import sys

import pytest

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest  # noqa: E402

# 假想新 config（分层结构，与本次 config.json 改版一致）
NEW_CFG = {
    "trade": {
        "init_cash": 1000000,
        "by_market": {
            "CN":     {"cost_bps": 12, "slippage_bps": 3},
            "US":     {"cost_bps": 5,  "slippage_bps": 1},
            "HK":     {"cost_bps": 5,  "slippage_bps": 1},
            "CRYPTO": {"cost_bps": 8,  "slippage_bps": 2},
            "T_plus_one_markets": ["CN", "US", "HK"],
        },
    }
}

# 假想旧 config（扁平结构，向后兼容回退路径）
OLD_CFG = {"trade": {"init_cash": 1000000, "cost_bps": 3, "slippage_bps": 1}}


def test_load_by_market_layered():
    """分层读取：各市场拿到各自成本；CN=12+3、CRYPTO=8+2 且 T+0。"""
    cn = backtest.load_trade_config("CN", cfg=NEW_CFG)
    assert cn["cost_bps"] == 12 and cn["slippage_bps"] == 3
    assert cn["t_plus_one"] is True and cn["init_cash"] == 1000000
    cr = backtest.load_trade_config("CRYPTO", cfg=NEW_CFG)
    assert cr["cost_bps"] == 8 and cr["slippage_bps"] == 2
    assert cr["t_plus_one"] is False          # CRYPTO 不在 T_plus_one_markets
    us = backtest.load_trade_config("US", cfg=NEW_CFG)
    assert us["cost_bps"] == 5 and us["t_plus_one"] is True
    # 拿不到市场（None/未知）→ 默认 CN 保守口径
    none_tc = backtest.load_trade_config(None, cfg=NEW_CFG)
    assert none_tc["cost_bps"] == 12 and none_tc["t_plus_one"] is True
    unk = backtest.load_trade_config("MARS", cfg=NEW_CFG)
    assert unk["cost_bps"] == 12 and unk["slippage_bps"] == 3
    # 返回 dict 可直接 ** 展开进 run()（含 t_plus_one，不含多余键）
    assert set(cn) == {"init_cash", "cost_bps", "slippage_bps", "t_plus_one"}


def test_load_legacy_flat_fallback():
    """旧 config 兼容回退：无 by_market → 旧行为（统一成本 + T+1）。"""
    tc = backtest.load_trade_config("CN", cfg=OLD_CFG)
    assert tc == {"init_cash": 1000000, "cost_bps": 3,
                  "slippage_bps": 1, "t_plus_one": True}
    tc2 = backtest.load_trade_config("CRYPTO", cfg=OLD_CFG)
    assert tc2["cost_bps"] == 3 and tc2["t_plus_one"] is True  # 旧口径不区分市场


def _synth_df(px_values, start="2024-01-01"):
    """合成 df（判例：Series 带默认 RangeIndex 时传 index= 会 reindex 成
    全 NaN 列，必须用 .values 构造）。"""
    n = len(px_values)
    return pd.DataFrame({"close": list(px_values)},
                        index=pd.date_range(start, periods=n, freq="B"))


def test_crypto_t0_shift_difference():
    """CRYPTO T+0：t_plus_one=False 当日生效（首日即持仓），与 T+1 净值有差异。"""
    n = 10
    px = [100 * (1.01 ** i) for i in range(n)]               # 单边上涨
    df = _synth_df(px)
    # 判例：day0 的 pct_change 恒为 0，shift 差异要在 day1 翻多才能体现
    sig = pd.Series(0, index=df.index)
    sig.iloc[1:] = 1                                         # day1 起持多
    m_t1 = backtest.run(df, sig, t_plus_one=True, cost_bps=3, slippage_bps=1)
    m_t0 = backtest.run(df, sig, t_plus_one=False, cost_bps=3, slippage_bps=1)
    # T+1：day1 信号 day2 才持仓（踏空 day1 的 1%）；T+0：day1 当日即持仓
    assert m_t1["position"].iloc[1] == 0
    assert m_t0["position"].iloc[1] == 1
    assert m_t0["total_return"] > m_t1["total_return"]       # 涨市少踏空一天
    # 经 load_trade_config(CRYPTO) 走完整链路等价于显式 t_plus_one=False
    # + CRYPTO 成本（8+2）
    tc = backtest.load_trade_config("CRYPTO", cfg=NEW_CFG)
    m_cr = backtest.run(df, sig, **tc)
    m_ref = backtest.run(df, sig, t_plus_one=False, cost_bps=8, slippage_bps=2)
    assert m_cr["position"].iloc[1] == m_ref["position"].iloc[1] == 1
    assert m_cr["total_return"] == m_ref["total_return"]


def test_real_config_loads():
    """真实 config.json（已分层）能被读取且口径正确——冒烟。"""
    tc = backtest.load_trade_config("CN")
    assert tc["cost_bps"] == 12 and tc["slippage_bps"] == 3
    assert tc["t_plus_one"] is True
    tc_cr = backtest.load_trade_config("CRYPTO")
    assert tc_cr["t_plus_one"] is False and tc_cr["cost_bps"] == 8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
