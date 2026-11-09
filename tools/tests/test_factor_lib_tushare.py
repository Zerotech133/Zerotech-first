"""tools/tests/test_factor_lib_tushare.py · Tushare factor_list 映射与 B1 批次单测。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_factor_lib_tushare.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402
from factor_lib import FACTORS, Panel  # noqa: E402
from factor_lib_tushare import TUSHARE_FACTOR_MAP  # noqa: E402

BATCH_MODULE = ROOT / "tools" / "research" / "factor_lib_tushare.py"


# ------------------------------------------------------------------ 1. 映射完整性
def test_factor_map_complete():
    assert len(TUSHARE_FACTOR_MAP) == 272
    summary = ft.factor_map_summary()
    assert summary["total"] == 272
    assert summary["by_type"] == {
        "Alpha101": 101, "Growth": 15, "Liquidity": 35, "Momentum": 20,
        "Quality": 59, "Reversal": 3, "Risk": 25, "Size": 3, "Value": 11,
    }
    assert summary["by_batch"] == {
        ft.BATCH_B1: 86, ft.BATCH_B2: 101, ft.BATCH_B3: 85,
    }
    assert summary["by_status"] == {"implemented": 271, "blocked_missing_data": 1}
    for name, entry in TUSHARE_FACTOR_MAP.items():
        assert entry["name"] == name
        assert entry["factor_type"] in ft.TYPE_NAMES
        assert entry["status"] in ("implemented", "mapped", "blocked_missing_data")
        assert isinstance(entry["requires"], tuple)
        assert entry["source"] == ft.SOURCE


def test_b1_implemented_registered_in_factor_lib():
    implemented = {n for n, e in TUSHARE_FACTOR_MAP.items()
                   if e["status"] == "implemented" and e["batch"] == ft.BATCH_B1}
    assert len(implemented) == 86
    assert implemented <= set(FACTORS)
    for name in implemented:
        spec = FACTORS[name]
        assert spec.source == ft.SOURCE
        assert spec.family.startswith("Tushare-")
        assert spec.kind == "continuous"
        assert spec.direction in (1, -1)


def test_register_batch_is_idempotent():
    assert ft.register_batch(factor_lib.register, FACTORS) == 0


# ------------------------------------------------------------------ 2. 合成面板可计算
def _synthetic_panel(days: int = 90, codes: int = 6) -> Panel:
    rng = np.random.default_rng(7)
    dates = pd.Index(pd.date_range("2026-01-01", periods=days, freq="B").strftime("%Y%m%d"))
    cols = pd.Index([f"00000{i}.SZ" for i in range(1, codes + 1)])
    close = pd.DataFrame(10.0 + np.cumsum(rng.normal(0.0, 0.05, (days, codes)), axis=0),
                         index=dates, columns=cols)
    pre_close = close.shift(1).fillna(10.0)
    open_ = close * (1.0 + rng.normal(0.0, 0.005, (days, codes)))
    high = np.maximum(close, open_) * 1.01
    low = np.minimum(close, open_) * 0.99
    vol = pd.DataFrame(rng.uniform(1e5, 5e5, (days, codes)), index=dates, columns=cols)
    amount = vol * close / 1000.0
    base = pd.DataFrame(10.0, index=dates, columns=cols)
    fields = {
        "open": open_, "high": high, "low": low, "close": close,
        "pre_close": pre_close, "pct_chg": (close / pre_close - 1.0) * 100.0,
        "vol": vol, "amount": amount, "adj_close": close,
        "turnover_rate": pd.DataFrame(rng.uniform(0.5, 8.0, (days, codes)), index=dates, columns=cols),
        "pe_ttm": pd.DataFrame(12.0, index=dates, columns=cols),
        "pb": pd.DataFrame(1.5, index=dates, columns=cols),
        "ps_ttm": pd.DataFrame(2.0, index=dates, columns=cols),
        "dv_ratio": pd.DataFrame(1.0, index=dates, columns=cols),
        "volume_ratio": pd.DataFrame(1.0, index=dates, columns=cols),
        "total_share": pd.DataFrame(10.0, index=dates, columns=cols),
        "float_share": pd.DataFrame(6.0, index=dates, columns=cols),
        "free_share": pd.DataFrame(5.0, index=dates, columns=cols),
        "total_mv": pd.DataFrame(500000.0, index=dates, columns=cols),
        "circ_mv": pd.DataFrame(300000.0, index=dates, columns=cols),
        "roe": base,
    }
    idx_r = pd.Series(rng.normal(0.0, 0.01, days), index=dates)
    idx_v = pd.Series(rng.uniform(1e8, 2e8, days), index=dates)
    meta = {
        "index_returns": {"000300.SH": idx_r, "000001.SH": idx_r * 0.9},
        "index_volume": {"000300.SH": idx_v, "000001.SH": idx_v * 0.8},
        "industry": pd.Series("银行", index=cols),
    }
    return Panel(fields, meta=meta)


def test_all_b1_factors_compute_and_shape():
    panel = _synthetic_panel()
    names = (n for n, e in TUSHARE_FACTOR_MAP.items()
             if e["status"] == "implemented" and e["batch"] in (ft.BATCH_B1, ft.BATCH_B2))
    for name in names:
        out = FACTORS[name].compute(panel)
        assert isinstance(out, pd.DataFrame), name
        assert out.shape == panel.shape, name
        assert list(out.index) == list(panel.dates), name
        assert list(out.columns) == list(panel.codes), name
        assert not np.isinf(out.to_numpy(dtype=float)).any(), f"{name} 出现 inf"


def test_no_negative_shift_in_batch_module():
    src = BATCH_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "shift" and sub.args):
                    try:
                        val = ast.literal_eval(sub.args[0])
                    except (ValueError, SyntaxError):
                        continue
                    if isinstance(val, (int, float)) and not isinstance(val, bool) and val < 0:
                        bad.add(node.name)
    assert bad == set(), f"B1 模块出现负 lag: {sorted(bad)}"
    assert "shift(-" not in src.replace(" ", "")


# ------------------------------------------------------------------ 3. 公式对照
def test_formula_reference_checks():
    panel = _synthetic_panel()
    code = panel.codes[0]
    close = panel.close[code]

    out = FACTORS["ma_20d"].compute(panel)[code]
    expect = close.rolling(20, min_periods=10).mean()
    pd.testing.assert_series_equal(out, expect, check_names=False)

    out = FACTORS["return_21d"].compute(panel)[code]
    expect = close / close.shift(21) - 1.0
    pd.testing.assert_series_equal(out, expect, check_names=False)

    out = FACTORS["size"].compute(panel)[code]
    expect = -np.log(panel.optional("total_share")[code] * close / 100.0)
    pd.testing.assert_series_equal(out, expect, check_names=False)

    out = FACTORS["rsi"].compute(panel)[code]
    assert out.dropna().between(0, 100).all()

    out = FACTORS["price_dist"].compute(panel)[code]
    assert (out.dropna() >= 0).all()


def test_nl_size_is_cross_sectional_residual_of_size_cubed():
    panel = _synthetic_panel(days=5, codes=30)
    out = FACTORS["nl_size"].compute(panel)
    size = FACTORS["size"].compute(panel)
    last = out.dropna(how="all").index[-1]
    x = size.loc[last].to_numpy(dtype=float)
    y = out.loc[last].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    assert mask.sum() >= 10
    # 残差与 size 的相关系数应接近 0（截面回归正交性）
    corr = np.corrcoef(x[mask], y[mask])[0, 1]
    assert abs(corr) < 1e-6, corr


def test_index_based_factors_use_index_meta():
    """回归类因子必须真正吃到 Panel.load_meta 的指数收益/成交量（曾因 _index_series
    只接受 Series 而导致所有 beta/alpha 因子全 NaN）。"""
    panel = _synthetic_panel(days=260, codes=2)
    for name in ("beta_60d_000300", "alpha_125d_000300", "rsrs",
                 "volume_alpha_300d_000300", "volume_beta_120d_000300",
                 "beta_consistency_1320d_000300", "sigma_1320d_000300"):
        out = FACTORS[name].compute(panel)
        finite = np.isfinite(out.to_numpy(dtype=float)).sum()
        if name in ("beta_consistency_1320d_000300", "sigma_1320d_000300"):
            # 窗口 1320 > 260 日，允许全 NaN，但不能抛异常
            assert finite == 0, name
        else:
            assert finite > 0, f"{name} 全 NaN（指数 meta 未吃到）"


def test_rsrs_is_low_high_regression_slope_zscore():
    panel = _synthetic_panel(days=260, codes=2)
    out = FACTORS["rsrs"].compute(panel)
    # 手工：high 对 low 滚动 18 日回归斜率，再 200 日 zscore（这里仅校验形状与有限值）
    assert out.shape == panel.shape
    assert np.isfinite(out.to_numpy(dtype=float)).sum() > 0


# ------------------------------------------------------------------ 4. loader 扩展
def test_load_panel_extended_fields_with_synthetic_parquet(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path / "tushare"
    codes = ["000001.SZ", "600000.SH"]
    dates = pd.bdate_range("2026-01-01", periods=40).strftime("%Y%m%d").tolist()
    rows = []
    for d in dates:
        for i, c in enumerate(codes):
            rows.append({
                "ts_code": c, "trade_date": d, "open": 10.0 + i, "high": 10.5 + i,
                "low": 9.5 + i, "close": 10.0 + i, "pre_close": 9.9 + i,
                "pct_chg": 1.0, "vol": 1000.0, "amount": 10000.0,
            })
    daily = pd.DataFrame(rows)
    for table, frame in (("daily", daily),):
        tdir = root / table
        tdir.mkdir(parents=True)
        for d, part in frame.groupby("trade_date"):
            pq.write_table(pa.Table.from_pandas(part), str(tdir / f"d_{d}.parquet"))
    adj = daily[["ts_code", "trade_date"]].copy()
    adj["adj_factor"] = 1.0
    tdir = root / "adj_factor"
    tdir.mkdir(parents=True)
    for d, part in adj.groupby("trade_date"):
        pq.write_table(pa.Table.from_pandas(part), str(tdir / f"d_{d}.parquet"))
    db = daily[["ts_code", "trade_date"]].copy()
    db["pe_ttm"] = 12.0
    db["turnover_rate"] = 2.0
    db["pb"] = 1.2
    db["ps_ttm"] = 2.0
    db["dv_ratio"] = 1.0
    db["total_share"] = 100.0
    db["float_share"] = 60.0
    db["free_share"] = 50.0
    db["total_mv"] = 100000.0
    db["circ_mv"] = 60000.0
    db["volume_ratio"] = 1.1
    tdir = root / "daily_basic"
    tdir.mkdir(parents=True)
    for d, part in db.groupby("trade_date"):
        pq.write_table(pa.Table.from_pandas(part), str(tdir / f"d_{d}.parquet"))
    idx_rows = []
    for d in dates:
        for code in ("000300.SH", "000001.SH"):
            idx_rows.append({"ts_code": code, "trade_date": d, "close": 4000.0,
                             "vol": 1e8})
    idx = pd.DataFrame(idx_rows)
    tdir = root / "index_daily"
    tdir.mkdir(parents=True)
    for d, part in idx.groupby("trade_date"):
        pq.write_table(pa.Table.from_pandas(part), str(tdir / f"d_{d}.parquet"))
    sb = pd.DataFrame({"ts_code": codes, "name": ["A", "B"], "industry": ["银行", "地产"],
                       "list_date": ["20200101", "20200101"]})
    tdir = root / "stock_basic"
    tdir.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(sb), str(tdir / "stock_basic.parquet"))

    panel = factor_lib.load_panel(dates[0], dates[-1], warmup=5, universe="all",
                                  data_root=root, load_roe=False)
    for field in ("turnover_rate", "total_share", "float_share", "total_mv", "circ_mv",
                  "pb", "ps_ttm", "dv_ratio", "volume_ratio"):
        assert field in panel.fields
    assert set(panel.load_meta["index_returns"]) == {"000300.SH", "000001.SH"}
    assert panel.load_meta["industry"].notna().all()
    assert panel.fields["turnover_rate"].notna().all().all()


# ------------------------------------------------------------------ 5. CLI
def test_cli_map_json():
    proc = __import__("subprocess").run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "research" / "factor_lib.py"),
         "map", "--json"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    summary = payload["summary"]
    assert summary["total"] == 272
    assert summary["implemented"] == 271
