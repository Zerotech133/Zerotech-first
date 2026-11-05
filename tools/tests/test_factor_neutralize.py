"""行业+市值中性化测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib  # noqa: E402
from factor_lib import Panel, evaluate_panel, FACTORS  # noqa: E402
from factor_neutralize import (
    build_neutralized_specs,
    neutralize_matrix,
    parse_modes,
)


def _panel(n_days: int = 20, n_codes: int = 30, seed: int = 7) -> Panel:
    rng = np.random.default_rng(seed)
    dates = [f"2025-01-{i + 1:02d}" for i in range(n_days)]
    codes = [f"C{i:02d}" for i in range(n_codes)]
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, (n_days, n_codes)), axis=0) * 10,
        index=dates, columns=codes,
    )
    pct = close.pct_change().fillna(0) * 100
    log_mv = pd.DataFrame(
        rng.normal(10, 1.0, (n_days, n_codes)), index=dates, columns=codes,
    )
    total_mv = np.exp(log_mv)
    industry = pd.Series(
        [f"ind{i % 3}" for i in range(n_codes)], index=codes, dtype=object,
    )
    fields = {"close": close, "adj_close": close, "pct_chg": pct, "total_mv": total_mv}
    meta = {"industry": industry}
    return Panel(fields, eligible=None, meta=meta)


def _factor_with_industry_size(panel: Panel) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    ind_code = {f"ind{i}": i for i in range(3)}
    ind_effect = pd.Series(panel.load_meta["industry"].map(ind_code).astype(float))
    mean_effect = float(ind_effect.mean())
    log_mv = np.log(panel.fields["total_mv"])
    noise = rng.normal(0, 0.1, size=panel.shape)
    return (ind_effect - mean_effect).to_numpy()[None, :] + 2.0 * log_mv.to_numpy() + noise


def test_neutralize_removes_size_and_industry_exposure():
    panel = _panel()
    raw = pd.DataFrame(_factor_with_industry_size(panel), index=panel.dates, columns=panel.codes)
    out, info = neutralize_matrix(panel, raw, industry=True, size=True, min_obs=10)
    assert info["n_days"] == len(panel.dates)
    assert info["modes"] == ["industry", "size"]
    log_mv = np.log(panel.fields["total_mv"])
    for dt in panel.dates:
        y = out.loc[dt].to_numpy(dtype=float)
        x = log_mv.loc[dt].to_numpy(dtype=float)
        mask = np.isfinite(y) & np.isfinite(x)
        assert abs(np.corrcoef(y[mask], x[mask])[0, 1]) < 1e-8
        ind = panel.load_meta["industry"]
        means = pd.Series(y, index=panel.codes)[mask].groupby(ind[mask]).mean()
        assert means.abs().max() < 1e-8


def test_neutralize_min_obs_produces_nan():
    panel = _panel(n_days=3, n_codes=5)
    raw = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
    raw.iloc[0, :2] = 1.0
    out, info = neutralize_matrix(panel, raw, industry=True, size=True, min_obs=10)
    assert info["n_days"] == 0
    assert out.isna().all().all()


def test_neutralize_missing_industry_and_size():
    panel = _panel(n_days=5, n_codes=10)
    ind = panel.load_meta["industry"].copy()
    ind.iloc[0] = None
    panel.load_meta["industry"] = ind
    raw = pd.DataFrame(
        np.linspace(0, 1, panel.shape[0] * panel.shape[1]).reshape(panel.shape),
        index=panel.dates, columns=panel.codes,
    )
    out, info = neutralize_matrix(panel, raw, industry=True, size=True, min_obs=5)
    assert info["industry_available"] is True
    assert out.notna().any().any()


def test_neutralize_size_only_and_industry_only():
    panel = _panel(n_days=6, n_codes=12)
    raw = pd.DataFrame(
        np.random.default_rng(3).normal(size=panel.shape), index=panel.dates, columns=panel.codes
    )
    out_s, info_s = neutralize_matrix(panel, raw, industry=False, size=True, min_obs=5)
    assert info_s["modes"] == ["size"]
    out_i, info_i = neutralize_matrix(panel, raw, industry=True, size=False, min_obs=5)
    assert info_i["modes"] == ["industry"]
    assert out_s.shape == out_i.shape == raw.shape


def test_parse_modes():
    assert parse_modes("industry,size") == (True, True)
    assert parse_modes("size") == (False, True)
    assert parse_modes("none") == (False, False)
    assert parse_modes("") == (False, False)
    with pytest.raises(ValueError):
        parse_modes("industry,beta")


def test_build_neutralized_specs_and_evaluate_panel():
    panel = _panel(n_days=30, n_codes=20)
    specs, meta = build_neutralized_specs(panel, ["mom20"], industry=True, size=True, min_obs=5)
    assert "mom20" in specs
    assert specs["mom20"].family.endswith("中性化")
    mat = specs["mom20"].compute(panel)
    assert mat.shape == panel.shape
    report = evaluate_panel(panel, factor_names=["mom20"], horizon=1, min_cross_section=5,
                            n_boot=20, factor_specs=specs,
                            neutralization={"factor": "mom20", "modes": ["industry", "size"]})
    assert "mom20" in report["factors"]
    assert report["meta"]["neutralization"]["modes"] == ["industry", "size"]


def test_run_eval_neutralize_integration(monkeypatch):
    panel = _panel(n_days=30, n_codes=20)
    monkeypatch.setattr(factor_lib, "load_panel", lambda *a, **k: panel)
    report = factor_lib.run_eval("20250101", "20251231", factor_names=["mom20"], horizon=1,
                         min_cross_section=5, n_boot=20, neutralize="industry,size")
    assert report["meta"]["neutralization"]["industry"] is True
    assert report["meta"]["neutralization"]["size"] is True
    assert report["factors"]["mom20"]["family"].endswith("中性化")


class _FakePITIndustry:
    def __init__(self, panel: Panel):
        self.panel = panel
        self.calls: list[str] = []

    def at(self, date):
        self.calls.append(str(date))
        half = len(self.panel.dates) // 2
        pos = list(self.panel.dates).index(str(date)) if str(date) in list(self.panel.dates) else 0
        labels = ["A", "B"] if pos < half else ["B", "A"]
        return pd.Series([labels[i % 2] for i in range(len(self.panel.codes))],
                         index=self.panel.codes, name="industry")


def test_neutralize_uses_pit_industry_per_day():
    panel = _panel(n_days=6, n_codes=12)
    pit = _FakePITIndustry(panel)
    # 构造与 PIT 行业强相关的原始因子
    raw = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
    for dt in panel.dates:
        ind = pit.at(dt)
        code = ind.map({"A": 1.0, "B": -1.0}).to_numpy(dtype=float)
        raw.loc[dt] = code * 10.0 + np.linspace(0, 0.1, len(panel.codes))
    out, info = neutralize_matrix(panel, raw, industry=True, size=False, min_obs=5,
                                  industry_map=pit)
    assert info["industry_source"] == "pit_sw_l1"
    assert info["industry_available"] is True
    for dt in panel.dates:
        y = out.loc[dt]
        ind = pit.at(dt)
        means = y.groupby(ind).mean()
        assert means.abs().max() < 1e-8
    # 快照行业与 PIT 不同，说明确实没有用 snapshot
    assert panel.load_meta["industry"].nunique() >= 2


def test_build_neutralized_specs_records_pit_source():
    panel = _panel(n_days=10, n_codes=12)
    pit = _FakePITIndustry(panel)
    specs, meta = build_neutralized_specs(panel, ["mom20"], industry=True, size=True,
                                          min_obs=5, industry_map=pit)
    assert meta["industry_source"] == "pit_sw_l1"
    assert "mom20" in specs
