# -*- coding: utf-8 -*-
"""H1b 口径修复测试（task-40）：E1 涨停容差（H1 对齐）+ A 股 302 白名单。

覆盖：默认 1e-3 容差、可配置严格/容差、边界（limit−1e-3）、near-miss、
复牌/除权跳变、首 bar pre_close、簇合并独立事件、t+1 一字板容差、参数校验、
302 前缀归 A、适配层 panel 输出 pre_close。
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO / "tools" / "lcs1_h1b_harness.py"
ADAPTER_PATH = REPO / "tools" / "h1b_data_adapter.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HARNESS = _load("lcs1_h1b_harness_t40", HARNESS_PATH)
ADAPTER = _load("h1b_data_adapter_t40", ADAPTER_PATH)
SPEC = HARNESS.SPEC
CAL = ["2024-01-%02d" % (i + 2) for i in range(30)]  # 均在预注册样本窗口内


def _bar(day, close, pre_close=None, open_=None, volume=1_000_000.0,
         amount=35_000_000.0, is_st=False):
    return {"date": day, "open": open_ if open_ is not None else close,
            "high": max(close, open_ if open_ is not None else close),
            "low": min(close, open_ if open_ is not None else close),
            "close": close, "pre_close": pre_close, "volume": volume,
            "amount": amount, "is_st": is_st}


def _panel(bars_by_code, calendar=None, list_date="2020-01-01"):
    cal = list(calendar or CAL)
    return {"calendar": cal,
            "codes": {code: {"list_date": list_date, "bars": bars}
                      for code, bars in bars_by_code.items()}}


def _e1(events):
    return [ev for ev in events if ev["event_type"] == "E1"]


def _spec(**overrides):
    spec = copy.deepcopy(SPEC)
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# E1 容差（H1 口径：close >= limit - 1e-3）
# ---------------------------------------------------------------------------
def test_default_tolerance_matches_h1_formula():
    assert SPEC["limit_up_tolerance"] == pytest.approx(1e-3)
    # day1：limit=11.00，close=10.9995（差 0.0005，near-miss）
    bars = [_bar(CAL[0], 10.0, pre_close=10.0),
            _bar(CAL[1], 10.9995, pre_close=10.0),
            _bar(CAL[2], 10.5, pre_close=10.9995)]
    events = _e1(HARNESS.detect_events(_panel({"600000.SH": bars}), None, SPEC))
    assert [ev["date"] for ev in events] == [CAL[1]]


def test_tolerance_configurable_strict_zero():
    bars = [_bar(CAL[0], 10.0, pre_close=10.0),
            _bar(CAL[1], 10.9995, pre_close=10.0)]
    panel = _panel({"600000.SH": bars})
    strict = _e1(HARNESS.detect_events(panel, None, _spec(limit_up_tolerance=0.0)))
    tolerant = _e1(HARNESS.detect_events(panel, None,
                                         _spec(limit_up_tolerance=1e-3)))
    assert strict == []
    assert [ev["date"] for ev in tolerant] == [CAL[1]]


def test_tolerance_boundary_inclusive_and_beyond():
    # 恰好 limit-tol（10.999 = 11.00-0.001）在容差内（>=），再低 0.0001 排除
    edge = [_bar(CAL[0], 10.0, pre_close=10.0),
            _bar(CAL[1], 10.999, pre_close=10.0)]
    beyond = [_bar(CAL[0], 10.0, pre_close=10.0),
              _bar(CAL[1], 10.9989, pre_close=10.0)]
    assert len(_e1(HARNESS.detect_events(_panel({"600000.SH": edge}), None,
                                         SPEC))) == 1
    assert _e1(HARNESS.detect_events(_panel({"600000.SH": beyond}), None,
                                     SPEC)) == []


def test_tolerance_counts_resumption_exrights_jump_like_h1():
    # 复牌/除权类跳变：pre_close 7.33（创业板 20%→limit 8.80），close 18.95 远超限价；
    # H1 的 `close >= limit - 1e-3` 会把它计入（历史口径）。注意：容差只放宽下界，
    # 不排除「超过限价」的复权/复牌异常行（>= 语义，H1 同此）——正式跑数时需在
    # 口径说明中披露；若要排除异常需另加规则（当前不擅自改 H1 口径）。
    bars = [_bar(CAL[0], 7.33, pre_close=7.0),
            _bar(CAL[1], 18.95, pre_close=7.33)]
    panel = _panel({"300001.SZ": bars})
    tolerant = _e1(HARNESS.detect_events(panel, None, SPEC))
    strict = _e1(HARNESS.detect_events(panel, None,
                                       _spec(limit_up_tolerance=0.0)))
    assert [ev["date"] for ev in tolerant] == [CAL[1]]
    assert [ev["date"] for ev in strict] == [CAL[1]]  # >= 语义下容差 0 仍计入


def test_first_bar_uses_pre_close_like_h1():
    # 窗口内首 bar 无上一 bar：用 bar 自带 pre_close 仍可判 E1（H1 矩阵同口径）
    bars = [_bar(CAL[0], 11.0, pre_close=10.0),
            _bar(CAL[1], 10.0, pre_close=11.0)]
    events = _e1(HARNESS.detect_events(_panel({"600000.SH": bars}), None, SPEC))
    assert [(ev["date"], ev["ts_code"]) for ev in events] == [(CAL[0], "600000.SH")]


def test_cluster_merges_near_miss_events():
    bars = [_bar(CAL[0], 10.0, pre_close=10.0),
            _bar(CAL[1], 10.9995, pre_close=10.0),
            _bar(CAL[2], 10.5, pre_close=10.9995),
            _bar(CAL[3], 10.9995, pre_close=10.0),
            _bar(CAL[4], 10.5, pre_close=10.9995)]
    events = _e1(HARNESS.detect_events(_panel({"600000.SH": bars}), None, SPEC))
    assert [ev["date"] for ev in events] == [CAL[1]]  # 同股 <20 交易日只留首事件


# ---------------------------------------------------------------------------
# t+1 一字板硬闸同样使用容差（H1 ONEWORD 口径）
# ---------------------------------------------------------------------------
def _gate_panel():
    cal = CAL[:25]
    bars = []
    for k, day in enumerate(cal):
        if k == 20:
            bars.append(_bar(day, 10.0, pre_close=10.0))
        elif k == 21:
            bars.append(_bar(day, 10.5, pre_close=10.0, open_=10.9995))
        else:
            bars.append(_bar(day, 10.0, pre_close=10.0))
    return _panel({"600000.SH": bars}, cal)


def test_gate_t1_one_word_uses_tolerance():
    panel = _gate_panel()
    event = {"date": CAL[20], "ts_code": "600000.SH", "event_type": "E1"}
    spec = _spec(min_listed_days=5)
    ok, reasons = HARNESS.gate_t1(panel, event, spec)
    assert ok is False
    assert "t1_open_at_limit_up（一字板不可交易）" in reasons
    strict = _spec(min_listed_days=5, limit_up_tolerance=0.0)
    ok_strict, reasons_strict = HARNESS.gate_t1(panel, event, strict)
    assert ok_strict is True, reasons_strict


# ---------------------------------------------------------------------------
# 参数校验 / 面板 pre_close 透传
# ---------------------------------------------------------------------------
def test_validate_spec_tolerance_range():
    default_issues = HARNESS.validate_spec(SPEC)
    assert not [x for x in default_issues if "limit_up_tolerance" in x]
    for bad in (-0.01, 0.02, 1.0):
        issues = HARNESS.validate_spec(_spec(limit_up_tolerance=bad))
        assert [x for x in issues if "limit_up_tolerance" in x], bad


# ---------------------------------------------------------------------------
# 涨停价舍入口径：h1_float32（默认，复现 H1）vs half_up（标准分位）
# ---------------------------------------------------------------------------
def test_limit_up_rounding_modes():
    assert SPEC["limit_up_rounding"] == "h1_float32"
    # H1 float32：9.45×1.3=12.285 → float32 12.2799997 → 12.28；标准 half-up=12.29
    assert HARNESS.limit_up_price(9.45, "920748.BJ",
                                  rounding="h1_float32") == pytest.approx(12.28,
                                                                         abs=1e-6)
    assert HARNESS.limit_up_price(9.45, "920748.BJ",
                                  rounding="half_up") == pytest.approx(12.29)
    # 普通整分价格两种口径一致
    assert HARNESS.limit_up_price(10.0, "600000.SH") == pytest.approx(11.0)
    with pytest.raises(ValueError):
        HARNESS.limit_up_price(10.0, "600000.SH", rounding="bad")


def test_default_rounding_reproduces_h1_half_cent_case():
    bars = [_bar(CAL[0], 9.45, pre_close=9.45),
            _bar(CAL[1], 12.28, pre_close=9.45)]
    panel = _panel({"920748.BJ": bars})
    h1_events = _e1(HARNESS.detect_events(panel, None, SPEC))
    std_spec = _spec(limit_up_rounding="half_up")
    std_events = _e1(HARNESS.detect_events(panel, None, std_spec))
    assert [ev["date"] for ev in h1_events] == [CAL[1]]  # H1 口径计入
    assert std_events == []  # 标准口径不计（12.28 < 12.289）


def test_validate_spec_rounding_mode():
    issues = HARNESS.validate_spec(_spec(limit_up_rounding="float32"))
    assert [x for x in issues if "limit_up_rounding" in x]
    assert not [x for x in HARNESS.validate_spec(SPEC)
                if "limit_up_rounding" in x]


def test_load_panel_keeps_pre_close(tmp_path):
    panel = {"calendar": CAL[:2],
             "codes": {"600000.SH": {"list_date": "2020-01-01", "bars": [
                 _bar(CAL[0], 10.0, pre_close=9.9)]}}}
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(panel, ensure_ascii=False), encoding="utf-8")
    loaded = HARNESS.load_panel(str(path))
    assert loaded["codes"]["600000.SH"]["bars"][0]["pre_close"] == 9.9


def test_adapter_panel_outputs_pre_close():
    daily = [{"ts_code": "600000.SH", "trade_date": "20240102",
              "open": 10.0, "high": 11.0, "low": 10.0, "close": 11.0,
              "pre_close": 10.0, "volume": 100_000.0, "amount": 35_000_000.0}]
    panel = ADAPTER.build_panel(daily, {}, [], {"list_date": {
        "600000.SH": "20150101"}}, {}, [], ["20240102"])
    bar = panel["codes"]["600000.SH"]["bars"][0]
    assert bar["pre_close"] == 10.0
    assert bar["date"] == "2024-01-02"
    assert bar["amount"] == 35_000_000.0
    assert bar["volume"] == 100_000.0


# ---------------------------------------------------------------------------
# A 股白名单补 302（302132.SZ；top_list 302.SZ 行归 A）
# ---------------------------------------------------------------------------
def test_whitelist_302_prefix():
    assert ADAPTER.is_a_share_code("302132.SZ") is True
    assert ADAPTER.is_a_share_code("302001.SZ") is True
    assert ADAPTER.is_a_share_code("300001.SZ") is True
    assert ADAPTER.is_a_share_code("303001.SZ") is False  # 仅补 302，其余不擅自扩
    assert ADAPTER.is_a_share_code("110044.SH") is False
    assert ADAPTER.is_a_share_code("900901.SH") is False


def test_top_list_302_rows_count_as_a_share():
    rows = [{"ts_code": "302132.SZ", "trade_date": "20240102",
             "net_amount": 3_000_000.0},
            {"ts_code": "110044.SH", "trade_date": "20240102",
             "net_amount": 1_000_000.0}]
    kept, stats = ADAPTER.filter_top_list(rows, {"302132.SZ"})
    assert stats["non_a_share"] == 1          # 仅可转债
    assert stats["not_in_universe"] == 0
    assert stats["a_share_rows"] == 1
    assert stats["e2_positive"] == 1
    assert [row["ts_code"] for row in kept] == ["302132.SZ"]
