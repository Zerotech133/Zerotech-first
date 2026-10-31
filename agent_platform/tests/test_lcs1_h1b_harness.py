# -*- coding: utf-8 -*-
"""LCS-1 H1b 回测骨架 测试（WO-LCS1-H1B-HARNESS-20261031，离线合成数据）。

覆盖：E1/E2 事件检测（样本窗口 + 同股簇合并）/ t+1 可交易硬闸全部分支 /
回避 W 后 t+6 入场与顺延 / 持有 H 退出与顺延（跌停不可卖）/ 10bps 往返成本
（含 15bps 压力）/ 8 门评分卡阈值（GO/补证/No-Go + Holm）/ 数据未冻结拒绝
（缺字段精确到路径）/ --dry-run 参数校验 / 数据接口。

纪律：全部合成数据 + tmp_path；不联网、不读真实行情、不写生产。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
_TOOL = WT / "tools" / "lcs1_h1b_harness.py"
_spec = importlib.util.spec_from_file_location("lcs1_h1b_harness", str(_TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CODE = "600000"


# ---------------------------------------------------------------------------
# 合成数据构造
# ---------------------------------------------------------------------------
def _cal(n=420, start=date(2023, 1, 1)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _base_bars(cal, event_idx):
    """事件日 t 收盘=涨停(10→11)；t+3 第二个涨停（同股簇）；其余平盘。"""
    bars = []
    for i, day in enumerate(cal):
        if i < event_idx:
            close, open_px = 10.00, 10.00
        elif i == event_idx:
            close, open_px = 11.00, 10.00   # E1：prev_close 10 → 涨停 11
        elif i == event_idx + 3:
            close, open_px = 12.10, 11.00   # 第二个涨停（prev 11 → 12.10）
        else:
            close, open_px = 12.10, 11.00
        bars.append({"date": day, "open": open_px,
                     "high": max(open_px, close), "low": min(open_px, close),
                     "close": close, "volume": 1_000_000.0,
                     "amount": 50_000_000.0, "is_st": False})
    return bars


def _panel(**kw):
    """构造合成 panel；可注入硬闸/入场/离场变体。"""
    event_idx = kw.get("event_idx", 320)
    n = kw.get("n", 420)
    cal = _cal(n, kw.get("start", date(2023, 1, 1)))
    bars = _base_bars(cal, event_idx)
    amount = kw.get("amount", 50_000_000.0)
    for bar in bars:
        bar["amount"] = amount
    if kw.get("st_on_event"):
        bars[event_idx]["is_st"] = True
    if kw.get("t1_volume") is not None:
        bars[event_idx + 1]["volume"] = kw["t1_volume"]
    if kw.get("t1_open") is not None:
        bars[event_idx + 1]["open"] = kw["t1_open"]
    if kw.get("entry_open") is not None:
        bars[event_idx + 6]["open"] = kw["entry_open"]
    if kw.get("exit_close") is not None:
        bars[event_idx + 10]["close"] = kw["exit_close"]
    if kw.get("drop_t1"):
        bars.pop(event_idx + 1)
    if kw.get("truncate_after") is not None:
        bars = bars[:kw["truncate_after"]]
    list_date = cal[kw.get("list_date_idx", 0)]
    return {"calendar": cal,
            "codes": {CODE: {"list_date": list_date, "bars": bars}}}


def _event(panel, event_type="E1", date_override=None):
    return {"date": date_override or panel["calendar"][320],
            "ts_code": CODE, "event_type": event_type, "net_amount": None}


def _manifest(**over):
    manifest = {
        "data_version": "lcs1-h1b-test-v1",
        "frozen_at": "2026-11-01T00:00:00",
        "sample_in": mod.SPEC["sample_in"],
        "sample_out": mod.SPEC["sample_out"],
        "cost_model": {"round_trip_bps": 10, "stress_bps": 15,
                       "includes_borrow": False},
        "datasets": {name: {"sha256": "a" * 64}
                     for name in mod.FREEZE_DATASETS},
        "checks": {name: True for name in mod.FREEZE_CHECKS},
    }
    manifest.update(over)
    return manifest


# ---------------------------------------------------------------------------
# 预注册参数 / --dry-run
# ---------------------------------------------------------------------------
def test_preregistered_spec_locked():
    assert mod.SPEC["W"] == 5 and mod.SPEC["H"] == 5
    assert mod.SPEC["entry_offset"] == 6
    assert mod.SPEC["cost_bps"] == 10 and mod.SPEC["stress_cost_bps"] == 15
    assert mod.SPEC["sample_in"] == "2023-09-04"
    assert mod.SPEC["sample_out"] == "2026-10-16"
    assert mod.SPEC["oos_start"] == "2025-07-01"
    assert mod.SPEC["gates"] == ("M1", "M2", "M3", "M4",
                                 "R1", "R2", "R3", "R4")
    assert mod.validate_spec() == []


def test_validate_spec_detects_bad_params():
    bad = dict(mod.SPEC, entry_offset=7)
    assert any("entry_offset" in x for x in mod.validate_spec(bad))
    bad2 = dict(mod.SPEC, W=0)
    assert mod.validate_spec(bad2)


def test_dry_run_outputs_params_and_freeze_missing(tmp_path, capsys):
    rc = mod.main(["--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "dry_run_ok"
    assert payload["spec"]["W"] == 5 and payload["spec"]["H"] == 5
    assert payload["spec"]["entry_offset"] == 6
    assert payload["spec"]["cost_bps"] == 10
    assert payload["param_issues"] == []
    assert payload["result_claim"] is False
    assert payload["freeze"]["frozen"] is False
    assert payload["freeze"]["missing_fields"], "未给 manifest 应列出缺字段"

    manifest = tmp_path / "freeze.json"
    manifest.write_text(json.dumps(_manifest(), ensure_ascii=False),
                        encoding="utf-8")
    rc2 = mod.main(["--dry-run", "--manifest", str(manifest), "--json"])
    payload2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and payload2["freeze"]["frozen"] is True


# ---------------------------------------------------------------------------
# E1/E2 事件检测（窗口 + 同股簇）
# ---------------------------------------------------------------------------
def test_detect_events_e1_e2_window_and_cluster():
    panel = _panel()
    top = [{"ts_code": CODE, "trade_date": panel["calendar"][340],
            "net_amount": 1_000_000.0},
           {"ts_code": CODE, "trade_date": panel["calendar"][341],
            "net_amount": -500_000.0}]
    events = mod.detect_events(panel, top)
    e1 = [e for e in events if e["event_type"] == "E1"]
    e2 = [e for e in events if e["event_type"] == "E2"]
    assert [e["date"] for e in e1] == [panel["calendar"][320]], \
        "t+3 的同股涨停应并入簇、只保留首事件"
    assert [e["date"] for e in e2] == [panel["calendar"][340]], \
        "E2 只收 net_amount 聚合>0 且窗口内"
    assert all(e["ts_code"] == CODE for e in events)


def test_detect_events_filters_out_of_window():
    # 日历整体早于 sample_in：事件应全部被窗口过滤
    panel = _panel(event_idx=20, n=40, start=date(2023, 8, 1))
    assert panel["calendar"][20] < mod.SPEC["sample_in"]
    assert mod.detect_events(panel, []) == []
    # 窗口内事件保留
    panel2 = _panel(event_idx=320, n=420)
    assert len(mod.detect_events(panel2, [])) == 1


# ---------------------------------------------------------------------------
# t+1 可交易硬闸（全部硬条件）
# ---------------------------------------------------------------------------
def test_t1_gate_passes_on_clean_case():
    panel = _panel()
    ok, reasons = mod.gate_t1(panel, _event(panel))
    assert ok is True and reasons == []


def test_t1_gate_rejects_unexecutable_cases():
    cases = []
    panel = _panel(t1_open=12.10)       # t+1 开盘=涨停价（一字板）
    cases.append((panel, "t1_open_at_limit_up"))
    panel = _panel(t1_volume=0.0)       # t+1 无成交
    cases.append((panel, "t1_volume_zero"))
    panel = _panel(drop_t1=True)        # t+1 停牌跳跃
    cases.append((panel, "t1_suspended_or_missing"))
    panel = _panel(st_on_event=True)    # 事件日 ST
    cases.append((panel, "st"))
    panel = _panel(amount=1_000_000.0)  # 20 日均成交额不足 3000 万
    cases.append((panel, "liquidity_below_30m"))
    panel = _panel(list_date_idx=300)   # 上市不足 250 交易日
    cases.append((panel, "listed_days_lt_250"))
    for panel, expected in cases:
        ok, reasons = mod.gate_t1(panel, _event(panel))
        assert ok is False, expected
        assert any(expected in reason for reason in reasons), (expected,
                                                               reasons)


# ---------------------------------------------------------------------------
# 回避 W 后 t+6 入场（顺延 ≤4 候选）
# ---------------------------------------------------------------------------
def test_entry_index_is_t_plus_6():
    panel = _panel()
    idx, reasons = mod.find_entry(panel, _event(panel))
    assert idx == 320 + 6 and reasons == []


def test_entry_falls_back_when_first_candidates_unexecutable():
    panel = _panel()
    bars = panel["codes"][CODE]["bars"]
    # t+6 开盘=涨停价（prev close 12.10 → 13.31）不可入场
    bars[326]["open"] = 13.31
    bars[327]["volume"] = 0.0
    idx, reasons = mod.find_entry(panel, _event(panel))
    assert idx == 320 + 8, reasons
    assert any("t+6_open_at_limit_up" in r for r in reasons)
    assert any("t+7_volume_zero" in r for r in reasons)


def test_entry_candidates_exhausted_returns_none():
    panel = _panel()
    bars = panel["codes"][CODE]["bars"]
    for offset in range(6, 10):
        bars[320 + offset]["volume"] = 0.0
    idx, reasons = mod.find_entry(panel, _event(panel))
    assert idx is None
    assert "entry_candidates_exhausted" in reasons


# ---------------------------------------------------------------------------
# 持有 H 退出（无 bar/跌停顺延 ≤3 日）
# ---------------------------------------------------------------------------
def test_exit_is_four_trading_days_after_entry():
    panel = _panel()
    exit_idx, reasons = mod.find_exit(panel, _event(panel), 320 + 6)
    assert exit_idx == 320 + 10
    assert reasons == []


def test_exit_falls_back_on_limit_down():
    panel = _panel()
    bars = panel["codes"][CODE]["bars"]
    exit_idx = 320 + 10
    prev_close = bars[exit_idx - 1]["close"]
    bars[exit_idx]["close"] = mod.limit_down_price(prev_close, CODE)
    new_idx, reasons = mod.find_exit(panel, _event(panel), 320 + 6)
    assert new_idx == exit_idx + 1, reasons
    assert any("limit_down" in r for r in reasons)


def test_exit_without_bars_returns_none():
    panel = _panel(truncate_after=320 + 6 + 3)
    exit_idx, reasons = mod.find_exit(panel, _event(panel), 320 + 6)
    assert exit_idx is None
    assert "exit_candidates_exhausted" in reasons


# ---------------------------------------------------------------------------
# 单笔 trade 与成本（10bps 主口径 / 15bps 压力）
# ---------------------------------------------------------------------------
def test_simulate_trade_10bps_cost_and_avoid_kpi():
    panel = _panel()
    trade = mod.simulate_trade(panel, _event(panel))
    assert trade["status"] == "traded"
    assert trade["entry_date"] == panel["calendar"][326]
    assert trade["exit_date"] == panel["calendar"][330]
    gross = trade["exit_close"] / trade["entry_open"] - 1.0
    assert abs(trade["gross_ret"] - gross) < 1e-12
    assert abs(trade["raw_net"] - (gross - 0.0010)) < 1e-12
    assert abs(trade["stress_net"] - (gross - 0.0015)) < 1e-12
    # H1b-B 回避 KPI：close_{t+W}/open_{t+1}-1
    expected_avoid = (panel["codes"][CODE]["bars"][325]["close"]
                      / panel["codes"][CODE]["bars"][321]["open"] - 1.0)
    assert abs(trade["avoid_ret"] - expected_avoid) < 1e-12


def test_simulate_trade_excluded_when_t1_gate_fails():
    panel = _panel(t1_volume=0.0)
    trade = mod.simulate_trade(panel, _event(panel))
    assert trade["status"] == "excluded"
    assert trade["stage"] == "t1_gate"
    assert trade["reasons"]


def test_run_backtest_funnel_and_trade():
    panel = _panel()
    result = mod.run_backtest([_event(panel)], panel)
    assert result["funnel"] == {"events": 1, "traded": 1, "excluded": 0,
                                "excluded_by_stage": {}}
    assert len(result["trades"]) == 1


# ---------------------------------------------------------------------------
# 8 门评分卡阈值（预注册 §7.1/§7.2）
# ---------------------------------------------------------------------------
GOOD_METRICS = {
    "n_indep": 35, "median_net": 0.010, "mean_net": 0.012,
    "mean_net_stress": 0.0105, "p_holm": 0.01, "ci_low": 0.002,
    "neutral_net_median": 0.004, "neutral_net_mean": 0.003,
    "subperiod_means": [0.010, -0.002, 0.008],
}


def _metrics(**over):
    metrics = dict(GOOD_METRICS)
    metrics.update(over)
    return metrics


def test_gates_go_when_all_thresholds_met():
    result = mod.evaluate_event_gates(_metrics())
    assert set(result["gates"]) == set(mod.SPEC["gates"])
    assert all(result["gates"].values())
    assert result["score"] == 8 and result["robust_pass"] == 4
    assert result["verdict"] == "GO" and result["go"] is True


def test_gates_no_go_on_direction_or_significance():
    for over in ({"median_net": -0.001}, {"mean_net": 0.0},
                 {"p_holm": 0.20}, {"p_holm": None}):
        result = mod.evaluate_event_gates(_metrics(**over))
        assert result["verdict"] == "No-Go", over
        assert result["go"] is False


def test_gates_supplement_on_sample_or_robustness_shortfall():
    short_sample = mod.evaluate_event_gates(_metrics(n_indep=29))
    assert short_sample["gates"]["M1"] is False
    assert short_sample["verdict"] == "补证"
    weak_robust = mod.evaluate_event_gates(
        _metrics(ci_low=None, neutral_net_median=None,
                 neutral_net_mean=None))
    assert weak_robust["robust_pass"] <= 2
    assert weak_robust["verdict"] == "补证"


def test_holm_adjust_main_family_two_tests():
    adjusted = mod.holm_adjust([0.04, 0.01])
    assert adjusted[1] == 0.02          # 0.01*2
    assert adjusted[0] == 0.04          # max(0.04, 0.04*1)
    assert mod.holm_adjust([None]) == [1.0]


# ---------------------------------------------------------------------------
# 数据未冻结 → 拒绝真实回测并给缺字段
# ---------------------------------------------------------------------------
def _write_inputs(tmp_path):
    panel = _panel()
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(json.dumps(_event(panel), ensure_ascii=False) + "\n",
                           encoding="utf-8")
    panel_path = tmp_path / "panel.json"
    panel_path.write_text(json.dumps(panel, ensure_ascii=False),
                          encoding="utf-8")
    return events_path, panel_path, panel


def test_check_freeze_reports_missing_fields():
    result = mod.check_freeze(None)
    assert result["frozen"] is False
    assert result["missing_fields"]
    manifest = _manifest()
    manifest.pop("sample_in")
    manifest["datasets"].pop("ts_namechange")
    manifest["checks"]["unadjusted_prices"] = False
    result2 = mod.check_freeze(manifest)
    assert result2["frozen"] is False
    assert "sample_in" in result2["missing_fields"]
    assert "datasets.ts_namechange" in result2["missing_fields"]
    assert "checks.unadjusted_prices" in result2["missing_fields"]
    bad_sample = mod.check_freeze(_manifest(sample_in="2020-01-01"))
    assert bad_sample["frozen"] is False
    assert any("sample_in 不一致" in p for p in bad_sample["problems"])
    assert mod.check_freeze(_manifest())["frozen"] is True


def test_real_run_refused_without_frozen_data(tmp_path, capsys):
    events_path, panel_path, _ = _write_inputs(tmp_path)
    # 完全不给 manifest
    rc = mod.main(["--events", str(events_path), "--panel", str(panel_path),
                   "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "refused"
    assert payload["reason"] == "data_not_frozen"
    assert payload["missing_fields"]
    assert payload["result_claim"] is False
    # manifest 路径不存在
    rc = mod.main(["--events", str(events_path), "--panel", str(panel_path),
                   "--manifest", str(tmp_path / "nope.json"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["status"] == "refused"
    # 坏 manifest（缺字段）→ 精确列缺
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"data_version": "x", "datasets": {},
                               "checks": {}}), encoding="utf-8")
    rc = mod.main(["--events", str(events_path), "--panel", str(panel_path),
                   "--manifest", str(bad), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "sample_in" in payload["missing_fields"]
    assert "datasets.ts_daily" in payload["missing_fields"]
    assert "checks.unadjusted_prices" in payload["missing_fields"]


def test_frozen_inputs_run_skeleton_without_claim(tmp_path, capsys):
    events_path, panel_path, _ = _write_inputs(tmp_path)
    manifest_path = tmp_path / "freeze.json"
    manifest_path.write_text(json.dumps(_manifest(), ensure_ascii=False),
                             encoding="utf-8")
    rc = mod.main(["--events", str(events_path), "--panel", str(panel_path),
                   "--manifest", str(manifest_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "backtest_skeleton_ran"
    assert payload["funnel"]["events"] == 1 and payload["funnel"]["traded"] == 1
    assert payload["result_claim"] is False
    assert "不宣称结果" in payload["note"]
    assert set(payload["evaluation"]["gates"]) == {"E1", "E2"}


# ---------------------------------------------------------------------------
# 数据接口（load_events / load_panel）
# ---------------------------------------------------------------------------
def test_load_events_jsonl_and_validation(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"date": "2024-01-02", "ts_code": "600000",
                    "event_type": "E1"}, ensure_ascii=False) + "\n" +
        json.dumps({"trade_date": "2024-01-03", "symbol": "000001",
                    "event_type": "e2", "net_amount": 123.0},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    events = mod.load_events(path)
    assert [e["event_type"] for e in events] == ["E1", "E2"]
    assert events[1]["net_amount"] == 123.0 and events[1]["date"] == "2024-01-03"
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"date": "2024-01-02"}\n', encoding="utf-8")
    try:
        mod.load_events(bad)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("缺字段事件应报 ValueError")


def test_load_panel_and_roundtrip(tmp_path):
    panel = _panel()
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(panel, ensure_ascii=False), encoding="utf-8")
    loaded = mod.load_panel(path)
    assert loaded["calendar"] == panel["calendar"]
    assert list(loaded["codes"]) == [CODE]
    assert loaded["codes"][CODE]["list_date"] == panel["calendar"][0]
    assert len(loaded["codes"][CODE]["bars"]) == len(panel["codes"][CODE]["bars"])


# ---------------------------------------------------------------------------
# 工具边界：不写盘 / 不自动交易（结构断言）
# ---------------------------------------------------------------------------
def test_harness_has_no_write_or_trade_surface(tmp_path, monkeypatch):
    """骨架不应写任何文件（除读入），也不提供交易函数。"""
    import builtins

    opened = []
    real_open = builtins.open

    def _spy(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            opened.append((str(path), mode))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _spy)
    rc = mod.main(["--dry-run", "--json"])
    assert rc == 0
    assert opened == [], "dry-run 不得写盘：%s" % opened
    for name in ("place_order", "execute_trade", "auto_trade"):
        assert not hasattr(mod, name), "骨架不得提供交易函数：%s" % name
