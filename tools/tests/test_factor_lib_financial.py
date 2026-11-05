# -*- coding: utf-8 -*-
"""B3 PIT 财报因子（tools/research/factor_lib_financial.py）单元测试。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_factor_lib_financial.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib  # noqa: E402
import factor_lib_financial as ff  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402
from factor_lib import FACTORS, Panel  # noqa: E402

MODULE = ROOT / "tools" / "research" / "factor_lib_financial.py"


# ------------------------------------------------------------------ 报表构造
def _make_reports(codes=("000001.SZ", "600000.SH")) -> pd.DataFrame:
    rows = []
    quarters = [
        ("2023-03-31", "2023-04-28", 250, 150, 20, 20),
        ("2023-06-30", "2023-08-30", 520, 310, 42, 42),
        ("2023-09-30", "2023-10-30", 780, 460, 63, 63),
        ("2023-12-31", "2024-04-01", 1050, 620, 85, 85),
        ("2024-03-31", "2024-04-30", 300, 180, 25, 25),
        ("2024-06-30", "2024-08-30", 600, 360, 50, 50),
        ("2024-09-30", "2024-10-30", 900, 540, 75, 75),
        ("2024-12-31", "2025-04-01", 1200, 720, 100, 100),
        ("2025-03-31", "2025-04-30", 360, 210, 30, 30),
    ]
    for code in codes:
        for ed, ad, rev, cost, ni, _ in quarters:
            rows.append(dict(
                ts_code=code, end_date=ed, ann_date=ad, report_type="1",
                revenue=rev, oper_cost=cost, n_income=ni, n_income_attr_p=ni,
                operate_profit=ni * 1.2, total_profit=ni * 1.1,
                income_tax=ni * 0.2, basic_eps=ni / 100, ebit=ni * 1.3,
                ebitda=ni * 1.5, fin_exp_int_exp=ni * 0.1,
                total_assets=5000, total_liab=2000,
                total_hldr_eqy_exc_min_int=3000,
                total_hldr_eqy_inc_min_int=3000,
                total_cur_assets=2000, total_cur_liab=1000,
                inventories=300, money_cap=500, trad_asset=200,
                fix_assets=1000, lt_rec=100, accounts_receiv=200,
                adv_receipts=150, prepayment=50, defer_tax_assets=100,
                total_ncl=1000, total_share=100, n_cashflow_act=ni * 1.1,
                n_cashflow_inv_act=-ni * 0.4, n_cash_flows_fnc_act=ni * 0.2,
                stot_out_inv_act=ni * 0.3, c_inf_fr_operate_a=ni * 1.2,
                stot_inflows_inv_act=ni * 0.2, c_paid_to_for_empl=ni * 0.3,
                sell_exp=ni * 0.1, admin_exp=ni * 0.1,
                biz_tax_surchg=ni * 0.05, profit_dedt=ni * 0.9,
                eps=ni / 100,
            ))
    df = pd.DataFrame(rows)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


def _derived() -> pd.DataFrame:
    reports = _make_reports()
    merged = ff._add_flow_metrics(
        reports, ff.FLOW_INCOME + ff.FLOW_CASH + ("profit_dedt",))
    return ff._build_derived(merged)


def _panel(days: int = 300, codes=("000001.SZ", "600000.SH")) -> Panel:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=days).strftime("%Y%m%d"))
    cols = pd.Index(codes)
    close = pd.DataFrame(10.0, index=dates, columns=cols)
    fields = {
        "close": close, "adj_close": close,
        "total_mv": pd.DataFrame(1e10, index=dates, columns=cols),
    }
    fd = ff.FinancialData(_derived())
    meta = {"financial": fd, "industry": pd.Series("银行", index=cols)}
    return Panel(fields, meta=meta)


# ------------------------------------------------------------------ 1. 映射/注册
def test_b3_map_complete():
    assert len(ff.B3_FACTORS) == 85
    summary = ff.summary()
    assert summary["by_type"] == {"Growth": 15, "Quality": 59, "Value": 11}
    assert summary["blocked"] == ["dividend_yield_3y_avg"]
    status = {n: e["status"] for n, e in ft.TUSHARE_FACTOR_MAP.items()}
    assert status["asset_growth_qoq"] == "implemented"
    assert status["dividend_yield_3y_avg"] == "blocked_missing_data"
    assert sum(1 for v in status.values() if v == "implemented") == 271


def test_register_b3_and_idempotent():
    assert len(FACTORS) == 288
    assert "asset_growth_qoq" in FACTORS
    assert "dividend_yield_3y_avg" not in FACTORS
    assert FACTORS["asset_growth_qoq"].family == "Tushare-财务"
    assert ft.register_batch(factor_lib.register, FACTORS) == 0


# ------------------------------------------------------------------ 2. 流量口径
def test_flow_metrics_quarter_ttm_annual():
    reports = _make_reports(codes=("000001.SZ",))
    merged = ff._add_flow_metrics(
        reports, ff.FLOW_INCOME + ff.FLOW_CASH + ("profit_dedt",))
    row = merged[merged["end_date"] == pd.Timestamp("2025-03-31")].iloc[0]
    assert row["revenue_q"] == 360
    assert row["revenue_y"] == 1200
    # TTM = 2024 年报 1200 + 2025Q1 360 - 2024Q1 300
    assert row["revenue_ttm"] == 1260
    row_y = merged[merged["end_date"] == pd.Timestamp("2024-12-31")].iloc[0]
    assert row_y["revenue_ttm"] == 1200
    assert row_y["revenue_y"] == 1200


def test_flow_metrics_dedupes_reports_before_merging():
    """全市场回归：重复报告行/同年多个 Q4 不能让 merge 逐列放大。"""
    reports = _make_reports(codes=("000001.SZ",))
    extra = reports.iloc[[3, 7]].copy()  # 同 ts_code/end_date 的重复行
    extra["ann_date"] = extra["ann_date"] + pd.Timedelta(days=1)
    # 同一财年出现两个 Q4 报告期（12-30 重述 + 12-31 正式）
    restated = reports.iloc[[7]].copy()
    restated["end_date"] = pd.Timestamp("2024-12-30")
    reports = pd.concat([reports, extra, restated], ignore_index=True)
    n_unique = reports.drop_duplicates(["ts_code", "end_date"]).shape[0]

    merged = ff._add_flow_metrics(
        reports, ff.FLOW_INCOME + ff.FLOW_CASH + ("profit_dedt",))

    assert len(merged) == n_unique
    row = merged[merged["end_date"] == pd.Timestamp("2025-03-31")].iloc[0]
    assert row["revenue_ttm"] == 1260
    # 同年两个 Q4 时 annual 取排序后的最后一个（12-31 正式值）
    row_y = merged[merged["end_date"] == pd.Timestamp("2024-12-31")].iloc[0]
    assert row_y["revenue_ttm"] == 1200


def test_derived_metrics_and_value_scale():
    d = _derived()
    assert "gross_margin_ttm" in d.columns
    assert "quality_composite" in d.columns
    assert "book_to_market_num" in d.columns
    row = d[d["end_date"] == pd.Timestamp("2024-12-31")].iloc[0]
    assert row["gross_margin_ttm"] == pytest.approx((1200 - 720) / 1200)
    assert row["net_margin_ttm"] == pytest.approx(100 / 1200)
    assert row["roe_ttm"] == pytest.approx(100 / 3000)


# ------------------------------------------------------------------ 3. PIT 日频化
def test_daily_aligns_by_ann_date():
    d = _derived()
    fd = ff.FinancialData(d)
    dates = pd.Index(["2025-03-31", "2025-04-01", "2025-04-30", "2025-05-06"])
    panel = Panel({"close": pd.DataFrame(10.0, index=dates, columns=["000001.SZ"])},
                  meta={})
    daily = fd.daily("revenue", panel)
    assert np.isnan(daily.loc["2025-03-31", "000001.SZ"]) or \
        daily.loc["2025-03-31", "000001.SZ"] == 900
    assert daily.loc["2025-04-01", "000001.SZ"] == 1200
    assert daily.loc["2025-04-30", "000001.SZ"] == 360
    assert daily.loc["2025-05-06", "000001.SZ"] == 360


# ------------------------------------------------------------------ 4. 全 B3 因子可算
def test_all_b3_factors_compute():
    panel = _panel()
    for name, spec in ff.B3_FACTORS.items():
        if "blocked" in (spec["note"] or ""):
            continue
        out = ff.compute_b3(name, panel)
        assert isinstance(out, pd.DataFrame), name
        assert out.shape == panel.shape, name
        assert list(out.index) == list(panel.dates), name
        assert list(out.columns) == list(panel.codes), name
        assert not np.isinf(out.to_numpy(dtype=float)).any(), f"{name} 出现 inf"


# ------------------------------------------------------------------ 5. 无前视
def test_no_negative_shift_in_financial_module():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
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
    assert bad == set(), f"财务模块出现负 lag: {sorted(bad)}"
