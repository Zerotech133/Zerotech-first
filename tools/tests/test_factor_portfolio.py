"""组合回测（中性化 book_to_market）测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

from factor_lib import Panel  # noqa: E402
from factor_portfolio import (  # noqa: E402
    PortfolioConfig,
    _target_weights,
    investable_masks,
    rebalance_indices,
    simulate,
)


def _panel(n_days: int = 80, n_codes: int = 60, seed: int = 7) -> tuple[Panel, dict]:
    rng = np.random.default_rng(seed)
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n_days)]
    codes = [f"{i:06d}.SZ" for i in range(n_codes)]
    rets = rng.normal(0, 0.01, (n_days, n_codes))
    close = pd.DataFrame(np.cumprod(1 + rets, axis=0) * 10, index=dates, columns=codes)
    adj = close.copy()
    pct = close.pct_change().fillna(0) * 100
    vol = pd.DataFrame(1e6, index=dates, columns=codes)
    amount = pd.DataFrame(2e6, index=dates, columns=codes)  # 千元，20 亿元
    turnover = pd.DataFrame(2.5, index=dates, columns=codes)  # 2.5%
    total_mv = pd.DataFrame(1e6, index=dates, columns=codes)
    factor = pd.DataFrame(
        np.tile(np.arange(n_codes, dtype=float), (n_days, 1)), index=dates, columns=codes
    )
    fields = {"close": close, "adj_close": adj, "pct_chg": pct, "vol": vol,
              "amount": amount, "turnover_rate": turnover, "total_mv": total_mv}
    panel = Panel(fields, eligible=pd.DataFrame(True, index=dates, columns=codes),
                  meta={"industry": pd.Series("ind0", index=codes)})
    return panel, {c: "正常股份" for c in codes}


def test_rebalance_indices():
    panel, _ = _panel(n_days=50)
    idx = rebalance_indices(panel, None, None, 20)
    assert idx == [0, 20, 40]


def test_investable_masks_excludes_st_limit_suspend_low_amount_low_turnover():
    panel, names = _panel(n_days=5, n_codes=10)
    names[panel.codes[0]] = "ST测试"
    panel.fields["pct_chg"].iloc[1, 1] = 9.9
    panel.fields["vol"].iloc[1, 2] = 0
    panel.fields["amount"].iloc[1, 3] = 1e3
    panel.fields["turnover_rate"].iloc[1, 4] = 0.01
    sel, trade = investable_masks(panel, names, PortfolioConfig())
    assert not sel.iloc[1, 0]
    assert not sel.iloc[1, 1]
    assert not sel.iloc[1, 2]
    assert not sel.iloc[1, 3]
    assert not sel.iloc[1, 4]
    assert bool(trade.iloc[1, 2]) is False


def test_simulate_respects_turnover_cap_and_costs():
    panel, names = _panel(n_days=80, n_codes=60)
    cfg = PortfolioConfig(rebalance_days=20, quantile=0.2, max_one_way_turnover=0.10,
                          cost_bp=30.0)
    factor = pd.DataFrame(
        np.tile(np.arange(60, dtype=float), (80, 1)), index=panel.dates, columns=panel.codes
    )
    out = simulate(panel, factor, cfg, names=names)
    assert out["metrics"]["rebalances"] >= 2
    assert all(t["actual_turnover"] <= 0.10 + 1e-12 for t in out["trades"])
    assert out["metrics"]["total_cost"] > 0
    assert len(out["nav"]) > 0
    assert np.isfinite(out["nav"]).all()
    assert out["metrics"]["sharpe"] is not None


def test_simulate_no_lookahead_first_trade_uses_first_signal():
    panel, names = _panel(n_days=80)
    factor = pd.DataFrame(
        np.tile(np.arange(60, dtype=float), (80, 1)), index=panel.dates, columns=panel.codes
    )
    out = simulate(panel, factor, PortfolioConfig(rebalance_days=20), names=names)
    first = out["trades"][0]
    assert first["signal_date"] == panel.dates[0]
    assert first["entry_date"] == panel.dates[1]
    assert first["n_long"] > 0 and first["n_short"] > 0


def test_weights_gross_and_target_quantiles():
    panel, names = _panel(n_days=80)
    factor = pd.DataFrame(
        np.tile(np.arange(60, dtype=float), (80, 1)), index=panel.dates, columns=panel.codes
    )
    out = simulate(panel, factor, PortfolioConfig(rebalance_days=20), names=names)
    first = out["trades"][0]
    assert first["n_long"] == first["n_short"] == 12
    assert abs(first["target_turnover"] - 0.5) < 1e-9  # 首次建仓 0.5 单边


def test_buffer_keeps_existing_positions_within_extended_quantile():
    codes = pd.Index([f"{i:06d}.SZ" for i in range(100)])
    factor = pd.Series(np.arange(100, 0, -1, dtype=float), index=codes)  # code0 最高
    mask = pd.Series(True, index=codes)
    prior = pd.Series(0.0, index=codes)
    prior.iloc[24] = 0.025   # rank 25，处于 top 20%~30% 之间
    prior.iloc[75] = -0.025  # rank 76，处于 bottom 20%~30% 之间
    target = _target_weights(factor, mask, PortfolioConfig(buffer_quantile=0.3), prior=prior)
    assert target.iloc[24] > 0          # 原多头保留
    assert target.iloc[75] < 0          # 原空头保留
    assert target.iloc[22] == 0         # 新标的仍只允许 top 20%
    assert abs(float(target.abs().sum()) - 1.0) < 1e-9


def test_turnover_penalty_reduces_turnover_and_keeps_gross():
    panel, names = _panel(n_days=80)
    factor = pd.DataFrame(
        np.tile(np.arange(60, dtype=float), (80, 1)), index=panel.dates, columns=panel.codes
    )
    plain = simulate(panel, factor, PortfolioConfig(rebalance_days=20, turnover_penalty=0.0),
                     names=names)
    penalized = simulate(panel, factor, PortfolioConfig(rebalance_days=20, turnover_penalty=0.6),
                         names=names)
    plain_turn = sum(t["actual_turnover"] for t in plain["trades"])
    pen_turn = sum(t["actual_turnover"] for t in penalized["trades"])
    assert pen_turn <= plain_turn + 1e-9
    assert all(abs(t["gross"] - 1.0) < 1e-6 for t in penalized["trades"])


def test_60_day_rebalance_indices_and_simulation():
    panel, names = _panel(n_days=80)
    assert rebalance_indices(panel, None, None, 60) == [0, 60]
    factor = pd.DataFrame(
        np.tile(np.arange(60, dtype=float), (80, 1)), index=panel.dates, columns=panel.codes
    )
    out = simulate(panel, factor, PortfolioConfig(rebalance_days=60), names=names)
    assert out["metrics"]["rebalances"] == 2
    assert out["metrics"]["rebalance_days"] == 60
