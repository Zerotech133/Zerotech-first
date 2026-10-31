# -*- coding: utf-8 -*-
"""H1b §5.2 暴露回归辅方法 测试（WO-LCS1-H1B-EXPOSURE-20261031，离线合成数据）。

覆盖：已知系数恢复 / board·SW·事件哑变量 / 字段缺失 unavailable / 样本不足
unavailable / 按日 Fama-MacBeth 均值+SE+t / 结果不参与 GO 判定。
纪律：纯合成 records，不跑真实回测、不联网、不写生产。
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
_TOOL = WT / "tools" / "lcs1_h1b_harness.py"
_spec = importlib.util.spec_from_file_location("lcs1_h1b_harness", str(_TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DAYS = ["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07"]
# 真值：y = 0.010 + 0.020·log(mv) + 0.5·turnover + 0.030·chinext
#            + 0.020·(sw=801020) + 0.015·(E2) + noise
BETA = {"intercept": 0.010, "log_circ_mv": 0.020, "turnover": 0.5,
        "chinext_vs_main": 0.030, "sw_801020": 0.020, "event_e2": 0.015}


def _rows(noise=False):
    rows = []
    for day_idx, day in enumerate(DAYS):
        for i in range(12):
            board = "main" if i % 3 else "chinext"
            sw = "801010" if i % 4 else "801020"
            event_type = "E1" if i < 8 else "E2"
            mv = 1000.0 + 50.0 * i
            turnover = 0.01 + 0.002 * i
            noise_value = (((i + day_idx) % 5) - 2) * 0.0005 if noise else 0.0
            y_value = (BETA["intercept"] + BETA["log_circ_mv"] * math.log(mv)
                       + BETA["turnover"] * turnover
                       + BETA["chinext_vs_main"] * (1 if board == "chinext" else 0)
                       + BETA["sw_801020"] * (1 if sw == "801020" else 0)
                       + BETA["event_e2"] * (1 if event_type == "E2" else 0)
                       + noise_value)
            prefix = "300" if board == "chinext" else "600"
            rows.append({
                "ts_code": "%s%03d" % (prefix, i),
                "event_type": event_type, "event_date": day,
                "entry_date": day, "exit_date": day,
                "raw_net": y_value, "stress_net": y_value - 0.0005,
                "circ_mv": mv, "turnover": turnover, "sw_l1": sw,
            })
    return rows


def _trades(rows):
    return [{k: row[k] for k in ("ts_code", "event_type", "event_date",
                                 "entry_date", "exit_date", "raw_net",
                                 "stress_net")} for row in rows]


def _panel(rows, drop_fields=()):
    codes = {}
    for row in rows:
        bar = {"date": row["event_date"]}
        if "circ_mv" not in drop_fields:
            bar["circ_mv"] = row["circ_mv"]
        if "turnover_rate" not in drop_fields:
            bar["turnover_rate"] = row["turnover"]
        if "sw_l1" not in drop_fields:
            bar["sw_l1"] = row["sw_l1"]
        codes.setdefault(row["ts_code"],
                         {"list_date": "2020-01-02", "bars": []})["bars"].append(bar)
    return {"calendar": DAYS, "codes": codes}


# ---------------------------------------------------------------------------
# 已知系数恢复 + 哑变量口径
# ---------------------------------------------------------------------------
def test_recovers_known_coefficients():
    rows = _rows()
    result = mod.cross_section_regression(_trades(rows), _panel(rows))
    assert result["status"] == "ok"
    assert result["n"] == len(rows) and result["days_estimable"] == len(DAYS)
    assert result["used_in_go"] is False
    assert "不参与 GO" in result["note"]
    coef = result["coefficients"]
    # 基线=chinext/SW801010/E1：截距=α+chinext效应=0.010+0.030
    assert abs(coef["intercept"]["mean"]
               - (BETA["intercept"] + BETA["chinext_vs_main"])) < 1e-9
    assert abs(coef["log_circ_mv"]["mean"] - BETA["log_circ_mv"]) < 1e-9
    assert abs(coef["turnover"]["mean"] - BETA["turnover"]) < 1e-9
    # 哑变量基线按字母序：chinext 为基线 → 系数=main−chinext=−0.030
    assert abs(coef["board:main"]["mean"] + BETA["chinext_vs_main"]) < 1e-9
    assert abs(coef["sw:801020"]["mean"] - BETA["sw_801020"]) < 1e-9
    assert abs(coef["event:E2"]["mean"] - BETA["event_e2"]) < 1e-9


def test_dummy_baselines_and_keys():
    rows = _rows()
    result = mod.cross_section_regression(_trades(rows), _panel(rows))
    coef = result["coefficients"]
    assert "board:main" in coef and "board:chinext" not in coef
    assert "sw:801020" in coef and "sw:801010" not in coef
    assert "event:E2" in coef and "event:E1" not in coef
    assert set(coef) == {"intercept", "log_circ_mv", "turnover",
                         "board:main", "sw:801020", "event:E2"}


# ---------------------------------------------------------------------------
# 按日 Fama-MacBeth：均值 / SE / t
# ---------------------------------------------------------------------------
def test_fama_macbeth_mean_se_t():
    rows = _rows(noise=True)
    result = mod.cross_section_regression(_trades(rows), _panel(rows))
    assert result["status"] == "ok"
    assert result["days_estimable"] == len(DAYS)
    coefficient = result["coefficients"]["log_circ_mv"]
    assert coefficient["n_days"] == len(DAYS)
    assert coefficient["se"] >= 0.0
    if coefficient["se"] > 0:
        assert coefficient["t"] is not None
        assert abs(coefficient["t"]
                   - coefficient["mean"] / coefficient["se"]) < 1e-9
    # 至少一个系数跨日有差异 → SE>0/t 可算（FM 生效）
    assert any(c["se"] > 0 and c["t"] is not None
               for c in result["coefficients"].values())


# ---------------------------------------------------------------------------
# 字段缺失 / 样本不足 → unavailable + 原因
# ---------------------------------------------------------------------------
def test_missing_fields_unavailable():
    rows = _rows()
    panel = _panel(rows, drop_fields=("circ_mv", "turnover_rate", "sw_l1"))
    result = mod.cross_section_regression(_trades(rows), panel)
    assert result["status"] == "unavailable"
    assert result["reason"] == "fields_missing"
    assert {"circ_mv", "turnover_rate", "sw_l1"} <= set(result["missing_fields"])
    assert result["used_in_go"] is False
    assert result["coefficients"] == {}


def test_partial_missing_rows_are_dropped():
    rows = _rows()
    panel = _panel(rows)
    panel["codes"]["600001"]["bars"][0].pop("circ_mv", None)
    result = mod.cross_section_regression(_trades(rows), panel)
    assert result["status"] == "ok"
    assert result["dropped"].get("circ_mv_missing") == 1
    assert "circ_mv" in result["missing_fields"]


def test_insufficient_days_unavailable():
    rows = [row for row in _rows() if row["event_date"] == DAYS[0]]
    result = mod.cross_section_regression(_trades(rows), _panel(rows))
    assert result["status"] == "unavailable"
    assert result["reason"] == "insufficient_cross_section_sample"
    assert result["days_estimable"] == 1
    assert result["min_days_required"] == mod.EXPOSURE_MIN_DAYS


def test_insufficient_obs_per_day_unavailable():
    rows = []
    for day in DAYS:
        for i in range(2):
            rows.append({"ts_code": "600%03d" % i, "event_type": "E1",
                         "event_date": day, "entry_date": day,
                         "exit_date": day, "raw_net": 0.01,
                         "stress_net": 0.0095, "circ_mv": 1000.0 + i,
                         "turnover": 0.01, "sw_l1": "801010"})
    result = mod.cross_section_regression(_trades(rows), _panel(rows))
    assert result["status"] == "unavailable"
    assert result["reason"] == "insufficient_cross_section_sample"
    assert result["days_estimable"] == 0


def test_no_panel_unavailable():
    result = mod.cross_section_regression(_trades(_rows()), None)
    assert result["status"] == "unavailable"
    assert result["reason"] == "panel_missing"
    assert result["used_in_go"] is False


# ---------------------------------------------------------------------------
# 不参与 GO：回归 ok 与否不改变 8 门/结论
# ---------------------------------------------------------------------------
def test_exposure_regression_does_not_enter_go():
    rows = _rows(noise=True)
    trades = _trades(rows)
    panel = _panel(rows)
    events = [{"date": row["event_date"], "ts_code": row["ts_code"],
               "event_type": row["event_type"]} for row in rows]
    with_panel = mod.evaluate_all(trades, panel=panel, events=events)
    without_panel = mod.evaluate_all(trades)

    assert with_panel["exposure_regression"]["status"] == "ok"
    assert without_panel["exposure_regression"]["status"] == "unavailable"
    assert without_panel["exposure_regression"]["reason"] == "panel_missing"
    # 8 门判定与结论不因 §5.2 结果变化（仅中性化/R2 原因文本可能不同）
    for etype in mod.SPEC["event_types"]:
        assert with_panel["gates"][etype]["gates"] == \
            without_panel["gates"][etype]["gates"]
        assert with_panel["gates"][etype]["verdict"] == \
            without_panel["gates"][etype]["verdict"]
        assert with_panel["gates"][etype]["score"] == \
            without_panel["gates"][etype]["score"]
        assert set(with_panel["gates"][etype]["gates"]) == set(mod.SPEC["gates"])
        assert "exposure_regression" not in with_panel["gates"][etype]
    assert with_panel["exposure_regression"]["used_in_go"] is False
