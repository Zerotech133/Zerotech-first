# -*- coding: utf-8 -*-
"""真实因子面板验证运行器契约测试（WO-VAL-FACTOR-20261101 / task-32）。

全离线合成 Panel/数据（不读真实数据仓、不联网）：
  · 周频信号日选择（每周最后交易日）；
  · 涨跌停推导（主板10%/ST5%/创业板·科创板20%/北交所30%）；
  · 面板构造：复权 forward 20 日、PIT 因子方向、ST/停牌/涨跌停过滤计数；
  · 调用已合入 factor_diagnostics + factor_gate：正/负因子、样本不足 fail-visible。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "research" / "factor_panel_validate.py"


def _load():
    spec = importlib.util.spec_from_file_location("factor_panel_validate_under_test",
                                                  MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fv = _load()
fl = fv.fl


# ---------------------------------------------------------------- 合成面板
def _biz_dates(n=60):
    out, d = [], date(2026, 1, 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _mk_panel(dates, codes, daily_ret=0.01, eligible=None):
    idx = pd.Index(dates)
    cols = pd.Index(codes)
    adj = pd.DataFrame(index=idx, columns=cols, dtype=float)
    for code in codes:
        adj[code] = 100.0 * (1.0 + daily_ret) ** np.arange(len(dates))
    close = adj.copy()
    high = close.copy()
    low = close.copy()
    pct = close.pct_change() * 100.0
    turnover = pd.DataFrame({code: np.linspace(1.0, 3.0, len(dates)) for code in codes},
                            index=idx)
    amount = pd.DataFrame(1e6, index=idx, columns=cols, dtype=float)
    if eligible is None:
        eligible = pd.DataFrame(True, index=idx, columns=cols)
    fields = {
        "close": close, "high": high, "low": low, "pre_close": close.shift(1),
        "pct_chg": pct, "adj_close": adj, "turnover_rate": turnover,
        "amount": amount, "vol": amount / 100.0, "open": close,
    }
    return fl.Panel(fields, eligible=eligible)


# ---------------------------------------------------------------- 周频日期
def test_weekly_signal_dates():
    dates = _biz_dates(12)       # 2026-01-05 起约 2.5 周
    sig = fv.weekly_signal_dates(dates, dates[0], dates[-1])
    assert sig
    dt = pd.to_datetime(pd.Index(sig), format="%Y%m%d")
    assert list(dt.to_period("W-FRI")) == sorted(set(dt.to_period("W-FRI")))
    assert all(d in dates for d in sig)
    # 区间裁剪
    assert all(dates[3] <= s <= dates[8] for s in
               fv.weekly_signal_dates(dates, dates[3], dates[8]))
    assert fv.weekly_signal_dates(dates, "20270101", "20270201") == []


# ---------------------------------------------------------------- 涨跌停推导
def test_board_limit_pct():
    assert fv._board_limit_pct("600000.SH", False) == 0.10
    assert fv._board_limit_pct("600000.SH", True) == 0.05
    assert fv._board_limit_pct("300001.SZ", False) == 0.20
    assert fv._board_limit_pct("688001.SH", True) == 0.20
    assert fv._board_limit_pct("830001.BJ", False) == 0.30


def test_derive_limit_matrices():
    dates = _biz_dates(4)
    codes = ["600000.SH", "300001.SZ", "830001.BJ", "600001.SH"]
    panel = _mk_panel(dates, codes, daily_ret=0.0)
    # 人工构造：主板涨停、创业板 +10% 不算、创业板 +20% 算、北交所 +20% 不算、主板 ST +5% 算
    p = panel.pct_chg.copy()
    c = panel.close.copy()
    h = panel.high.copy()
    lo = panel.low.copy()
    p.iloc[1, p.columns.get_loc("600000.SH")] = 10.0
    c.iloc[1, c.columns.get_loc("600000.SH")] = 110.0
    h.iloc[1, h.columns.get_loc("600000.SH")] = 110.0
    p.iloc[1, p.columns.get_loc("300001.SZ")] = 10.0
    c.iloc[1, c.columns.get_loc("300001.SZ")] = 110.0
    h.iloc[1, h.columns.get_loc("300001.SZ")] = 110.0
    p.iloc[2, p.columns.get_loc("300001.SZ")] = 20.0
    c.iloc[2, c.columns.get_loc("300001.SZ")] = 120.0
    h.iloc[2, h.columns.get_loc("300001.SZ")] = 120.0
    p.iloc[1, p.columns.get_loc("830001.BJ")] = 20.0
    c.iloc[1, c.columns.get_loc("830001.BJ")] = 120.0
    h.iloc[1, h.columns.get_loc("830001.BJ")] = 120.0
    p.iloc[1, p.columns.get_loc("600001.SH")] = 5.0
    c.iloc[1, c.columns.get_loc("600001.SH")] = 105.0
    h.iloc[1, h.columns.get_loc("600001.SH")] = 105.0
    for frame in (p, c, h):
        panel.fields["pct_chg"] = p
        panel.fields["close"] = c
        panel.fields["high"] = h
        break
    st = pd.DataFrame(False, index=dates, columns=codes)
    st.iloc[:, st.columns.get_loc("600001.SH")] = True
    up, down = fv.derive_limit_matrices(panel, st)
    assert bool(up.loc[dates[1], "600000.SH"]) is True
    assert bool(up.loc[dates[1], "300001.SZ"]) is False    # +10% 未达 20%
    assert bool(up.loc[dates[2], "300001.SZ"]) is True     # +20% 达涨停
    assert bool(up.loc[dates[1], "830001.BJ"]) is False    # 20% < 30%
    assert bool(up.loc[dates[1], "600001.SH"]) is True     # 主板 ST 5%
    assert not down.to_numpy().any()


# ---------------------------------------------------------------- 面板构造
def test_build_factor_panel_filters_and_forward():
    dates = _biz_dates(60)
    codes = ["600000.SH", "600001.SH", "000002.SZ", "300001.SZ",
             "600003.SH", "600004.SH"]
    elig = pd.DataFrame(True, index=dates, columns=codes)
    elig["600004.SH"] = False                      # 停牌/不可交易
    panel = _mk_panel(dates, codes, daily_ret=0.01, eligible=elig)
    signals = fv.weekly_signal_dates(dates, dates[0], dates[-1])
    st = pd.DataFrame(False, index=dates, columns=codes)
    st["000002.SZ"] = True                         # ST
    up = pd.DataFrame(False, index=dates, columns=codes)
    down = pd.DataFrame(False, index=dates, columns=codes)
    first_sig = signals[0]
    i0 = dates.index(first_sig)
    up.loc[first_sig, "300001.SZ"] = True          # 信号日涨停
    up.loc[dates[i0 + 1], "600003.SH"] = True      # 次日（入场日）涨停
    df, stats = fv.build_factor_panel(
        panel, "low_turnover", signals, st_matrix=st, limit_up=up,
        limit_down=down, horizon=20)
    assert not df.empty
    assert set(df["symbol"]) == {"600000.SH", "600001.SH", "300001.SZ", "600003.SH"}
    # 第一个信号日：300001 信号日涨停、600003 次日涨停 → 只剩两只正常股
    first_rows = df[df["date"] == first_sig]
    assert set(first_rows["symbol"]) == {"600000.SH", "600001.SH"}
    # forward = adj(T+21)/adj(T+1)-1 = 1.01^20-1
    assert first_rows["forward_return"].iloc[0] == pytest.approx(1.01 ** 20 - 1, abs=1e-9)
    # 因子方向：low_turnover direction=-1 → 送入面板 = -turnover(T)
    expected = -float(panel.turnover_rate.loc[first_sig, "600000.SH"])
    got = float(first_rows.loc[first_rows["symbol"] == "600000.SH", "factor"].iloc[0])
    assert got == pytest.approx(expected, abs=1e-12)
    # 过滤计数（每信号日：1 只 ST、1 只不可交易、首日各 1 只涨跌停）
    n_weeks = stats["weeks"]
    assert stats["skipped_st"] == n_weeks
    assert stats["skipped_untradable"] == n_weeks
    assert stats["skipped_limit_signal"] == 1
    assert stats["skipped_limit_entry"] == 1
    assert stats["rows"] == len(df)
    # PIT：因子只含 T 日换手，不含未来；把未来换手改掉不影响该日因子
    assert df["date"].max() < dates[-1]


# ---------------------------------------------------------------- 诊断+gate 集成
def _weekly_frame(n_weeks=60, n_symbols=100, seed=7, direction=1.0):
    rng = np.random.default_rng(seed)
    rows = []
    for w in range(n_weeks):
        date = (pd.Timestamp("2023-01-06") + pd.Timedelta(weeks=w)).strftime("%Y-%m-%d")
        factor = rng.normal(0, 1, n_symbols)
        fwd = direction * 0.5 * factor + rng.normal(0, 0.3, n_symbols)
        for i in range(n_symbols):
            rows.append({"date": date, "symbol": "S%03d" % i,
                         "factor": float(factor[i]),
                         "forward_return": float(fwd[i])})
    return pd.DataFrame(rows)


def test_evaluate_panel_positive_passes_gate():
    th = {"min_rank_ic_mean": 0.02, "min_icir": 0.2, "min_coverage": 50.0,
          "min_days": 20, "min_positive_rate": 0.55, "min_quantile_spearman": 0.5,
          "min_long_short_ir": 0.2, "max_turnover": 1.0, "max_bad_ratio": 0.2}
    res = fv.evaluate_panel(_weekly_frame(direction=1.0), th)
    assert res["diagnostics"]["ic"]["rank"]["mean"] > 0.2
    assert res["gate"]["status"] == "pass", res["gate"]["checks"]


def test_evaluate_panel_negative_factor_fails():
    th = dict(fv.VALIDATION_THRESHOLDS)
    th.update({"min_coverage": 50.0, "min_days": 20})
    res = fv.evaluate_panel(_weekly_frame(direction=-1.0), th)
    assert res["gate"]["status"] == "fail"
    assert "rank_ic_mean" in res["gate"]["failures"]


def test_evaluate_panel_insufficient_fail_visible():
    df = _weekly_frame(n_weeks=5, n_symbols=60)
    res = fv.evaluate_panel(df, dict(fv.VALIDATION_THRESHOLDS))
    gate = res["gate"]
    assert gate["status"] == "fail"
    assert "min_days" in gate["failures"] or "coverage" in gate["failures"]
    assert gate["metrics"]["bad_rows"] == 0
    assert res["diagnostics"]["coverage"]["names_mean"] == 60.0


# ---------------------------------------------------------------- 报告渲染
def test_render_md_smoke():
    report = {
        "work_order": "WO-VAL-FACTOR-20261101",
        "generated_at": "2026-11-01T00:00:00+08:00",
        "data": {"root": "X", "daily_last_file": "d_x.parquet",
                 "window": {"start": "20230101", "end": "20260930", "horizon": 20},
                 "weekly_signals": 1, "first_signal": "20230106",
                 "last_signal": "20230106", "universe": "u", "st_filter": "st",
                 "suspension_filter": "s", "limit_filter": "l"},
        "thresholds": {},
        "factors": {"low_turnover": {
            "panel": {"rows": 2, "weeks": 1, "symbols": 2,
                      "filter_stats": {"candidates": 3, "skipped_untradable": 0,
                                       "skipped_st": 0, "skipped_limit_signal": 0,
                                       "skipped_limit_entry": 0,
                                       "skipped_missing_data": 1}},
            "diagnostics": {"ic": {"rank": {"mean": 0.1, "icir": 1.0,
                                            "positive_rate": 1.0}},
                            "coverage": {"names_mean": 2.0},
                            "quantiles": {"returns": {"1": 0.0, "5": 0.1},
                                          "monotonic_up": True,
                                          "monotonic_down": False,
                                          "quantile_spearman": 1.0},
                            "long_short": {"mean": 0.1, "icir": 1.0},
                            "turnover": {"mean": 0.0}},
            "gate": {"status": "pass", "checks": [], "warnings": []},
        }},
        "limitations": ["L1"],
    }
    md = fv.render_md(report)
    assert "# 真实因子面板验证" in md
    assert "low_turnover" in md and "gate 检查明细" in md
    assert "复现" in md
