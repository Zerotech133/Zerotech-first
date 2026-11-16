# -*- coding: utf-8 -*-
"""投研派活器 v2 测试（2026-11-16 WO-RESEARCH-DISPATCH v2）。

覆盖：三态判定 / 五档契约 / 风险等级 / 置信度归一 / 辩论触发 / 索引去重。
不调真实模型（用假 client）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
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


class FakeClient:
    """按队列返回预设文本，模拟不同执行结果。"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    def chat_full(self, messages, model=None, temperature=0.0, max_tokens=0):
        self.calls += 1
        t = self.texts.pop(0) if self.texts else ""
        return {"content": t}


def test_parse_json_variants():
    ok = '{"stance":"bull","points":["a"],"confidence":0.6}'
    assert rd._parse_json(ok)["stance"] == "bull"
    assert rd._parse_json("```json\n%s\n```" % ok)["stance"] == "bull"
    assert rd._parse_json("前言\n%s\n后记" % ok)["stance"] == "bull"
    assert rd._parse_json("完全不是 JSON") is None
    assert rd._parse_json('{"stance":"bull","points":[') is None


def test_call_modes_normal_and_degraded():
    good = rd._call(FakeClient(['{"stance":"bull","confidence":0.5}']), "s", "u", "m", 100)
    assert good["ok"] is True and good["mode"] == "normal"
    # 有输出但 JSON 截断 → degraded（不是"席位缺席"）
    bad = rd._call(FakeClient(['{"stance":"bull","points":["x"']), "s", "u", "m", 100)
    assert bad["ok"] is False and bad["mode"] == "degraded" and bad["error"] == "no_json"
    # 完全空 → degraded
    empty = rd._call(FakeClient(["", ""]), "s", "u", "m", 100)
    assert empty["ok"] is False and empty["mode"] == "degraded" and empty["error"] == "empty_reply"


def _seat(stance: str, conf: float = 0.5, mode: str = "normal") -> dict:
    return {"ok": mode == "normal", "mode": mode,
            "result": {"stance": stance, "confidence": conf, "verdict": "x"} if mode == "normal" else None}


def test_summarize_unknown_when_all_degraded():
    s = rd.summarize("X", {k: _seat("unknown", mode="degraded") for k in rd.SEATS})
    assert s["recommendation_level"] == "UNKNOWN" and s["confidence_score"] == 0.0
    assert s["seats_degraded"] == 4 and s["risk_level"] == "HIGH"


def test_summarize_buy_and_strong_buy():
    buy = rd.summarize("X", {"T1": _seat("bull", 0.6), "T2": _seat("unknown"),
                             "N1": _seat("bull", 0.4), "F1": _seat("unknown")})
    assert buy["recommendation_level"] == "BUY" and 0 <= buy["confidence_score"] <= 100
    strong = rd.summarize("X", {"T1": _seat("bull", 0.8), "T2": _seat("neutral", 0.5),
                                "N1": _seat("bull", 0.7), "F1": _seat("bull", 0.6)})
    assert strong["recommendation_level"] == "STRONG_BUY"


def test_summarize_sell_levels():
    # 空头须带技术面依据（跌破/破位），否则会被粒度收口降为 HOLD
    tech = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["跌破前低 12.8"], "verdict": "价格破位", "risks": []}}
    sell = rd.summarize("X", {"T1": _seat("bull", 0.5), "T2": tech,
                              "N1": _seat("bear", 0.5), "F1": _seat("unknown")})
    assert sell["recommendation_level"] == "SELL" and sell["risk_level"] == "HIGH"
    strong = rd.summarize("X", {"T1": _seat("bear", 0.7), "T2": tech,
                                "N1": _seat("bear", 0.7), "F1": _seat("unknown")})
    assert strong["recommendation_level"] == "STRONG_SELL"


def test_summarize_sell_downgraded_without_technical():
    """两个 bear 但都只有估值/资金依据 → 降 HOLD（'不该买' ≠ '该卖'）。"""
    val = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["PE 887 倍"], "verdict": "估值太高", "risks": []}}
    s = rd.summarize("X", {"T1": _seat("bull"), "T2": val, "N1": val, "F1": _seat("unknown")})
    assert s["recommendation_level"] == "HOLD"
    assert s["rule_level_downgraded"] in ("SELL", "STRONG_SELL")
    assert "缺技术面" in s["rationale"]


