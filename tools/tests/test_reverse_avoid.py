# -*- coding: utf-8 -*-
"""tools/tests/test_reverse_avoid.py · 规避组合深测（tools/research/reverse_avoid_test.py）单元测试。

覆盖（任务验收要求 ≥4）：
  1. 信号窗口 `trigger_window` 语义：过去 N 日（含当日）触发 == 剔除，N=1/5 边界正确、NaN 视为未触发；
  2. 涨停/可买入池：按板块涨跌幅限制识别涨停（主板 10%/创业板科创板 20%/北交所 30%），
     基准与规避组合同口径剔除 T 日涨停与 T+1 无数据股票；
  3. 组合日收益/覆盖度/churn/容量：纯函数数学口径（等权均值、Jaccard、成交额单位千元→亿元）；
  4. 规避超额方向：合成面板中「触发股票未来跑输」→ 规避组合毛超额为正；「触发股票跑赢」→ 为负；
  5. 结果 schema 与 JSON 可序列化：`assess_war_stop` 返回 coverage/capacity/turnover/gross/net/
     yearly/stress/stratified/exposure/verdict 全字段；`classify` 判定规则可单测；
  6. PIT 静态检查：模块内 `shift(-n)`（前视）只允许出现在 `next_day_return`（收益对齐用），
     信号/窗口/组合函数不得用未来数据。

不跑全量真实数据：合成面板 80 天 × 40 只，单测整体 < 15s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_reverse_avoid_test.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "tools" / "research"
sys.path.insert(0, str(RESEARCH))

import factor_lib as fl          # noqa: E402
import reverse_avoid_test as ra  # noqa: E402

MODULE = RESEARCH / "reverse_avoid_test.py"
N_DATES, N_CODES = 80, 40


# ------------------------------------------------------------------ 合成面板
def make_panel(n_dates: int = N_DATES, n_codes: int = N_CODES, seed: int = 11,
               ret: np.ndarray | None = None) -> fl.Panel:
    """随机面板（字段齐全、复权=收盘）；可传入自定义日收益矩阵以构造方向性场景。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    if ret is None:
        ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre_close = np.vstack([close[0:1], close[:-1]])
    pct_chg = (close / pre_close - 1.0) * 100.0
    open_ = pre_close * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.005, close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.005, close.shape)))
    vol = rng.lognormal(11.0, 0.5, size=close.shape)
    amount = vol * close * 0.1

    def frame(arr):
        return pd.DataFrame(arr, index=dates, columns=codes)

    fields = {
        "open": frame(open_), "high": frame(high), "low": frame(low), "close": frame(close),
        "pre_close": frame(pre_close), "pct_chg": frame(pct_chg), "vol": frame(vol),
        "amount": frame(amount), "adj_close": frame(close),
    }
    return fl.Panel(fields)


# ------------------------------------------------------------------ 1. 信号窗口
def test_trigger_window_inclusive_and_lag():
    dates = pd.bdate_range("2026-01-05", periods=8).strftime("%Y%m%d")
    codes = ["000001.SZ", "000002.SZ"]
    trig = pd.DataFrame(False, index=dates, columns=codes)
    trig.loc[dates[2], "000001.SZ"] = True     # 第 3 天触发
    trig = trig.astype(float)
    trig.loc[dates[3], "000002.SZ"] = np.nan   # 无效（NaN）视为未触发

    w1 = ra.trigger_window(trig, 1)
    assert w1.loc[dates[2], "000001.SZ"] and not w1.loc[dates[3], "000001.SZ"]
    assert not w1.loc[dates[3], "000002.SZ"]  # NaN 不算触发

    # n=3：第 5 天（index 4）只覆盖 index 2,3,4 → 覆盖第 3 天；第 6 天（index 5）不覆盖
    w3 = ra.trigger_window(trig, 3)
    assert w3.loc[dates[4], "000001.SZ"] and not w3.loc[dates[5], "000001.SZ"]
    # n=5：index 6 覆盖 index 2..6 → 仍覆盖
    w5 = ra.trigger_window(trig, 5)
    assert w5.loc[dates[6], "000001.SZ"]
    # 窗口单调：N 越大剔除集合是 N 小的超集
    assert bool((w1.to_numpy() <= w5.to_numpy()).all())


