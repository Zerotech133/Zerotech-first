# -*- coding: utf-8 -*-
"""投研派活器测试（2026-11-16 WO-RESEARCH-DISPATCH）。

只测纯函数（JSON 解析 / 汇总规则 / 索引去重），不调真实模型。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "research_dispatch_t", REPO / "tools" / "research_dispatch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load()


def test_parse_json_variants():
    ok = '{"stance":"bull","points":["a"],"confidence":0.6}'
    assert rd._parse_json(ok)["stance"] == "bull"
    assert rd._parse_json("```json\n%s\n```" % ok)["stance"] == "bull"
    assert rd._parse_json("前言\n%s\n后记" % ok)["stance"] == "bull"
    assert rd._parse_json("完全不是 JSON") is None
    assert rd._parse_json('{"stance":"bull","points":[') is None  # 截断


def _seat(stance: str, conf: float = 0.5) -> dict:
    return {"ok": True, "result": {"stance": stance, "confidence": conf, "verdict": "x"}}


def test_summarize_unknown_when_all_missing():
    s = rd.summarize("X", {k: {"ok": False, "result": None} for k in rd.SEATS})
    assert s["recommendation"] == "UNKNOWN" and s["confidence"] == 0.0
    assert s["seats_ok"] == 0


def test_summarize_buy_when_bull_and_no_bear():
    seats = {"T1": _seat("bull", 0.6), "T2": _seat("unknown"), "N1": _seat("bull", 0.4), "F1": _seat("unknown")}
    s = rd.summarize("X", seats)
    assert s["recommendation"] == "BUY" and s["seats_ok"] == 4


def test_summarize_avoid_when_two_bears():
    seats = {"T1": _seat("bull"), "T2": _seat("bear", 0.7), "N1": _seat("bear", 0.5), "F1": _seat("unknown")}
    s = rd.summarize("X", seats)
    assert s["recommendation"] == "AVOID"


def test_summarize_hold_on_divergence_or_partial():
    seats = {"T1": _seat("bull"), "T2": _seat("bear"), "N1": _seat("unknown"), "F1": {"ok": False, "result": None}}
    s = rd.summarize("X", seats)
    assert s["recommendation"] == "HOLD" and s["seats_ok"] == 3
    assert "多空对立" in s["rationale"]          # 真有对冲才叫分歧


def test_summarize_wording_distinguishes_causes():
    """三种 HOLD 原因必须给不同文案——全未知 ≠ 分歧 ≠ 全中性。"""
    all_unknown = {k: _seat("unknown") for k in rd.SEATS}
    s1 = rd.summarize("X", all_unknown)
    assert s1["recommendation"] == "HOLD" and "四席均无立场" in s1["rationale"]
    assert s1["stance_counts"] == {"bull": 0, "bear": 0, "neutral": 0, "unknown": 4}

    one_side = {"T1": _seat("bull"), "T2": _seat("unknown"),
                "N1": _seat("unknown"), "F1": _seat("unknown")}
    s2 = rd.summarize("X", one_side)
    assert "单边有立场但反方无证据" in s2["rationale"]

    all_neutral = {k: _seat("neutral") for k in rd.SEATS}
    s3 = rd.summarize("X", all_neutral)
    assert "四席均中性" in s3["rationale"]


def test_slim_xsec_keeps_only_seat_relevant():
    raw = {"funnel": {"primary_hypothesis": {"primary": "runup", "name": "抢跑异动",
                                             "next_verification": "待事件耦合"},
                      "ops": {"hypothesis_distribute": {"hit": True, "evidence": "x"},
                              "hypothesis_runup": {"hit": True},
                              "context": {"close": 15.04, "pos250": 0.99}}},
           "line2_kline": {"close": 15.04, "pos250": 0.99, "noise": "drop me"}}
    s = rd.slim_xsec(raw)
    assert s["primary"] == "runup" and s["primary_name"] == "抢跑异动"
    assert s["ops_hit"]["hypothesis_distribute"] is True
    assert s["context"]["pos250"] == 0.99
    assert "noise" not in s["kline_last"]          # 只保留白名单字段
    assert rd.slim_xsec({}) == {}                  # 生成失败不阻塞


def test_build_prompt_includes_xsec_when_present():
    item = {"code": "001228.SZ", "name": "永泰运", "market": "CN", "signal": "CHAN_V7",
            "score": 100.0, "selection_reason": "基础信号：买点", "risk_flags": []}
    _, without = rd.build_prompt("T1", item, {}, None)
    _, with_x = rd.build_prompt("T1", item, {}, {"primary": "runup", "context": {"pos250": 0.89}})
    assert "xsec_profile" not in without
    assert "xsec_profile" in with_x and "runup" in with_x and "0.89" in with_x


def test_upsert_index_dedup(tmp_path, monkeypatch):
    idx = tmp_path / "research_index.jsonl"
    monkeypatch.setattr(rd, "INDEX", idx)
    rd.upsert_index({"code": "A", "pool_date": "2026-11-16", "recommendation": "HOLD", "confidence": 0.1})
    rd.upsert_index({"code": "A", "pool_date": "2026-11-16", "recommendation": "BUY", "confidence": 0.7})
    rd.upsert_index({"code": "B", "pool_date": "2026-11-16", "recommendation": "HOLD", "confidence": 0.2})
    rows = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2                      # 重跑同一只不产生重复
    assert {r["code"]: r["recommendation"] for r in rows} == {"A": "BUY", "B": "HOLD"}  # 后者覆盖