def test_confidence_normalized_to_0_100():
    s = rd.summarize("X", {"T1": _seat("bull", 0.5), "T2": _seat("unknown", 1.0),
                           "N1": _seat("unknown", 1.0), "F1": _seat("unknown", 1.0)})
    # 0.5 与三个 1.0 混合：0.5 会被当成 0.5（<=1 → ×100=50），1.0 → 100
    assert s["confidence_score"] > 50, s


def test_target_price_left_null():
    s = rd.summarize("X", {"T1": _seat("bull"), "T2": _seat("bear"),
                           "N1": _seat("unknown"), "F1": _seat("unknown")})
    assert s["target_price_min"] is None and s["target_price_max"] is None
    assert "留空" in s["target_price_note"]


def test_risk_level_matrix():
    assert rd._risk_level(0, 2, 0, 0) == "HIGH"
    assert rd._risk_level(0, 0, 0, 2) == "HIGH"
    assert rd._risk_level(1, 1, 0, 0) == "MEDIUM"
    assert rd._risk_level(0, 0, 2, 0) == "MEDIUM"
    assert rd._risk_level(1, 0, 0, 0) == "LOW"      # 纯多头单边不构成风险
    assert rd._risk_level(4, 0, 0, 0) == "LOW"


def test_debate_only_when_opposed(monkeypatch):
    """T1/T2 立场相反才辩；同向不辩。"""
    item = {"code": "X"}
    cards = {}
    seats_opposed = {"T1": _seat("bull"), "T2": _seat("bear"),
                     "N1": _seat("unknown"), "F1": _seat("unknown")}
    called = {"n": 0}

    def fake_call(client, system, user, model, max_tokens):
        called["n"] += 1
        return {"ok": True, "mode": "normal", "elapsed_ms": 1, "raw_head": "",
                "result": {"stance": "neutral", "confidence": 0.4, "verdict": "辩后"},
                "error": ""}

    monkeypatch.setattr(rd, "_call", fake_call)
    res = rd.run_debate(FakeClient([]), item, cards, seats_opposed, {}, "m", 100, rounds=2)
    assert len(res["rounds"]) == 2 and called["n"] == 4     # 2 轮 × 2 席
    assert res["rounds"][0]["round"] == 1
    assert res["changed"] is True                            # bull/bear → neutral


def test_should_debate_gate():
    """只在立场正好相反时触发；同向/未知/中性/关轮数都不辩。"""
    assert rd.should_debate("bull", "bear", 2) is True
    assert rd.should_debate("bear", "bull", 2) is True
    assert rd.should_debate("bull", "bull", 2) is False
    assert rd.should_debate("bear", "bear", 2) is False
    assert rd.should_debate("bull", "unknown", 2) is False
    assert rd.should_debate("unknown", "bear", 2) is False
    assert rd.should_debate("neutral", "bear", 2) is False
    assert rd.should_debate("bull", "bear", 0) is False      # --debate-rounds 0 关闭


def test_debate_log_keeps_both_sides_text(monkeypatch):
    """辩论日志必须同时留下双方原话（可复核），而不只是最终 stance。"""
    seats = {"T1": _seat("bull"), "T2": _seat("bear"),
             "N1": _seat("unknown"), "F1": _seat("unknown")}
    seq = iter([
        {"stance": "bull", "confidence": 0.5, "verdict": "T1轮1：我承认高位，但无派发信号"},
        {"stance": "bear", "confidence": 0.6, "verdict": "T2轮1：无派发不等于安全，事件未核验"},
    ])

    def fake_call(client, system, user, model, max_tokens):
        r = next(seq)
        return {"ok": True, "mode": "normal", "elapsed_ms": 1, "raw_head": "",
                "result": r, "error": ""}

    monkeypatch.setattr(rd, "_call", fake_call)
    res = rd.run_debate(FakeClient([]), {"code": "X"}, {}, seats, {}, "m", 100, rounds=1)
    r0 = res["rounds"][0]
    assert "我承认高位" in r0["T1"]["verdict"] and "无派发不等于安全" in r0["T2"]["verdict"]
    assert r0["T1"]["mode"] == "normal" and r0["T2"]["mode"] == "normal"


def test_should_finalize_gate():
    """有对立就启用决胜席；单边/全同向不需要。"""
    assert rd.should_finalize(1, 1) is True
    assert rd.should_finalize(2, 1) is True
    assert rd.should_finalize(1, 2) is True
    assert rd.should_finalize(0, 2) is False        # 纯空头：规则已能定 SELL
    assert rd.should_finalize(3, 0) is False        # 纯多头
    assert rd.should_finalize(0, 0) is False


