# -*- coding: utf-8 -*-
"""统一回测口径 bt_harness 契约测试（合成行情，不依赖真实数据仓）。

覆盖：
  1. T+1 建仓：信号当日及前一日收益不得计入组合；
  2. 涨停不可买（pct_chg 阈值 + limit_list_d U 名单双通道）；
  3. 停牌/缺价容错：不可卖持仓保留估值、NAV 不断裂、错误有记录；
  4. 成本按换手扣减：高成本 NAV 更低、平均换手符合口径；
  5. 输出 schema / NAV 恒正 / 参数校验；
  6. 空信号容错；
  7. 周频 vs 日频调仓时点与次数；
  8. 全市场因子时按日截面 IC 计算；
  9. CLI --help 可用。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
BT_PATH = REPO / "tools" / "research" / "bt_harness.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("bt_harness_under_test", BT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bt = _load_harness()

D0, D1, D2, D3, D4 = "20260105", "20260106", "20260107", "20260108", "20260109"


def _market(dates, prices, *, pct=None, names=None, limit_up=None, limit_down=None):
    """prices: {ts_code: [close per date]}；None 表示该日停牌（无行）。"""
    records = []
    for i, date in enumerate(dates):
        for code, series in prices.items():
            value = series[i]
            if value is None:
                continue
            row = {"trade_date": date, "ts_code": code, "close": float(value), "adj_factor": 1.0}
            if pct is not None and code in pct and pct[code][i] is not None:
                row["pct_chg"] = float(pct[code][i])
            records.append(row)
    frame = pd.DataFrame(records)
    return bt.MarketData.from_long(
        frame,
        names=names or {},
        limit_up=limit_up or {},
        limit_down=limit_down or {},
    )


def _factor(rows):
    return pd.DataFrame(rows, columns=["date", "ts_code", "score"])


def _nav_map(report):
    return {row["date"]: row["nav"] for row in report["nav"]}


# --------------------------------------------------------------------------- #
# 1. T+1 建仓
# --------------------------------------------------------------------------- #
def test_t1_entry_does_not_count_signal_day_return():
    dates = [D0, D1, D2, D3]
    market = _market(dates, {"000001.SZ": [10.0, 11.0, 12.0, 12.0]})
    factor = _factor([(D0, "000001.SZ", 1.0)])
    rep = bt.run_backtest(factor, D0, D3, top_n=1, cost_bp=0, rebalance="weekly", market=market)
    navs = _nav_map(rep)
    # 信号日 D0 收盘 → D1 收盘 涨 10%；因为是 D1 收盘才建仓，当日收益绝不计入
    assert navs[D1] == pytest.approx(1.0, abs=1e-9)
    # D1 收盘价 11 买入，D2 收盘 12 → 收益从 D1→D2 才开始计
    assert navs[D2] == pytest.approx(12.0 / 11.0, abs=1e-6)
    assert navs[D3] == pytest.approx(12.0 / 11.0, abs=1e-6)
    assert rep["meta"]["first_entry_date"] == D1
    assert rep["meta"]["rebalance_count"] == 1


# --------------------------------------------------------------------------- #
# 2. 涨停不可买（pct 阈值 + limit_list_d）
# --------------------------------------------------------------------------- #
def test_limit_up_cannot_buy():
    dates = [D0, D1]
    prices = {
        "000001.SZ": [10.0, 11.0],   # 最高分，但 D1 涨停（pct 9.9）
        "000002.SZ": [10.0, 10.5],   # 次高分，D1 被 limit_list_d 标 U
        "000003.SZ": [10.0, 10.0],
        "000004.SZ": [10.0, 10.0],
    }
    pct = {
        "000001.SZ": [0.0, 9.9],
        "000002.SZ": [0.0, 5.0],
        "000003.SZ": [0.0, 0.0],
        "000004.SZ": [0.0, 0.0],
    }
    market = _market(dates, prices, pct=pct, limit_up={D1: ["000002.SZ"]})
    factor = _factor(
        [
            (D0, "000001.SZ", 3.0),
            (D0, "000002.SZ", 2.5),
            (D0, "000003.SZ", 2.0),
            (D0, "000004.SZ", 1.0),
        ]
    )
    rep = bt.run_backtest(
        factor, D0, D1, top_n=2, cost_bp=0, rebalance="weekly", market=market, include_holdings=True
    )
    rebalance = rep["rebalances"][0]
    assert rebalance["holdings"] == ["000003.SZ", "000004.SZ"]
    assert "000001.SZ" not in rebalance["holdings"]
    assert rebalance["n_skipped_limit_up"] == 2
    assert rep["error_summary"]["buy_limit_up"] == 2
    assert _nav_map(rep)[D1] == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 3. 停牌/缺价容错
# --------------------------------------------------------------------------- #
def test_suspended_hold_is_retained_with_frozen_valuation():
    dates = [D0, D1, D2, D3, D4]
    prices = {
        "000001.SZ": [10.0, 10.0, None, None, 10.0],  # D2 起停牌，D2/D3 调仓时不可卖
        "000002.SZ": [10.0, 10.0, 10.0, 10.0, 10.0],
        "000003.SZ": [10.0, 10.0, 10.0, 10.0, 10.0],
    }
    market = _market(dates, prices)
    factor = _factor(
        [
            (D0, "000001.SZ", 2.0),
            (D0, "000002.SZ", 1.0),
            (D1, "000001.SZ", 0.0),
            (D1, "000003.SZ", 2.0),
            (D1, "000002.SZ", 1.0),
            (D2, "000003.SZ", 2.0),
            (D2, "000002.SZ", 1.0),
        ]
    )
    rep = bt.run_backtest(
        factor, D0, D4, top_n=2, cost_bp=0, rebalance="daily", market=market, include_holdings=True
    )
    nav_values = [row["nav"] for row in rep["nav"]]
    assert all(np.isfinite(v) and v > 0 for v in nav_values), nav_values
    assert rep["mdd"] <= 0
    # D2 调仓时 000001.SZ 停牌不可卖：保留持仓、记录错误、组合不因缺价断裂
    d2_rebalance = next(row for row in rep["rebalances"] if row["date"] == D2)
    assert d2_rebalance["n_unsellable"] == 1
    assert "000001.SZ" in d2_rebalance["holdings"]
    assert rep["error_summary"].get("sell_suspended", 0) >= 1
    assert rep["error_summary"].get("valuation_frozen", 0) >= 1
    assert rep["metrics"]["avg_holdings"] > 0
    # 停牌保留仓占用 top_n 名额：D2 只能再买 1 只，持仓数不超过 top_n
    assert d2_rebalance["n_target"] == 1
    assert d2_rebalance["n_holdings"] == 2
    assert rep["max_holdings"] <= 2
    assert all(row["n"] <= 2 for row in rep["nav"])


# --------------------------------------------------------------------------- #
# 4. 成本按换手扣减
# --------------------------------------------------------------------------- #
def test_cost_deducted_by_turnover():
    dates = [D0, D1, D2, D3]
    market = _market(dates, {"000001.SZ": [10.0] * 4, "000002.SZ": [10.0] * 4})
    factor = _factor([(D0, "000001.SZ", 1.0), (D1, "000002.SZ", 2.0), (D2, "000002.SZ", 2.0)])

    def _run(cost_bp):
        return bt.run_backtest(
            factor, D0, D3, top_n=1, cost_bp=cost_bp, rebalance="daily", market=market
        )

    zero, mid, high = _run(0.0), _run(30.0), _run(100.0)
    nav_zero = zero["nav"][-1]["nav"]
    nav_mid = mid["nav"][-1]["nav"]
    nav_high = high["nav"][-1]["nav"]
    assert nav_zero == pytest.approx(1.0, abs=1e-9)  # 零成本平盘：NAV 不动
    assert nav_high < nav_mid < nav_zero  # 成本越高 NAV 越低
    # 换手（双边口径 Σ|Δw|）：D1 首次建仓 1.0、D2 全换仓 2.0、D3 维持 0 → 平均 1.0
    assert mid["avg_turnover"] == pytest.approx(1.0, abs=1e-6)
    assert zero["avg_turnover"] == pytest.approx(1.0, abs=1e-6)
    # 100bp：D1 1.0×1% + D2 2.0×1%×0.99 → 末值 ≈ 0.9702
    assert nav_high == pytest.approx(0.9702, abs=1e-4)


# --------------------------------------------------------------------------- #
# 4b. 停牌/跌停保留仓占用 top_n 名额：持仓数恒 <= top_n
# --------------------------------------------------------------------------- #
def test_holdings_never_exceed_top_n_with_limit_down_retention():
    dates = [D0, D1, D2, D3]
    market = _market(
        dates,
        {
            "000001.SZ": [10.0, 10.0, 9.0, 9.0],   # D2 跌停不可卖
            "000002.SZ": [10.0] * 4,
            "000003.SZ": [10.0] * 4,
            "000004.SZ": [10.0] * 4,              # D2 新买入
            "000005.SZ": [10.0] * 4,              # D2 新买入
            "000006.SZ": [10.0] * 4,
        },
        pct={"000001.SZ": [None, None, -10.0, 0.0]},
    )
    factor = _factor(
        [
            (D0, "000001.SZ", 3.0),
            (D0, "000002.SZ", 2.0),
            (D0, "000003.SZ", 1.0),
            (D1, "000004.SZ", 3.0),
            (D1, "000005.SZ", 2.0),
            (D1, "000006.SZ", 1.0),
        ]
    )
    rep = bt.run_backtest(
        factor, D0, D3, top_n=3, cost_bp=0, rebalance="daily", market=market, include_holdings=True
    )
    d2_rebalance = next(row for row in rep["rebalances"] if row["date"] == D2)
    assert d2_rebalance["n_unsellable"] == 1
    assert d2_rebalance["n_slots"] == 2
    assert d2_rebalance["n_target"] == 2
    assert d2_rebalance["n_holdings"] == 3
    assert "000001.SZ" in d2_rebalance["holdings"]  # 跌停保留
    assert rep["max_holdings"] <= 3
    assert all(row["n"] <= 3 for row in rep["nav"])
    assert rep["error_summary"].get("sell_limit_down", 0) >= 1


# --------------------------------------------------------------------------- #
# 4c. MDD 对历史峰值逐日计算（与总收益不等）
# --------------------------------------------------------------------------- #
def test_mdd_measured_against_peak_not_total_return():
    dates = [D0, D1, D2, D3, D4]
    prices = {"000001.SZ": [10.0, 12.0, 14.4, 10.8, 13.2]}
    market = _market(dates, prices)
    factor = _factor([(D0, "000001.SZ", 1.0)])
    rep = bt.run_backtest(factor, D0, D4, top_n=1, cost_bp=0, rebalance="weekly", market=market)
    # NAV 路径：1.0 → 1.0 → 1.2 → 0.9 → 1.1
    assert rep["total_return"] == pytest.approx(0.1, abs=1e-6)
    assert rep["mdd"] == pytest.approx(-0.25, abs=1e-6)  # 1.2 → 0.9 的峰值回撤
    assert rep["mdd"] != pytest.approx(rep["total_return"], abs=1e-6)
    assert rep["mdd_detail"]["peak_date"] == D2
    assert rep["mdd_detail"]["trough_date"] == D3
    assert rep["mdd_detail"]["peak_nav"] == pytest.approx(1.2, abs=1e-6)
    assert rep["mdd_detail"]["trough_nav"] == pytest.approx(0.9, abs=1e-6)


# --------------------------------------------------------------------------- #
# 5. 输出 schema / NAV 恒正 / 参数校验
# --------------------------------------------------------------------------- #
def test_output_schema_and_validation():
    dates = [D0, D1, D2, D3]
    market = _market(dates, {"000001.SZ": [10.0, 11.0, 12.0, 12.0], "000002.SZ": [10.0] * 4})
    factor = _factor([(D0, "000001.SZ", 2.0), (D0, "000002.SZ", 1.0)])
    rep = bt.run_backtest(factor, D0, D3, top_n=1, cost_bp=30, rebalance="weekly", market=market)

    for key in (
        "schema_version",
        "label",
        "meta",
        "nav",
        "total_return",
        "annual_return",
        "mdd",
        "vol_ann",
        "avg_turnover",
        "avg_holdings",
        "ic",
        "errors",
        "error_summary",
        "rebalances",
        "metrics",
    ):
        assert key in rep, key
    assert rep["schema_version"] == "bt_harness.v1"
    nav = rep["nav"]
    assert len(nav) == len(dates)
    assert [row["date"] for row in nav] == dates
    assert all(row["nav"] > 0 for row in nav)
    assert rep["mdd"] <= 0
    assert isinstance(rep["total_return"], float)
    assert isinstance(rep["avg_holdings"], float)
    assert rep["ic"] is None  # 只有 2 只股票，不算全市场 IC
    assert isinstance(rep["errors"], list)
    assert isinstance(rep["rebalances"], list) and len(rep["rebalances"]) == 1
    assert rep["meta"]["top_n"] == 1 and rep["meta"]["cost_bp"] == 30
    assert rep["meta"]["rebalance"] == "weekly"
    assert rep["metrics"]["trading_days"] == len(dates)
    # 持仓数上限 + 基准对照 + MDD 明细
    assert rep["max_holdings"] <= 1
    assert all(row["n"] <= 1 for row in nav)
    benchmark = rep["benchmark"]
    for key in ("name", "total_return", "annual_return", "mdd", "vol_ann"):
        assert key in benchmark, key
    assert rep["excess_return"] == pytest.approx(rep["total_return"] - benchmark["total_return"], abs=1e-6)
    assert rep["mdd"] <= 0
    if rep["mdd"] < 0:
        assert rep["mdd_detail"] is not None
        assert rep["mdd_detail"]["mdd"] == pytest.approx(rep["mdd"], abs=1e-6)

    with pytest.raises(ValueError):
        bt.run_backtest(factor, D0, D3, rebalance="monthly", market=market)
    with pytest.raises(ValueError):
        bt.run_backtest(factor, D0, D3, cost_bp=-1, market=market)
    with pytest.raises(ValueError):
        bt.run_backtest(factor, D3, D0, market=market)
    with pytest.raises(ValueError):
        bt.run_backtest(pd.DataFrame({"date": [D0], "ts_code": ["000001.SZ"]}), D0, D3, market=market)
    with pytest.raises(ValueError):
        bt.run_backtest(_factor([("2026-1-5", "000001.SZ", 1.0)]), D0, D3, market=market)


# --------------------------------------------------------------------------- #
# 6. 空信号容错
# --------------------------------------------------------------------------- #
def test_empty_signal_is_tolerated():
    dates = [D0, D1, D2]
    market = _market(dates, {"000001.SZ": [10.0, 10.0, 10.0]})
    empty = pd.DataFrame(columns=["date", "ts_code", "score"])
    rep = bt.run_backtest(empty, D0, D2, top_n=10, cost_bp=30, rebalance="weekly", market=market)
    assert [row["nav"] for row in rep["nav"]] == [1.0, 1.0, 1.0]
    assert rep["total_return"] == 0.0
    assert rep["rebalance_count"] == 0
    assert rep["avg_turnover"] is None
    assert "empty_factor" in rep["error_summary"]

    all_nan = pd.DataFrame({"date": [D0], "ts_code": ["000001.SZ"], "score": [np.nan]})
    rep2 = bt.run_backtest(all_nan, D0, D2, top_n=10, market=market)
    assert rep2["total_return"] == 0.0
    assert rep2["rebalance_count"] == 0
    assert "empty_factor" in rep2["error_summary"]


# --------------------------------------------------------------------------- #
# 7. 周频 vs 日频调仓时点
# --------------------------------------------------------------------------- #
def test_weekly_vs_daily_rebalance_schedule():
    dates = [
        "20260105", "20260106", "20260107", "20260108", "20260109",  # 第 1 周
        "20260112", "20260113", "20260114", "20260115", "20260116",  # 第 2 周
        "20260119", "20260120",  # 第 3 周（只到周二）
    ]
    market = _market(dates, {"000001.SZ": [10.0] * len(dates)})
    factor = _factor([(date, "000001.SZ", 1.0) for date in dates])

    weekly = bt.run_backtest(factor, dates[0], dates[-1], top_n=1, cost_bp=0, rebalance="weekly", market=market)
    daily = bt.run_backtest(factor, dates[0], dates[-1], top_n=1, cost_bp=0, rebalance="daily", market=market)

    # 周频：每周最后交易日发信号 → T+1 建仓（第三个信号 1/20 的下一交易日缺数据，跳过）
    assert weekly["meta"]["rebalance_count"] == 2
    assert [row["date"] for row in weekly["rebalances"]] == ["20260112", "20260119"]
    assert [row["signal_date"] for row in weekly["rebalances"]] == ["20260109", "20260116"]
    assert weekly["error_summary"].get("entry_out_of_range", 0) >= 1
    # 日频：每个交易日都调仓，仅期末最后一个信号无法建仓
    assert daily["meta"]["rebalance_count"] == len(dates) - 1
    assert daily["rebalances"][0]["signal_date"] == dates[0]
    assert daily["rebalances"][0]["date"] == dates[1]


# --------------------------------------------------------------------------- #
# 8. 全市场因子 → 按日截面 IC
# --------------------------------------------------------------------------- #
def test_ic_computed_for_market_wide_factor():
    dates = [D0, "20260106", "20260107", "20260108"]
    codes = [f"00000{i}.SZ" for i in range(1, 7)]
    base = np.array([10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
    prices = {code: [float(base[j] * (1.0 + 0.01 * step * (j + 1))) for step in range(len(dates))]
              for j, code in enumerate(codes)}
    market = _market(dates, prices)
    rows = []
    for date in dates[:-1]:  # 最后一天没有前向收益，IC 只统计前 3 天
        for rank, code in enumerate(codes, start=1):
            rows.append((date, code, float(rank)))
    factor = _factor(rows)
    rep = bt.run_backtest(
        factor,
        dates[0],
        dates[-1],
        top_n=2,
        cost_bp=0,
        rebalance="weekly",
        market=market,
        min_ic_cross_section=5,
    )
    assert rep["ic"] is not None, rep["error_summary"]
    assert rep["ic"]["n_days"] == 3
    assert rep["ic"]["mean_rank"] > 0.99  # 分数排序与次日收益排序完全一致
    assert rep["ic_mean"] is not None

    # 门槛不满足时应为 None（默认 100 > 6）
    rep2 = bt.run_backtest(factor, dates[0], dates[-1], top_n=2, cost_bp=0, rebalance="weekly", market=market)
    assert rep2["ic"] is None


# --------------------------------------------------------------------------- #
# 9. CLI 可用性
# --------------------------------------------------------------------------- #
def test_cli_help_available():
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(BT_PATH), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "run" in proc.stdout
    proc_run = subprocess.run(
        [sys.executable, "-X", "utf8", str(BT_PATH), "run", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc_run.returncode == 0, proc_run.stderr
    for flag in ("--factor", "--start", "--end", "--top", "--cost-bp", "--rebalance"):
        assert flag in proc_run.stdout


# --------------------------------------------------------------------------- #
# 10. 真实数据仓 loader 冒烟（数据缺失则跳过，不影响口径测试）
# --------------------------------------------------------------------------- #
def test_load_market_real_data_smoke():
    daily_path = bt.DATA / "daily" / "d_20260105.parquet"
    adj_path = bt.DATA / "adj_factor" / "d_20260105.parquet"
    if not daily_path.exists() or not adj_path.exists():
        pytest.skip("真实行情数据仓不可用，跳过 loader 冒烟")
    market = bt.load_market("20260105", "20260106", with_limits=False, with_names=False)
    assert market.dates == ["20260105", "20260106"]
    assert len(market.codes) > 1000
    finite_ratio = float(np.isfinite(market.adj).mean())
    assert finite_ratio > 0.8
    assert float(np.nanmedian(market.adj[0])) > 0
