# -*- coding: utf-8 -*-
"""tools/tests/test_lx20_avoid_rule.py · LX20 规避规则深化（tools/research/lx20_avoid_rule.py）单元测试。

覆盖（任务验收要求 ≥4）：
  1. 成交价口径：三种 execution（T 收盘 / T+1 开盘 / T+1 收盘）的收益矩阵数学口径正确
     （后复权价比值），且前视 `shift(-k)` 只允许出现在 execution_returns；
  2. 可交易性：涨停（收盘/开盘）、一字涨停/一字跌停、停牌（T/T+1/T+2）掩码按板块阈值识别；
     `shift_future` 未来对齐正确；
  3. 可执行池：entry 日涨停不可买、exit 日一字跌停不可卖、停牌跳过 —— 三种口径下统一剔除；
  4. 参数敏感性：`trigger_window_np`（cumsum 版）与 wave3 pandas 版 window 完全一致 N=1/3/5；
  5. 组合实现：降配公式（w=0 全剔除 / w=1 基准 / w=0.5 中间）精确；增量换手 = 降配换手 − 基准换手；
  6. 容量：给定 ADV20、变动只数与增量换手，AUM 上限公式（10% 参与率、换手/建仓两口径取小）正确；
  7. 规则规格：合成面板 run() 输出可序列化 JSON，含 rules/threshold/coverage/capacity/risk/evidence
     与上线前 checklist，Markdown 可渲染。

不跑真实全量数据：合成面板 90 天 × 40 只，单测整体 < 15s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_lx20_avoid_rule.py
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

import factor_lib as fl                 # noqa: E402
import lx20_avoid_rule as lx           # noqa: E402
import reverse_avoid_test as rav       # noqa: E402

MODULE = RESEARCH / "lx20_avoid_rule.py"


# ------------------------------------------------------------------ 合成面板
def make_panel(n_dates: int = 90, n_codes: int = 40, seed: int = 13,
               eligible: bool = True) -> fl.Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre = np.vstack([close[0:1], close[:-1]])
    pct = (close / pre - 1.0) * 100.0
    open_ = pre * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, close.shape)))
    vol = rng.lognormal(11.0, 0.4, size=close.shape)
    amount = vol * close * 0.1

    def fr(x):
        return pd.DataFrame(x, index=dates, columns=codes)

    fields = {"open": fr(open_), "high": fr(high), "low": fr(low), "close": fr(close),
              "pre_close": fr(pre), "pct_chg": fr(pct), "vol": fr(vol),
              "amount": fr(amount), "adj_close": fr(close)}
    elig = pd.DataFrame(eligible, index=dates, columns=codes) if eligible else None
    return fl.Panel(fields, eligible=elig)


def _empty_panel(n_dates: int, n_codes: int) -> fl.Panel:
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]

    def fr(scale=10.0):
        return pd.DataFrame(scale, index=dates, columns=codes)
    fields = {"open": fr(), "high": fr(), "low": fr(), "close": fr(), "pre_close": fr(),
              "pct_chg": fr(0.0), "vol": fr(1e5), "amount": fr(1e5), "adj_close": fr()}
    return fl.Panel(fields, eligible=pd.DataFrame(True, index=dates, columns=codes))


# ------------------------------------------------------------------ 1. 成交价口径
def test_execution_returns_math():
    panel = _empty_panel(6, 1)
    code = panel.codes[0]
    idx = panel.dates
    close = [10.0, 11.0, 12.1, 13.31, 14.641, 16.1051]
    open_ = [9.0, 10.5, 11.5, 12.8, 14.0, 15.5]
    for name, vals in (("close", close), ("open", open_)):
        panel.fields[name] = pd.DataFrame({code: vals}, index=idx)
    panel.fields["adj_close"] = pd.DataFrame({code: close}, index=idx)  # adj_factor=1
    rets = lx.execution_returns(panel)

    t0 = idx[0]
    assert rets["T_CLOSE"].loc[t0, code] == pytest.approx(close[1] / close[0] - 1)
    assert rets["T1_CLOSE"].loc[t0, code] == pytest.approx(close[2] / close[1] - 1)
    assert rets["T1_OPEN"].loc[t0, code] == pytest.approx(open_[2] / open_[1] - 1)
    # 最后两日无完整 entry/exit → NaN
    assert np.isnan(rets["T_CLOSE"].iloc[-1, 0])
    assert np.isnan(rets["T1_CLOSE"].iloc[-2:, 0]).all()
    assert np.isnan(rets["T1_OPEN"].iloc[-2:, 0]).all()
    # 后复权开盘价：adj_factor = adj_close/close = 2 时 adj_open = open×2
    assert lx.build_adj_open(panel).iloc[0, 0] == pytest.approx(open_[0])


def test_shift_future_alignment():
    mask = np.array([True, False, True, False, False])
    assert lx.shift_future(mask, 0).tolist() == mask.tolist()
    assert lx.shift_future(mask, 1).tolist() == [False, True, False, False, False]
    assert lx.shift_future(mask, 2).tolist() == [True, False, False, False, False]
    assert lx.shift_future(mask, 9).sum() == 0


# ------------------------------------------------------------------ 2. 一字板/涨停识别
def test_tradeability_masks_board_thresholds():
    panel = _empty_panel(6, 5)
    codes = ["000001.SZ", "300001.SZ", "300002.SZ", "830001.BJ", "000002.SZ"]
    panel.codes = pd.Index(codes)
    for name, df in panel.fields.items():
        df.columns = panel.codes
    # 第 0 日：主板+10(涨停, 一字)/创业板+10(非涨停)/创业板+20(一字涨停)/北交所+30(跌停? up)/主板平
    vals = [10.0, 10.0, 20.0, 30.0, 0.0]
    panel.fields["pct_chg"].iloc[0, :] = vals
    panel.fields["pre_close"].iloc[0, :] = 10.0
    panel.fields["open"].iloc[0, :] = 10.0
    panel.fields["high"].iloc[0, :] = 10.0
    panel.fields["low"].iloc[0, :] = 10.0
    panel.fields["close"].iloc[0, :] = 10.0
    masks = lx.tradeability_masks(panel)
    assert masks["limit_up_close"][0].tolist() == [True, False, True, True, False]
    assert masks["oneword_up"][0].tolist() == [True, False, True, True, False]
    # 第 1 日：主板 -10 → 一字跌停（open=high=low=close 且跌停）
    panel.fields["pct_chg"].iloc[1, 0] = -10.0
    masks = lx.tradeability_masks(panel)
    assert bool(masks["oneword_down"][1, 0])
    assert not bool(masks["oneword_down"][1, 1])
    # 停牌：vol=0 → tradable_day False
    panel.fields["vol"].iloc[2, 1] = 0.0
    masks = lx.tradeability_masks(panel)
    assert not bool(masks["tradable_day"][2, 1])


# ------------------------------------------------------------------ 3. 可执行池约束
def test_build_universe_entry_exit_constraints():
    panel = _empty_panel(6, 3)
    n_t, n_c = 6, 3
    ret = pd.DataFrame(np.zeros((n_t, n_c)) + 0.01, index=panel.dates, columns=panel.codes)
    masks = {"limit_up_close": np.zeros((n_t, n_c), bool),
             "limit_up_open": np.zeros((n_t, n_c), bool),
             "oneword_up": np.zeros((n_t, n_c), bool),
             "oneword_down": np.zeros((n_t, n_c), bool),
             "tradable_day": np.ones((n_t, n_c), bool)}
    uni = lx.build_universe(panel, ret, masks, "T_CLOSE")
    assert uni[3, 0]                                # 无约束时可执行
    masks["limit_up_close"][3, 0] = True            # T 日收盘涨停 → T_CLOSE 不可买
    uni = lx.build_universe(panel, ret, masks, "T_CLOSE")
    assert not uni[3, 0]
    # T1_CLOSE：T+1 日收盘涨停不可买
    masks2 = {k: v.copy() for k, v in masks.items()}
    masks2["limit_up_close"][:] = False
    masks2["limit_up_close"][4, 0] = True
    uni_t1 = lx.build_universe(panel, ret, masks2, "T1_CLOSE")
    assert not uni_t1[3, 0] and uni_t1[2, 0]
    # exit 日一字跌停不可卖（T_CLOSE 出场在 T+1）
    masks3 = {k: v.copy() for k, v in masks.items()}
    masks3["oneword_down"][:] = False
    masks3["oneword_down"][4, 1] = True
    uni3 = lx.build_universe(panel, ret, masks3, "T_CLOSE")
    assert not uni3[3, 1]
    # 停牌（无数据）：ret=NaN 自动剔除（决策日 T 行需 T+1 收益，故影响 T=4 一行）
    ret_na = ret.copy()
    ret_na.iloc[4, 2] = np.nan
    uni_na = lx.build_universe(panel, ret_na, masks3, "T_CLOSE")
    assert not uni_na[4, 2]


# ------------------------------------------------------------------ 4. 参数敏感性窗口
def test_trigger_window_np_matches_wave3():
    rng = np.random.default_rng(7)
    trig = rng.random((60, 12)) < 0.15
    dates = pd.bdate_range("2026-01-05", periods=60).strftime("%Y%m%d").tolist()
    codes = [f"{i:06d}.SZ" for i in range(1, 13)]
    df = pd.DataFrame(trig.astype(float), index=dates, columns=codes)
    for n in (1, 3, 5):
        expected = rav.trigger_window(df, n).to_numpy()
        got = lx.trigger_window_np(trig, n)
        assert np.array_equal(got, expected), n
    # 覆盖度随 N 单调不减
    cov = [lx.trigger_window_np(trig, n).sum() for n in (1, 3, 5, 10, 20)]
    assert all(a <= b for a, b in zip(cov, cov[1:]))


# ------------------------------------------------------------------ 5. 降配/换手
def test_deweight_returns_and_incremental_turnover():
    ret = np.array([[0.01, 0.02, 0.03, 0.04]])
    uni = np.array([[True, True, True, True]])
    excl = np.array([[True, True, False, False]])
    bench, avoid0 = lx.deweight_returns(ret, uni, excl, 0.0)
    assert bench[0] == pytest.approx(0.025)
    assert avoid0[0] == pytest.approx((0.03 + 0.04) / 2)
    _, avoid1 = lx.deweight_returns(ret, uni, excl, 1.0)
    assert avoid1[0] == pytest.approx(bench[0])                    # w=1 退化基准
    _, avoid05 = lx.deweight_returns(ret, uni, excl, 0.5)
    assert avoid05[0] == pytest.approx((0.03 + 0.04 + 0.5 * (0.01 + 0.02)) / (2 + 1))
    # 增量换手 = 降配换手 − 基准换手（≥0）
    uni2 = np.ones((6, 10), bool)
    excl2 = np.zeros((6, 10), bool)
    excl2[range(6), range(0, 6)] = True                            # 每日剔除不同个股（换手大）
    ret2 = np.zeros((6, 10))
    ta, tb, inc = lx.incremental_turnover(uni2, excl2, 0.0, ret2)
    assert (inc[1:] >= 0).all()
    assert np.allclose(inc[1:], np.maximum(ta[1:] - tb[1:], 0.0))


# ------------------------------------------------------------------ 6. 容量
def test_capacity_aum_formula():
    n_t, n_c = 30, 40
    adv20 = np.full((n_t, n_c), 1e8)                                # 每只 1 亿元 ADV
    uni = np.ones((n_t, n_c), bool)
    excl = np.zeros((n_t, n_c), bool)
    excl[:, :4] = True                                              # 4 只长期剔除
    excl[::2, 4:14] = True                                          # 10 只隔日变化 → 日变动 10 只
    inc_turn = np.full(n_t, 0.02)
    inc_turn[0] = np.nan
    res = lx.capacity_estimates(adv20, uni, excl, inc_turn, cap_q=None)
    assert res["aum_trade_cap_yi"] is not None and res["aum_build_cap_yi"] is not None
    # 换手口径：0.1 × 1e8 × 10 只 / 0.02 = 5e9 元 = 50 亿
    assert res["aum_trade_cap_yi"] == pytest.approx(50.0, rel=0.05)
    # 建仓口径：0.1 × 1e8 × 40 只 = 4e8 元 = 4 亿
    assert res["aum_build_cap_yi"] == pytest.approx(4.0, rel=0.05)
    assert res["aum_cap_yi"] == pytest.approx(min(res["aum_trade_cap_yi"], res["aum_build_cap_yi"]))


# ------------------------------------------------------------------ 7. 规则规格 + 全流程
def test_run_rule_spec_schema_and_markdown(monkeypatch):
    panel = make_panel(n_dates=90, n_codes=40, seed=13)
    monkeypatch.setattr(rav, "load_circ_mv", lambda *a, **k: None)
    payload = lx.run(start=str(panel.dates[30]), end=str(panel.dates[-2]),
                     ns=(1, 5), panel=panel, n_boot=20)
    assert set(payload) == {"meta", "sensitivity", "deweight", "tradeability", "capacity",
                            "primary_capacity", "grid", "rule_spec", "conclusions"}
    assert len(payload["sensitivity"]) == 2 * len(lx.EXECUTIONS)
    assert len(payload["deweight"]) == len(lx.DEWEIGHTS)
    spec = payload["rule_spec"]
    for key in ("rule_id", "library_slot", "status", "parameters", "rules", "threshold",
                "coverage_pct", "capacity", "risk", "evidence", "promotion_checklist",
                "missing_before_go_live"):
        assert key in spec, key
    assert spec["library_slot"] == "S05"
    assert spec["parameters"]["lookback_n_days"] in (1, 5)
    assert len(spec["rules"]) >= 4 and len(spec["promotion_checklist"]) >= 4
    assert len(spec["missing_before_go_live"]) >= 3
    assert isinstance(payload["conclusions"]["bullets"], list) and payload["conclusions"]["bullets"]
    assert payload["conclusions"]["worth_s05_candidate"] in (True, False)
    # 参数敏感性：覆盖度随 N 单调不减（同成交口径）
    sens = {(m["n"], m["execution"]): m["coverage"]["avg_pct"] for m in payload["sensitivity"]}
    for execution in lx.EXECUTIONS:
        if (1, execution) in sens and (5, execution) in sens:
            assert sens[(1, execution)] <= sens[(5, execution)] + 1e-9
    json.loads(json.dumps(payload, ensure_ascii=False))
    md = lx.render_markdown(payload)
    assert "参数敏感性" in md and "可注册规则规格" in md and "上线前还缺什么" in md


# ------------------------------------------------------------------ 8. PIT 静态检查
def test_no_lookahead_shift_outside_execution_returns():
    """shift(-k) 只允许出现在 execution_returns（构造未来收益）；其余函数不得引用未来数据。"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    allowed = {"execution_returns"}
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
