"""tools/tests/test_alpha101_engine.py · Alpha101 表达式引擎单测。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_alpha101_engine.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import alpha101_engine as ae  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402
from factor_lib import FACTORS, Panel  # noqa: E402


def _synth_panel(days: int = 60, codes: int = 6) -> Panel:
    rng = np.random.default_rng(7)
    dates = pd.Index(pd.date_range("2026-01-01", periods=days, freq="B").strftime("%Y%m%d"))
    cols = pd.Index([f"00000{i}.SZ" for i in range(1, codes + 1)])
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0, .05, (days, codes)), axis=0),
                         index=dates, columns=cols)
    open_ = close * (1 + rng.normal(0, .005, (days, codes)))
    high = np.maximum(close, open_) * 1.01
    low = np.minimum(close, open_) * .99
    vol = pd.DataFrame(rng.uniform(1e4, 5e5, (days, codes)), index=dates, columns=cols)
    amount = vol * close / 10
    fields = {
        "open": open_, "high": high, "low": low, "close": close,
        "pre_close": close.shift(1).fillna(10),
        "pct_chg": close.pct_change().fillna(0) * 100,
        "vol": vol, "amount": amount, "adj_close": close,
        "total_mv": pd.DataFrame(1e6, index=dates, columns=cols),
        "pe_ttm": pd.DataFrame(12.0, index=dates, columns=cols),
        "turnover_rate": pd.DataFrame(1.0, index=dates, columns=cols),
    }
    meta = {"industry": pd.Series(["银行", "地产", "银行", "科技", "地产", "科技"][:codes],
                                  index=cols)}
    return Panel(fields, meta=meta)


def test_formula_file_count_and_names():
    formulas = ae.load_formulas()
    assert len(formulas) == 101
    alpha_names = {n for n, e in ft.TUSHARE_FACTOR_MAP.items()
                   if e["factor_type"] == "Alpha101"}
    assert set(formulas) == alpha_names
    assert all(isinstance(f, str) and f.strip() for f in formulas.values())
    for name in alpha_names:
        assert ft.TUSHARE_FACTOR_MAP[name]["status"] == "implemented"


def test_parser_basic_formula_matches_pandas():
    panel = _synth_panel()
    out = ae.evaluate_formula("(Close - Open) / (High - Low + 0.001)", panel)
    expected = (panel.close - panel.open) / (panel.high - panel.low + 0.001)
    pd.testing.assert_frame_equal(out, expected, check_dtype=False, check_names=False)


def test_ternary_and_delay_parse():
    panel = _synth_panel()
    out = ae.evaluate_formula("Close > Delay(Close, 1) ? 1 : -1", panel)
    assert out.shape == panel.close.shape
    tail = out.iloc[2:].to_numpy(float)
    assert np.isfinite(tail).all()
    assert set(np.unique(tail)).issubset({-1.0, 1.0})


def test_all_101_formulas_evaluate_on_synthetic_panel():
    panel = _synth_panel()
    bad = {}
    for name, formula in ae.load_formulas().items():
        try:
            out = ae.evaluate_formula(formula, panel)
            assert out.shape == panel.close.shape, (name, out.shape)
            assert not np.isinf(out.to_numpy(float)).any(), name
        except Exception as exc:  # noqa: BLE001
            bad[name] = f"{type(exc).__name__}: {exc}"
    assert not bad, bad


def test_register_alpha101_idempotent():
    registry: dict = {}

    def register(name, family, kind, direction, description, source="", requires=()):
        def deco(fn):
            registry[name] = {
                "name": name, "family": family, "kind": kind,
                "direction": direction, "description": description,
                "source": source, "requires": requires, "fn": fn,
            }
            return fn
        return deco

    first = ft.register_batch(register, registry)
    second = ft.register_batch(register, registry)
    assert first == 187
    assert second == 0
    assert len(registry) == 187
    assert {"alpha101_1", "alpha101_101"} <= set(registry)
    assert registry["alpha101_1"]["family"] == "Tushare-Alpha101"
    assert registry["alpha101_1"]["kind"] == "continuous"


def test_global_registry_contains_alpha101():
    assert {"alpha101_1", "alpha101_101"} <= set(FACTORS)
    assert len(FACTORS) == 204
    spec = FACTORS["alpha101_1"]
    assert spec.family == "Tushare-Alpha101"
    assert spec.source == ft.SOURCE


def test_formulas_have_no_negative_lag():
    engine_src = (ROOT / "tools" / "research" / "alpha101_engine.py").read_text(encoding="utf-8")
    assert "shift(-" not in engine_src
    for name, formula in ae.load_formulas().items():
        assert "Delay(" not in formula or ",-" not in formula.replace(" ", ""), name


def test_empty_panel_formula_returns_empty():
    empty = Panel.empty()
    out = ae.evaluate_formula("Rank(Close)", empty)
    assert out.empty
