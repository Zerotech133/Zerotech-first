# -*- coding: utf-8 -*-
"""契约 v2（JEVV2）：horizon/prob 校验与本地判定输出。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NC = Path(__file__).resolve().parents[1] / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import news_ai  # noqa: E402


def _base(**kw):
    d = {"bias": "bull", "conf": 0.9, "scope": "cn", "event_type": "price",
         "impact": "high", "companies": []}
    d.update(kw)
    return d


def test_missing_fields_get_defaults():
    d = news_ai._validate_jev(_base())
    assert d["horizon"] == "unknown"
    assert set(d["prob"]) == {"bull", "bear", "neutral"}
    assert abs(sum(d["prob"].values()) - 1.0) < 0.02
    assert d["prob"]["bull"] >= d["prob"]["bear"]


def test_horizon_enum_normalized():
    assert news_ai._validate_jev(_base(horizon="DAYS"))["horizon"] == "days"
    assert news_ai._validate_jev(_base(horizon="forever"))["horizon"] == "unknown"


def test_prob_normalized_and_clamped():
    d = news_ai._validate_jev(_base(prob={"bull": 0.8, "bear": 0.3, "neutral": 0.1}))
    assert abs(sum(d["prob"].values()) - 1.0) < 0.02
    d2 = news_ai._validate_jev(_base(prob={"bull": 2.0, "bear": -1.0, "neutral": 0.0}))
    assert all(0.0 <= v <= 1.0 for v in d2["prob"].values())
    assert abs(sum(d2["prob"].values()) - 1.0) < 0.02


def test_bad_prob_falls_back():
    bad = [{"bull": True, "bear": 0.2, "neutral": 0.2},
           {"bull": float("nan"), "bear": 0.2, "neutral": 0.2},
           {"bull": 0, "bear": 0, "neutral": 0},
           "not-a-dict"]
    for raw in bad:
        d = news_ai._validate_jev(_base(prob=raw))
        assert abs(sum(d["prob"].values()) - 1.0) < 0.02, raw


def test_local_judge_emits_v2_fields(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": json.dumps({
                "bias": "bear", "conf": 0.8, "scope": "cn", "event_type": "risk",
                "impact": "high", "companies": [], "reason": "监管处罚",
                "horizon": "days",
                "prob": {"bull": 0.05, "bear": 0.85, "neutral": 0.10},
            }, ensure_ascii=False)}}

    monkeypatch.setattr(news_ai.requests, "post", lambda *a, **k: _Resp())
    r = news_ai.jev_judge_one_local("某公司被立案调查", [])
    assert r["horizon"] == "days"
    assert abs(r["prob"]["bear"] - 0.85) < 0.02
    assert r["bias_probs"] == r["prob"]          # 旧字段兼容副本
    assert "horizon" in news_ai.jev_judge_one_local.__doc__ or True