def _final(level="SELL", conf=72, ok=True, mode="normal"):
    return {"ok": ok, "mode": mode, "seat": "FD", "phase": "final",
            "result": {"decision_level": level, "confidence": conf,
                       "decisive_evidence": ["e1"], "rejected_arguments": ["r1"],
                       "rationale": "终结理由"} if ok else None}


def test_summarize_final_overrides_and_keeps_rule():
    seats = {"T1": _seat("bull", 0.5), "T2": _seat("bear", 0.6),
             "N1": _seat("neutral", 0.4), "F1": _seat("unknown", 0.3)}
    base = rd.summarize("X", seats)
    assert base["decided_by"] == "rule" and "rule_level" not in base

    over = rd.summarize("X", seats, None, _final("SELL", 72))
    assert over["decided_by"] == "final_seat"
    assert over["recommendation_level"] == "SELL"
    assert over["confidence_score"] == 72.0
    assert over["rule_level"] == base["recommendation_level"]      # 规则结论仍可查
    assert over["decisive_evidence"] == ["e1"] and over["rejected_arguments"] == ["r1"]


def test_summarize_falls_back_when_final_degraded():
    seats = {"T1": _seat("bull"), "T2": _seat("bear"), "N1": _seat("unknown"), "F1": _seat("unknown")}
    rule = rd.summarize("X", seats)
    deg = rd.summarize("X", seats, None, _final(ok=False, mode="degraded"))
    assert deg["recommendation_level"] == rule["recommendation_level"]   # 回落规则
    assert deg["decided_by"] == "rule"
    assert deg["final_seat_status"] == "degraded"


def test_summarize_ignores_illegal_final_level():
    seats = {"T1": _seat("bull"), "T2": _seat("bear"), "N1": _seat("unknown"), "F1": _seat("unknown")}
    rule = rd.summarize("X", seats)
    bad = rd.summarize("X", seats, None, _final("MAYBE", 50))
    assert bad["recommendation_level"] == rule["recommendation_level"]
    assert bad["decided_by"] == "rule"


def test_critical_seat_degraded_blocks_directional_call():
    """T1/T2 降级时不得出 BUY/SELL——否则 degraded 会被误当成'无空头反对'。"""
    # T2 降级 + 两席 bull → 规则本会判 BUY，必须降为 HOLD
    seats = {"T1": _seat("bull", 0.6), "T2": _seat("unknown", mode="degraded"),
             "N1": _seat("neutral"), "F1": _seat("bull", 0.5)}
    s = rd.summarize("X", seats)
    assert s["recommendation_level"] == "HOLD"
    assert s["rule_level_downgraded"] == "BUY"
    assert "关键席位降级" in s["rationale"] and "T2" in s["rationale"]

    # T1 降级 + 两席 bear → 本会判 SELL，也必须降为 HOLD
    seats2 = {"T1": _seat("unknown", mode="degraded"), "T2": _seat("bear", 0.6),
              "N1": _seat("bear", 0.5), "F1": _seat("unknown")}
    s2 = rd.summarize("X", seats2)
    assert s2["recommendation_level"] == "HOLD" and s2["rule_level_downgraded"] == "SELL"

    # 非关键席位（N1）降级不影响方向性结论
    seats3 = {"T1": _seat("bull", 0.6), "T2": _seat("bull", 0.5),
              "N1": _seat("unknown", mode="degraded"), "F1": _seat("bull", 0.6)}
    s3 = rd.summarize("X", seats3)
    assert s3["recommendation_level"] in ("BUY", "STRONG_BUY")
    assert "rule_level_downgraded" not in s3


def test_is_transient_classification():
    assert rd._is_transient("DeepSeekError: DeepSeek 调用失败（HTTP 0: ）") is True
    assert rd._is_transient("TimeoutError: timeout") is True
    assert rd._is_transient("ConnectionError: reset") is True
    assert rd._is_transient("no_json") is False          # 内容问题，重试无益
    assert rd._is_transient("empty_reply") is False


