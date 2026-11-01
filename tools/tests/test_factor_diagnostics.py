# -*- coding: utf-8 -*-
"""脱机因子诊断工具契约测试（WO-STRAT-FDIAG-20261101 / task-24）。

全离线：合成面板（pandas/numpy），不读真实数据仓、不联网。
覆盖：正/负 rank IC、分位单调、多空差、ICIR、换手、覆盖率、样本数、
缺列/空数据/坏行 fail-visible、并列因子稳定分层、CLI CSV/JSON。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "research" / "factor_diagnostics.py"


def _load():
    spec = importlib.util.spec_from_file_location("factor_diagnostics_under_test",
                                                  MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fd = _load()


# ---------------------------------------------------------------- 合成面板
def _panel(direction=1.0, n_days=3, n_names=20, seed=7):
    """factor=i，forward_return=direction*i*0.001+小噪声 → 高 rank IC 且日间有差值。

    噪声 0.0012 小于分位间距（4×0.001），保证分位均值仍单调；
    同时日间 rank IC 有微小差异（std>0 → ICIR 可计算）。
    """
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        date = "2026-10-%02d" % (20 + d)
        factor = np.arange(n_names, dtype=float)
        noise = rng.normal(0.0, 0.0012, n_names)
        fwd = direction * factor * 0.001 + noise
        for i in range(n_names):
            rows.append({"date": date, "symbol": "S%02d" % i,
                         "factor": float(factor[i]),
                         "forward_return": float(fwd[i])})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 正/负 IC
def test_positive_ic_and_quantile_monotonic():
    rep = fd.compute(_panel(direction=1.0))
    assert rep["schema"] == "factor_diagnostics/v1"
    rank = rep["ic"]["rank"]
    assert rank["n_days"] == 3
    assert rank["mean"] > 0.9
    assert rank["icir"] is not None and rank["icir"] > 0
    assert rank["t_stat"] is not None and rank["t_stat"] > 0
    assert rank["positive_rate"] == 1.0
    q = rep["quantiles"]
    means = [q["returns"][str(i)] for i in range(1, 6)]
    assert all(means[i] < means[i + 1] for i in range(4)), means
    assert q["monotonic_up"] is True and q["monotonic_down"] is False
    assert q["quantile_spearman"] is not None and q["quantile_spearman"] > 0.9
    ls = q["long_short"]
    assert ls["mean"] > 0 and ls["n_days"] == 3
    assert rep["turnover"]["mean"] == 0.0          # 排序不变 → 无成分替换
    assert rep["coverage"]["names_mean"] == 20
    assert rep["samples"]["rows_used"] == 60


def test_negative_ic_and_inverse_ordering():
    rep = fd.compute(_panel(direction=-1.0))
    rank = rep["ic"]["rank"]
    assert rank["mean"] < -0.9
    assert rank["icir"] < 0
    q = rep["quantiles"]
    means = [q["returns"][str(i)] for i in range(1, 6)]
    assert all(means[i] > means[i + 1] for i in range(4)), means
    assert q["monotonic_down"] is True and q["monotonic_up"] is False
    assert q["long_short"]["mean"] < 0


# ---------------------------------------------------------------- 缺列 / 空数据
def test_missing_columns_raises():
    for col in ("date", "symbol", "factor", "forward_return"):
        bad = _panel().drop(columns=[col])
        with pytest.raises(fd.FactorDiagnosticsError, match=col):
            fd.compute(bad)


def test_empty_and_non_frame_raise():
    with pytest.raises(fd.FactorDiagnosticsError):
        fd.compute(pd.DataFrame(columns=["date", "symbol", "factor",
                                         "forward_return"]))
    with pytest.raises(fd.FactorDiagnosticsError):
        fd.compute(None)
    with pytest.raises(fd.FactorDiagnosticsError):
        fd.compute([{"date": "2026-10-20"}])   # 非 DataFrame


def test_param_validation():
    with pytest.raises(fd.FactorDiagnosticsError):
        fd.compute(_panel(), n_quantiles=1)
    with pytest.raises(fd.FactorDiagnosticsError):
        fd.compute(_panel(), min_names_per_day=1)


# ---------------------------------------------------------------- 坏行 fail-visible
def test_bad_rows_are_dropped_and_visible():
    good = _panel(n_days=2, n_names=10).to_dict("records")
    bad = [
        {"date": "not-a-date", "symbol": "S00", "factor": 1.0, "forward_return": 0.1},
        {"date": "2026-10-20", "symbol": "", "factor": 1.0, "forward_return": 0.1},
        {"date": "2026-10-20", "symbol": "S99", "factor": "abc", "forward_return": 0.1},
        {"date": "2026-10-20", "symbol": "S98", "factor": 1.0, "forward_return": np.inf},
        {"date": "2026-10-20", "symbol": "S97", "factor": 1.0, "forward_return": "NaN"},
    ]
    df = pd.DataFrame(good + bad)
    rep = fd.compute(df)
    assert rep["samples"]["bad_rows"] == 5
    assert rep["samples"]["rows_used"] == len(good)
    assert len(rep["bad_rows"]) == 5
    reasons = " ".join(x["reason"] for x in rep["bad_rows"])
    assert "date 不可解析" in reasons and "symbol" in reasons
    assert "factor 非数值" in reasons and "forward_return 非数值" in reasons
    assert any("坏行 5 行" in w for w in rep["warnings"])
    assert rep["ic"]["rank"]["n_days"] == 2


def test_all_bad_rows_raise():
    df = pd.DataFrame([
        {"date": "bad", "symbol": "S0", "factor": 1, "forward_return": 0.1},
        {"date": "2026-10-20", "symbol": "S1", "factor": None, "forward_return": 0.1},
    ])
    with pytest.raises(fd.FactorDiagnosticsError, match="无有效行"):
        fd.compute(df)


def test_duplicate_rows_removed_visible():
    df = _panel(n_days=1, n_names=10)
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    rep = fd.compute(df)
    assert rep["samples"]["duplicate_rows"] == 1
    assert rep["samples"]["rows_used"] == 10
    assert any("重复" in w for w in rep["warnings"])


# ---------------------------------------------------------------- 截面/天数不足
def test_small_cross_section_skipped_fail_visible():
    rows = []
    for d in range(3):
        for i in range(3):                       # < 默认 min_names=5
            rows.append({"date": "2026-10-2%d" % d, "symbol": "S%d" % i,
                         "factor": float(i), "forward_return": 0.01 * i})
    rep = fd.compute(pd.DataFrame(rows))
    assert rep["ic"]["rank"]["mean"] is None
    assert rep["samples"]["days_ic"] == 0
    assert rep["samples"]["days_skipped_small"] == 3
    assert any("截面不足" in w for w in rep["warnings"])
    assert rep["coverage"]["names_mean"] == 3


def test_min_days_warning_but_no_raise():
    rep = fd.compute(_panel(n_days=1, n_names=10))
    assert rep["ic"]["rank"]["n_days"] == 1
    assert any("min_days" in w for w in rep["warnings"])


# ---------------------------------------------------------------- 换手 / 覆盖
def test_turnover_and_coverage():
    n = 10
    rows = []
    for i in range(n):                            # 第 1 日：因子升序
        rows.append({"date": "2026-10-20", "symbol": "S%02d" % i,
                     "factor": float(i), "forward_return": 0.001 * i})
    for i in range(n):                            # 第 2 日：因子降序 → top 成分全换
        rows.append({"date": "2026-10-21", "symbol": "S%02d" % i,
                     "factor": float(-i), "forward_return": 0.001 * i})
    rep = fd.compute(pd.DataFrame(rows))
    assert rep["coverage"]["names_mean"] == 10
    assert rep["coverage"]["dates"] == 2
    assert rep["turnover"]["n_pairs"] == 1
    assert rep["turnover"]["mean"] == 1.0       # 最高分位 2 只全部替换
    assert rep["quantiles"]["counts"] == {"1": 4, "2": 4, "3": 4, "4": 4, "5": 4}


# ---------------------------------------------------------------- 并列值 / 常数因子
def test_tied_factor_stable_quantiles_no_crash():
    rows = []
    for d in range(2):
        for i in range(20):
            rows.append({"date": "2026-10-2%d" % d, "symbol": "S%02d" % i,
                         "factor": 1.0,                # 全并列
                         "forward_return": 0.001 * i})
    rep = fd.compute(pd.DataFrame(rows))
    assert sum(rep["quantiles"]["counts"].values()) == 40
    assert rep["ic"]["rank"]["mean"] is None   # 常数因子 → 无 IC
    assert any("没有可计算 IC" in w for w in rep["warnings"])


# ---------------------------------------------------------------- CLI
def test_cli_csv_json_and_out(tmp_path, capsys):
    csv = tmp_path / "panel.csv"
    _panel(n_days=2, n_names=10).to_csv(csv, index=False)
    out = tmp_path / "report.json"
    rc = fd.main(["--csv", str(csv), "--json", "--out", str(out)])
    captured = capsys.readouterr().out
    printed = json.loads(captured[captured.index("{"):])   # 跳过 [report] 行
    assert rc == 0
    assert printed["schema"] == "factor_diagnostics/v1"
    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["ic"]["rank"]["n_days"] == 2


def test_cli_missing_file_fail_visible(tmp_path, capsys):
    rc = fd.main(["--csv", str(tmp_path / "nope.csv")])
    assert rc == 2
    assert "读取 CSV 失败" in capsys.readouterr().err


def test_cli_missing_column_fail_visible(tmp_path, capsys):
    csv = tmp_path / "bad.csv"
    _panel().drop(columns=["forward_return"]).to_csv(csv, index=False)
    rc = fd.main(["--csv", str(csv)])
    assert rc == 2
    assert "缺列" in capsys.readouterr().err
