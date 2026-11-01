# -*- coding: utf-8 -*-
"""tools/tests/test_screening_rs.py — 量学筛选 × 板块 RS 叠加（RS 产品化）。

覆盖（任务验收 ≥5）：
  1. 最新交易日探测：d_YYYYMMDD.parquet 取最大日期，忽略非法文件名；
  2. rs_pass A/B/C 判定（pct_chg 为百分比数值；A 等号、B +1.0pp、C 双 ≥1.0%）；
  3. 板块强度：行业等权当日涨幅计算正确（含行业缺失剔除）；
  4. RS overlay schema：全市场通过数/交集清单字段/超额排序/--rs-top 截断；
  5. off 模式：enabled=False、无重计算、JSON 可序列化；
  6. CLI 默认值：--rs-mode off、--rs-top 50、--date None（自动探测）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_screening_rs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import screening_20261101 as scr  # noqa: E402


# ------------------------------------------------------------------ 1. 最新交易日
def test_detect_latest_trade_date(tmp_path):
    for d in ("20261028", "20261029", "20261030"):
        pd.DataFrame({"ts_code": ["A"], "pct_chg": [1.0]}).to_parquet(tmp_path / f"d_{d}.parquet")
    (tmp_path / "d_bad.parquet").write_bytes(b"x")
    (tmp_path / "other.parquet").write_bytes(b"x")
    assert scr.detect_latest_trade_date(tmp_path) == "20261030"
    with pytest.raises(FileNotFoundError):
        scr.detect_latest_trade_date(tmp_path / "empty")


# ------------------------------------------------------------------ 2. A/B/C 判定
def test_rs_pass_modes():
    # A：个股 ≥ 板块（等号通过）
    assert scr.rs_pass(2.0, 2.0, "A") is True
    assert scr.rs_pass(1.99, 2.0, "A") is False
    # B：超额 ≥ 1.0pp
    assert scr.rs_pass(3.0, 2.0, "B") is True
    assert scr.rs_pass(2.99, 2.0, "B") is False
    assert scr.rs_pass(1.0, 0.0, "B") is True
    # C：板块 ≥1.0 且 个股 ≥1.0
    assert scr.rs_pass(1.0, 1.0, "C") is True
    assert scr.rs_pass(0.99, 2.0, "C") is False      # 个股不足 1%
    assert scr.rs_pass(3.0, 0.99, "C") is False      # 板块不足 1%
    with pytest.raises(ValueError):
        scr.rs_pass(1.0, 1.0, "X")


# ------------------------------------------------------------------ 3. 板块等权强度
def test_board_strength_equal_weight():
    daily = pd.DataFrame({"ts_code": ["A1", "A2", "B1", "C1"],
                          "pct_chg": [2.0, 4.0, -1.0, 10.0]})
    nm = {"A1": {"industry": "行业A"}, "A2": {"industry": "行业A"},
          "B1": {"industry": "行业B"}, "C1": {"industry": None}}   # C1 无行业 → 剔除
    res = scr.board_strength(daily, {c: (m or {}).get("industry") for c, m in nm.items()})
    assert res["boards"]["行业A"] == {"pct_chg": 3.0, "n": 2}
    assert res["boards"]["行业B"] == {"pct_chg": -1.0, "n": 1}
    assert res["n_stocks"] == 3 and res["n_boards"] == 2
    assert res["stocks"]["A2"]["board_pct_chg"] == 3.0
    assert res["stocks"]["A2"]["pct_chg"] == 4.0


# ------------------------------------------------------------------ 4. RS overlay schema
def _overlay_case():
    daily = pd.DataFrame({
        "ts_code": ["A1", "A2", "A3", "B1", "B2"],
        "pct_chg": [2.0, 5.0, 1.0, 3.0, 0.5],
    })
    nm = {"A1": {"name": "甲", "industry": "行业A"}, "A2": {"name": "乙", "industry": "行业A"},
          "A3": {"name": "丙", "industry": "行业A"}, "B1": {"name": "丁", "industry": "行业B"},
          "B2": {"name": "戊", "industry": "行业B"}}
    signals = {"A2": ["LX20_牛股三绝", "LX06_长阴短柱"], "A1": ["LX20_牛股三绝"],
               "B1": ["LX22_凹口淘金"]}
    return daily, nm, signals


def test_build_rs_overlay_schema_and_intersection():
    daily, nm, signals = _overlay_case()
    # 行业A 等权均值=(2+5+1)/3=2.667；行业B=(3+0.5)/2=1.75
    # mode C：板块≥1.0 且个股≥1.0 → A1,A2,A3,B1 通过（B2 0.5 不通过）
    rs = scr.build_rs_overlay("20261030", "C", 2, daily, nm, signals)
    assert rs["enabled"] is True and rs["mode"] == "C"
    assert rs["universe"] == {"stocks": 5, "boards": 2}
    assert rs["pass_count"] == 4
    assert rs["pinxue_signal_count"] == 3
    assert rs["intersection_count"] == 3          # A1,A2,B1
    assert len(rs["intersection"]) == 2           # rs_top=2 截断
    assert rs["intersection"][0]["ts_code"] == "A2"   # 超额最大 (5-2.667=2.333)
    for it in rs["intersection"]:
        for key in ("ts_code", "name", "industry", "pct_chg", "board_pct_chg",
                    "excess_pp", "rs_flag", "signals"):
            assert key in it, f"交集清单缺字段 {key}"
        assert it["rs_flag"] == "C"
        assert it["excess_pp"] == round(it["pct_chg"] - it["board_pct_chg"], 4)
    # 模式 A：全部个股≥板块？
    rs_a = scr.build_rs_overlay("20261030", "A", 0, daily, nm, signals)
    assert rs_a["rs_top"] == 0
    assert len(rs_a["intersection"]) == rs_a["intersection_count"]
    json.dumps(rs_a, ensure_ascii=False)


# ------------------------------------------------------------------ 5. off 模式
def test_off_mode():
    daily, nm, signals = _overlay_case()
    rs = scr.build_rs_overlay("20261030", "off", 50, daily, nm, signals)
    assert rs["enabled"] is False and rs["mode"] == "off"
    assert "intersection" not in rs          # 不重计算
    json.dumps(rs, ensure_ascii=False)


# ------------------------------------------------------------------ 6. CLI 默认值
def test_cli_defaults():
    ap = scr.build_parser()
    args = ap.parse_args([])
    assert args.rs_mode == "off"
    assert args.rs_top == scr.DEFAULT_RS_TOP == 50
    assert args.date is None                  # 默认自动探测最新交易日
    args2 = ap.parse_args(["--date", "20261030", "--rs-mode", "B", "--rs-top", "10"])
    assert (args2.date, args2.rs_mode, args2.rs_top) == ("20261030", "B", 10)