# ------------------------------------------------------------------ 2. 涨停/可买入池
def test_limit_up_mask_by_board_and_buyable():
    panel = make_panel(n_dates=6, n_codes=4)
    codes = list(panel.codes)
    # 收盘后覆盖 pct_chg：主板 10% / 创业板 20% / 北交所 30% 的阈值差异
    panel.fields["pct_chg"] = pd.DataFrame(
        [[10.0, 10.0, 20.0, 30.0]] * 6, index=panel.dates, columns=codes)
    panel.codes = pd.Index(["000001.SZ", "300001.SZ", "300002.SZ", "830001.BJ"])
    for k, v in panel.fields.items():
        v.columns = panel.codes
    lim = ra.limit_up_mask(panel)
    assert bool(lim.iloc[0, 0])          # 主板 +10% → 涨停
    assert not bool(lim.iloc[0, 1])      # 创业板 +10% → 未涨停
    assert bool(lim.iloc[0, 2])          # 创业板 +20% → 涨停
    assert bool(lim.iloc[0, 3])          # 北交所 +30% → 涨停

    ret_next = ra.next_day_return(panel)
    buyable = ra.build_buyable(panel, ret_next)
    assert not bool(buyable.iloc[0, 0]) and not bool(buyable.iloc[0, 2])
    assert bool(buyable.iloc[0, 1])      # 未涨停且 T+1 有数据 → 可买
    assert not bool(buyable.iloc[-1, 1])  # 最后一日无 T+1 收益 → 不可买


# ------------------------------------------------------------------ 3. 数学口径
def test_portfolio_coverage_churn_capacity_math():
    dates = pd.date_range("2026-01-05", periods=4).strftime("%Y%m%d")
    codes = ["a", "b", "c", "d"]
    ret = pd.DataFrame(
        [[0.10, 0.02, np.nan, 0.00],
         [0.00, 0.00, 0.00, 0.00],
         [-0.10, 0.04, 0.06, 0.02],
         [0.00, 0.00, 0.00, 0.00]], index=dates, columns=codes)
    hold = pd.DataFrame([[True, True, False, False], [True, True, True, False],
                         [True, False, True, True], [False, False, False, False]],
                        index=dates, columns=codes)
    r = ra.portfolio_returns(ret, hold)
    assert r.iloc[0] == pytest.approx((0.10 + 0.02) / 2)     # NaN 不参与等权均值
    assert r.iloc[2] == pytest.approx((-0.10 + 0.06 + 0.02) / 3)

    buy = pd.DataFrame(True, index=dates, columns=codes)
    excl = pd.DataFrame(False, index=dates, columns=codes)
    excl.iloc[1, :2] = True                                   # 第 2 天剔除 2/4
    cov, n_ex = ra.coverage_series(excl, buy)
    assert cov.iloc[1] == pytest.approx(0.5) and n_ex.iloc[1] == 2
    # churn：第 2 天剔除 {a,b}，第 3 天剔除 {a} → Jaccard 距离 = 1 - 1/2 = 0.5
    excl2 = pd.DataFrame(False, index=dates, columns=codes)
    excl2.iloc[1, :2] = True
    excl2.iloc[2, 0] = True
    churn = ra.set_churn(excl2)
    assert churn.iloc[2] == pytest.approx(0.5)

    # 容量：amount 单位千元 → 亿元；被剔除股票 amount=100000 千元 = 1e8 元 = 1 亿
    # 逐日取被剔除股票中位数后再对天取均值：第 2 天 {a=2亿,b=1亿}→1.5，第 3 天 {a=2亿}→2.0 → 均值 1.75
    amount_yuan = pd.DataFrame(1e8, index=dates, columns=codes)
    amount_yuan.iloc[:, 0] = 2e8                              # a 股 2 亿
    cap = ra.capacity_stats(amount_yuan, excl2, buy)
    assert cap["excluded_median_amount_yi"] == pytest.approx(1.75, abs=1e-6)
    assert cap["excluded_amount_ge_1yi_share"] == pytest.approx(1.0)


# ------------------------------------------------------------------ 4. 规避超额方向
def _directional_panel(triggered_underperform: bool) -> tuple[fl.Panel, pd.DataFrame, int, int]:
    """构造方向性场景：被剔除（触发）股票未来收益 ±2%，其余 0；返回 (panel, signal, start_i, end_i)。"""
    n_dates, n_codes = 70, 40
    rng = np.random.default_rng(3)
    ret = rng.normal(0.0, 0.005, size=(n_dates, n_codes))
    ret[:, :8] += -0.02 if triggered_underperform else 0.02   # 8/40 只长期跑输/跑赢
    panel = make_panel(n_dates=n_dates, n_codes=n_codes, ret=ret)
    signal = pd.DataFrame(0.0, index=panel.dates, columns=panel.codes)
    signal.iloc[:, :8] = 1.0                                  # 前 8 只每日触发
    return panel, signal, 25, n_dates - 2


