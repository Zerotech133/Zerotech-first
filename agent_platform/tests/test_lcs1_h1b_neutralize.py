# -*- coding: utf-8 -*-
"""H1b R2 中性化端点 测试（WO-LCS1-H1B-NEUTRAL-20261031，离线合成数据）。

覆盖：同事件日匹配命中（板块 + SW L1 PIT + 市值三分位 + 换手三分位）/
预注册回退链（去行业 → 去换手 → 不计入端点）/ 单元<10 / 无控制 /
事件行业缺失按预注册跳过行业维度 / 事件截面字段缺失 / neutral_net =
raw_net − 控制均值 / R2 门通过/不通过（含原因）/ 中性化显著性 / 未匹配率
告警 / evaluate_all 集成。

纪律：全部合成 records，不跑真实回测、不联网、不写生产。
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
_TOOL = WT / "tools" / "lcs1_h1b_harness.py"
_spec = importlib.util.spec_from_file_location("lcs1_h1b_harness", str(_TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CAL = [(date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(420)]
T, ENTRY, EXIT = CAL[320], CAL[326], CAL[330]
EVENT_CODE = "600000"
PREFIX = {"main": "600", "chinext": "300", "star": "688", "bj": "830"}


# ---------------------------------------------------------------------------
# 合成 matches/controls（接口只吃 records，不依赖真实数据）
# ---------------------------------------------------------------------------
def _bar(day, open_px=10.0, close=10.0, **extra):
    bar = {"date": day, "open": open_px,
           "high": max(open_px, close), "low": min(open_px, close),
           "close": close, "volume": 1000.0, "amount": 50_000_000.0,
           "is_st": False}
    bar.update(extra)
    return bar


def _control_group(count, start=1, ret=0.05, board="main", sw="801080",
                   mv=1000.0, turnover=0.02):
    """控制股：t 日截面字段 + 与事件相同 entry/exit 日期的收益。"""
    out = {}
    prefix = PREFIX[board]
    for i in range(count):
        code = "%s%03d" % (prefix, start + i)
        out[code] = {"list_date": CAL[0], "bars": [
            _bar(T, 10.0, 10.0, sw_l1=sw, circ_mv=mv,
                 turnover_rate=turnover),
            _bar(ENTRY, 10.0, 10.0),
            _bar(EXIT, 10.0, 10.0 * (1.0 + ret)),
        ]}
    return out


def _event_bar(**over):
    bar = _bar(T, 10.0, 11.0, sw_l1="801080", circ_mv=1000.0,
               turnover_rate=0.02)
    bar.update(over)
    return bar


def _panel(controls, event_bar=None):
    codes = {EVENT_CODE: {"list_date": CAL[0],
                          "bars": [event_bar or _event_bar()]}}
    codes.update(controls)
    return {"calendar": CAL, "codes": codes}


def _trade(code=EVENT_CODE, raw_net=0.099, event_type="E1"):
    return {"status": "traded", "ts_code": code, "event_type": event_type,
            "event_date": T, "entry_date": ENTRY, "exit_date": EXIT,
            "entry_open": 10.0, "exit_close": 10.99, "hold_days": 5,
            "gross_ret": 0.10, "raw_net": raw_net,
            "stress_net": raw_net - 0.0005, "avoid_ret": 0.02, "cost_bps": 10}


def _match(panel, trade=None):
    index = mod.build_panel_index(panel)
    return mod.match_control(index, trade or _trade(), {})


# ---------------------------------------------------------------------------
# 匹配命中 + 预注册回退链
# ---------------------------------------------------------------------------
def test_full_match_hit_uses_all_dimensions():
    result = _match(_panel(_control_group(12)))
    assert result["matched"] is True
    assert result["fallback"] == "full"
    assert result["control_n"] == 12
    assert abs(result["control_mean"] - 0.05) < 1e-12
    assert abs(result["neutral_net"] - (0.099 - 0.05)) < 1e-12
    assert result["reason"] == ""


def test_fallback_drop_industry_when_cell_lt_10():
    controls = {}
    controls.update(_control_group(3, start=1, sw="801080"))   # 同行业仅 3 只
    controls.update(_control_group(9, start=4, sw="801090"))   # 跨行业 9 只
    result = _match(_panel(controls))
    assert result["matched"] is True
    assert result["fallback"] == "no_industry"
    assert result["control_n"] == 12
    assert result["attempts"]["full"] == 3
    assert result["attempts"]["no_industry"] == 12


def test_fallback_drop_turnover_when_industry_cell_lt_10():
    controls = {}
    controls.update(_control_group(4, start=1, turnover=0.02))  # 同换手档 4
    controls.update(_control_group(8, start=5, turnover=0.5))   # 另一换手档 8
    result = _match(_panel(controls))
    assert result["matched"] is True
    assert result["fallback"] == "no_turnover"
    assert result["attempts"]["full"] < 10
    assert result["attempts"]["no_industry"] < 10
    assert result["attempts"]["no_turnover"] == 12


def test_unmatched_when_cell_lt_10_and_no_control_pool():
    result = _match(_panel(_control_group(5)))
    assert result["matched"] is False
    assert result["fallback"] == "unmatched"
    assert result["reason"] == "control_cell_lt_10"
    assert result["neutral_net"] is None
    assert result["attempts"]["no_turnover"] == 5

    empty = _match(_panel({}))
    assert empty["matched"] is False
    assert empty["reason"] == "no_control_pool"


def test_event_industry_missing_skips_industry_dimension():
    panel = _panel(_control_group(12, sw="801090"),
                   event_bar=_event_bar(sw_l1=None))
    result = _match(panel)
    assert result["matched"] is True
    assert result["fallback"] == "no_industry"
    assert "full" not in result["attempts"]


def test_event_cross_section_missing_is_unmatched():
    panel = _panel(_control_group(12), event_bar=_event_bar(circ_mv=None))
    result = _match(panel)
    assert result["matched"] is False
    assert result["reason"] == "event_cross_section_missing"


# ---------------------------------------------------------------------------
# neutralize_trade / neutralize_trades
# ---------------------------------------------------------------------------
def test_neutralize_trade_attaches_fields_and_is_pure():
    panel = _panel(_control_group(12))
    trade = _trade()
    before = dict(trade)
    index = mod.build_panel_index(panel)
    row = mod.neutralize_trade(index, trade, {})
    assert trade == before, "纯函数不得改写入参 trade"
    assert row["neutral_control_n"] == 12
    assert row["neutral_fallback"] == "full"
    assert row["neutral_reason"] == ""
    assert abs(row["neutral_net"] - (0.099 - 0.05)) < 1e-12


def test_neutralize_trades_summary_and_warn_over_20pct():
    panel = _panel(_control_group(12))
    trades = [_trade()]
    for i in range(4):
        bad = _trade()
        bad["ts_code"] = "6999%02d" % i     # panel 里没有 → event_bar_missing
        trades.append(bad)
    rows, summary = mod.neutralize_trades(trades, panel, {})
    assert len(rows) == 5
    assert summary["n"] == 5 and summary["matched"] == 1
    assert summary["unmatched"] == 4
    assert summary["match_rate"] == 0.2
    assert summary["unmatched_rate"] == 0.8
    assert summary["warn_unmatched_over_20pct"] is True
    assert summary["fallback_counts"] == {"full": 1}
    assert summary["min_controls"] == 10


# ---------------------------------------------------------------------------
# R2 门：由中性化结果决定（通过/不通过+原因）
# ---------------------------------------------------------------------------
def _gate_metrics(**over):
    metrics = {
        "n_indep": 35, "median_net": 0.010, "mean_net": 0.012,
        "mean_net_stress": 0.0105, "p_holm": 0.01, "ci_low": 0.002,
        "neutral_n": 35, "neutral_net_median": 0.004,
        "neutral_net_mean": 0.003, "neutral_net_reason": "",
        "subperiod_means": [0.010, -0.002, 0.008],
    }
    metrics.update(over)
    return metrics


def test_r2_gate_passes_with_valid_neutralization():
    result = mod.evaluate_event_gates(_gate_metrics())
    assert result["gates"]["R2"] is True
    assert "R2" not in result["reasons"]
    assert result["score"] == 8 and result["verdict"] == "GO"


def test_r2_gate_fails_without_control_and_gives_reason():
    result = mod.evaluate_event_gates(_gate_metrics(
        neutral_n=0, neutral_net_median=None, neutral_net_mean=None,
        neutral_net_reason="control_cell_lt_10"))
    assert result["gates"]["R2"] is False
    assert result["reasons"]["R2"] == "control_cell_lt_10"
    assert result["score"] == 7
    assert result["go"] is True, "M1-M4 + 3 稳健门 + 7/8 分仍可 GO（R2 为稳健门）"


def test_r2_gate_fails_when_neutral_net_negative():
    result = mod.evaluate_event_gates(_gate_metrics(
        neutral_net_median=-0.010, neutral_net_mean=-0.005))
    assert result["gates"]["R2"] is False
    assert result["reasons"]["R2"]
    assert result["score"] == 7


# ---------------------------------------------------------------------------
# 中性化后显著 / 不显著（neutral_net 单侧 p）
# ---------------------------------------------------------------------------
def _with_neutral(value, event_type="E1"):
    trade = _trade(event_type=event_type)
    trade.update({"neutral_net": value, "neutral_fallback": "full",
                  "neutral_reason": "", "neutral_control_n": 12})
    return trade


def test_neutralized_significant_and_not_significant():
    significant = mod.summarize_trades(
        [_with_neutral(v) for v in
         [0.008, 0.009, 0.010, 0.011, 0.012, 0.0095, 0.0105, 0.0085,
          0.0115, 0.0098]])
    e1 = significant["E1"]
    assert e1["neutral_n"] == 10
    assert e1["neutral_net_mean"] is not None and e1["neutral_net_mean"] > 0
    assert e1["neutral_net_p"] is not None and e1["neutral_net_p"] < 0.05

    not_significant = mod.summarize_trades(
        [_with_neutral(v) for v in
         [0.02, -0.02, 0.01, -0.01, 0.015, -0.015, 0.005, -0.005,
          0.0, 0.001]])
    e2 = not_significant["E1"]
    assert e2["neutral_net_p"] is not None and e2["neutral_net_p"] > 0.05


# ---------------------------------------------------------------------------
# evaluate_all 集成：R2 不再恒 False
# ---------------------------------------------------------------------------
def test_evaluate_all_integrates_neutralization():
    panel = _panel(_control_group(12))
    trades = [_trade() for _ in range(5)]
    events = [{"date": T, "ts_code": EVENT_CODE, "event_type": "E1"}]
    out = mod.evaluate_all(trades, panel=panel, events=events)
    assert out["neutralization"]["n"] == 5
    assert out["neutralization"]["matched"] == 5
    assert out["neutralization"]["fallback_counts"] == {"full": 5}
    e1_stats = out["by_event"]["E1"]
    assert e1_stats["neutral_n"] == 5
    assert abs(e1_stats["neutral_net_mean"] - (0.099 - 0.05)) < 1e-9
    assert out["gates"]["E1"]["gates"]["R2"] is True
    # E2 无样本 → R2 False 且有原因
    assert out["gates"]["E2"]["gates"]["R2"] is False
    assert out["gates"]["E2"]["reasons"]["R2"]


def test_evaluate_all_r2_false_when_control_return_higher():
    panel = _panel(_control_group(12, ret=0.20))   # 控制组更强 → neutral<0
    out = mod.evaluate_all([_trade()], panel=panel,
                           events=[{"date": T, "ts_code": EVENT_CODE,
                                    "event_type": "E1"}])
    assert out["by_event"]["E1"]["neutral_net_mean"] < 0
    assert out["gates"]["E1"]["gates"]["R2"] is False
    assert out["gates"]["E1"]["reasons"]["R2"]


def test_evaluate_all_without_panel_keeps_r2_false_with_reason():
    out = mod.evaluate_all([_trade()])
    assert out["neutralization"] is None
    assert out["by_event"]["E1"]["neutral_n"] == 0
    assert out["gates"]["E1"]["gates"]["R2"] is False
    assert out["gates"]["E1"]["reasons"]["R2"] == "neutral_net_unavailable"
