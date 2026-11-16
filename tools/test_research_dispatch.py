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
    sell = rd.summarize("X", {"T1": _seat("bull", 0.5), "T2": _seat("bear", 0.5),
                              "N1": _seat("bear", 0.5), "F1": _seat("unknown")})
    assert sell["recommendation_level"] == "SELL" and sell["risk_level"] == "HIGH"
    strong = rd.summarize("X", {"T1": _seat("bear", 0.7), "T2": _seat("bear", 0.7),
                                "N1": _seat("bear", 0.7), "F1": _seat("unknown")})
    assert strong["recommendation_level"] == "STRONG_SELL"


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