@pytest.mark.parametrize("underperform,expect_sign", [(True, 1), (False, -1)])
def test_avoidance_excess_direction(underperform, expect_sign):
    panel, signal, i0, i1 = _directional_panel(underperform)
    res = ra.assess_war_stop(panel, "LX20_BULL_STOCK_THREE_ABSOLUTES", 5,
                             start=str(panel.dates[i0]), end=str(panel.dates[i1]),
                             signal=signal)
    assert res["coverage"]["avg_pct"] == pytest.approx(20.0, abs=0.5)   # 8/40
    assert res["gross"]["ann_excess_pct"] * expect_sign > 0
    assert res["gross"]["daily_t"] * expect_sign > 2                    # 方向显著
    assert res["verdict"]["tradable_avoidance"] is underperform
    if underperform:
        # 层内对照（无市值/行业信息 → 单层）方向应与毛超额一致
        assert res["stratified"]["avoidance_beyond_comp_ann_pct"] > 0


# ------------------------------------------------------------------ 5. schema / classify / 落盘
def test_assess_schema_json_and_classify_rules():
    panel, signal, i0, i1 = _directional_panel(True)
    res = ra.assess_war_stop(panel, "LX06_YIN_LINE_TACTICS", 1,
                             start=str(panel.dates[i0]), end=str(panel.dates[i1]),
                             signal=signal)
    for key in ("n", "factor", "direction", "eval", "coverage", "capacity", "turnover",
                "gross", "net", "yearly", "stress", "stratified", "exposure", "verdict"):
        assert key in res, key
    for key in ("avg_pct", "median_pct", "p95_pct", "avg_excluded_count",
                "avg_holdings_avoid", "avg_holdings_bench"):
        assert key in res["coverage"], key
    for key in ("spread_ann_pct", "spread_t", "avoidance_beyond_comp_ann_pct",
                "avoidance_beyond_comp_t", "matched_days"):
        assert key in res["stratified"], key
    for key in ("significant_excess", "tradable_avoidance", "beyond_cap_industry",
                "risk_rule_candidate", "verdict"):
        assert key in res["verdict"], key
    assert "2026" in res["yearly"]
    assert json.loads(json.dumps(res, ensure_ascii=False))["n"] == 1

    # classify 纯函数：可交易 / 覆盖过低 / 成本吃掉 / 方向为正不显著 / 无超额
    base = dict(gross_ann=3.0, net_ann=2.0, daily_t=2.5, boot_p=0.01, coverage_pct=5.0,
                beyond_t=2.5, beyond_ann=2.0, bear_ann=1.0, bear_t=2.0)
    assert ra.classify(**base)["tradable_avoidance"] is True
    assert ra.classify(**{**base, "coverage_pct": 0.5})["tradable_avoidance"] is False
    assert "换手成本" in ra.classify(**{**base, "net_ann": -1.0})["verdict"]
    assert "不显著" in ra.classify(**{**base, "daily_t": 0.5})["verdict"]
    assert "无正超额" in ra.classify(**{**base, "gross_ann": -2.0})["verdict"]


def test_synthetic_run_payload_and_markdown(tmp_path, monkeypatch):
    panel = make_panel(n_dates=60, n_codes=30, seed=5)
    # 不读真实 daily_basic/stock_basic，保持单测离线快速
    monkeypatch.setattr(ra, "load_circ_mv", lambda *a, **k: None)
    monkeypatch.setattr(ra, "load_industry", lambda *a, **k: None)
    payload = ra.run(start=str(panel.dates[20]), end=str(panel.dates[-2]), ns=(1, 5),
                     panel=panel, n_boot=20)
    assert set(payload) == {"meta", "war_stops", "summary", "conclusions"}
    assert len(payload["war_stops"]) == 4 and len(payload["summary"]) == 4
    assert all(str(n) in payload["war_stops"][sid]["by_n"]
               for sid in ra.WAR_ORDER for n in (1, 5))
    # JSON 可序列化 + Markdown 可渲染
    json.loads(json.dumps(payload, ensure_ascii=False))
    md = ra.render_markdown(payload)
    assert "汇总表" in md and "总判定" in md and "局限" in md
    jp, mp = ra.write_outputs(payload, tmp_path / "r.json", tmp_path / "r.md")
    assert Path(jp).exists() and Path(mp).exists()


# ------------------------------------------------------------------ 6. PIT 静态检查
def test_no_lookahead_shift_except_forward_return():
    """模块内 shift(-n) 只允许出现在 next_day_return（用于对齐 T+1 收益），不得用于信号/窗口。"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    allowed = {"next_day_return"}
    offenders = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "shift"
                    and node.args and isinstance(node.args[0], ast.UnaryOp)
                    and isinstance(node.args[0].op, ast.USub)):
                if not (set(self.stack) & allowed):
                    offenders.append((self.stack[-1] if self.stack else "?", ast.unparse(node)))
            self.generic_visit(node)

    Visitor().visit(tree)
    assert offenders == [], f"发现前视 shift(-n)：{offenders}"