def test_transport_error_retried_then_succeeds(monkeypatch):
    """连接类错误要退避重试，恢复后标记 recovered_by_retry（不再整只标失效）。"""
    calls = {"n": 0}

    class Flaky:
        def chat_full(self, messages, model=None, temperature=0.0, max_tokens=0):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("DeepSeek 调用失败（HTTP 0: ）")
            return {"content": '{"stance":"bull","confidence":0.5}'}

    monkeypatch.setattr(rd.time, "sleep", lambda s: None)     # 不真等
    out = rd._call(Flaky(), "s", "u", "m", 100, transport_retries=3)
    assert out["ok"] is True and out["mode"] == "normal"
    assert out["recovered_by_retry"] is True
    assert calls["n"] == 3


def test_no_json_not_retried_as_transport(monkeypatch):
    """内容问题（截断）重试次数应保持在内容层，不触发连接层重试。"""
    calls = {"n": 0}

    class Truncated:
        def chat_full(self, messages, model=None, temperature=0.0, max_tokens=0):
            calls["n"] += 1
            return {"content": '{"stance":"bull","points":["x"'}   # 永远不闭合

    monkeypatch.setattr(rd.time, "sleep", lambda s: None)
    out = rd._call(Truncated(), "s", "u", "m", 100, transport_retries=3)
    assert out["ok"] is False and out["mode"] == "degraded" and out["error"] == "no_json"
    assert calls["n"] == 2                                     # 只走内容层 2 次


def test_short_types_negation_aware():
    """'无破位证据' 不能算技术型空头；'估值与基本面背离' 也不是价格背离。"""
    val = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["PE 887 倍，估值极端"], "verdict": "无破位证据，但估值太高",
        "risks": []}}
    t = rd.short_types(val)
    assert "valuation" in t and "technical" not in t        # 否定语境不计技术面

    tech = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["已跌破 15.33 支撑", "250 日新低"], "verdict": "价格破位",
        "risks": []}}
    assert "technical" in rd.short_types(tech)

    amb = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["估值与基本面背离"], "verdict": "x", "risks": []}}
    assert "technical" not in rd.short_types(amb)           # 基本面背离 ≠ 价格背离


def test_decide_final_level_requires_technical_bear():
    """只靠估值/资金偏空 → 不该 SELL。"""
    seats_val = {"T1": _seat("bull"), "T2": _seat("bear"), "N1": _seat("bear"), "F1": _seat("unknown")}
    seats_val["T2"] = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["PE 高"], "verdict": "估值太高", "risks": []}}
    seats_val["N1"] = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["主力净流出"], "verdict": "资金弱", "risks": []}}
    lvl, note = rd.decide_final_level("SELL", seats_val)
    assert lvl == "HOLD" and "缺技术面" in note

    seats_tech = dict(seats_val)
    seats_tech["T2"] = {"ok": True, "mode": "normal", "result": {
        "stance": "bear", "points": ["跌破 15.33 支撑"], "verdict": "价格破位", "risks": []}}
    lvl2, note2 = rd.decide_final_level("SELL", seats_tech)
    assert lvl2 == "SELL" and note2 == ""


def test_upsert_index_dedup(tmp_path, monkeypatch):
    idx = tmp_path / "research_index.jsonl"
    monkeypatch.setattr(rd, "INDEX", idx)
    rd.upsert_index({"code": "A", "pool_date": "2026-11-16", "recommendation": "HOLD", "confidence": 10})
    rd.upsert_index({"code": "A", "pool_date": "2026-11-16", "recommendation": "BUY", "confidence": 70})
    rd.upsert_index({"code": "B", "pool_date": "2026-11-16", "recommendation": "HOLD", "confidence": 20})
    rows = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2
    assert {r["code"]: r["recommendation"] for r in rows} == {"A": "BUY", "B": "HOLD"}


def test_slim_xsec_whitelist_and_prompt():
    raw = {"funnel": {"primary_hypothesis": {"primary": "runup", "name": "抢跑异动"},
                      "ops": {"hypothesis_distribute": {"hit": True},
                              "context": {"pos250": 0.99}}},
           "line2_kline": {"close": 15.04, "noise": "drop"}}
    s = rd.slim_xsec(raw)
    assert s["primary"] == "runup" and s["ops_hit"]["hypothesis_distribute"] is True
    assert "noise" not in s["kline_last"]
    item = {"code": "X", "name": "n", "market": "CN", "signal": "CHAN", "score": 100.0,
            "selection_reason": "买点", "risk_flags": []}
    _, without = rd.build_prompt("T1", item, {}, None)
    _, with_x = rd.build_prompt("T1", item, {}, s)
    assert "xsec_profile" not in without and "xsec_profile" in with_x
    assert "篇幅硬约束" in with_x          # 长度约束必须在
