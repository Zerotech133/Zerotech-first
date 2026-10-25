# -*- coding: utf-8 -*-
"""OS-FIX-JEVSCOPE-20261026：本地 JEV 判读 scope 枚举回归。

JEV 审计 §四-①：本地判读 prompt 缺 scope → 新判行 scope 100% 空，
而老云端行用 cn/hk/us/commodity/crypto/fx/macro 七值。本测试锁三件事：
1) prompt 已包含 scope 枚举；
2) 模型返回的中文/别名 scope 被归一到七值；
3) 未知/缺失 scope 降级为空串（不猜、不伪造）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import news_ai  # noqa: E402


class _FakeResp:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": json.dumps(self._content)}}


def _judge_with(monkeypatch, ans):
    seen = {}

    def fake_post(url, **kwargs):  # noqa: ANN001
        seen["prompt"] = kwargs["json"]["messages"][0]["content"]
        return _FakeResp(ans)

    monkeypatch.setattr(news_ai.requests, "post", fake_post)
    monkeypatch.setattr(news_ai, "jev_reason", lambda *a, **k: "依据")
    d = news_ai.jev_judge_one_local("测试快讯：某公司发布业绩预告", [])
    return d, seen.get("prompt", "")


def test_prompt_contains_scope_enum(monkeypatch):
    ans = {"bias": "bull", "conf": 0.9, "scope": "cn",
           "event_type": "earnings", "impact": "high", "companies": []}
    _, prompt = _judge_with(monkeypatch, ans)
    assert '"scope"' in prompt
    assert "cn|hk|us|commodity|crypto|fx|macro" in prompt
    assert "macro 宏观" in prompt


def test_scope_alias_normalized(monkeypatch):
    ans = {"bias": "bull", "conf": 0.9, "scope": "A股",
           "event_type": "policy", "impact": "high", "companies": []}
    d, _ = _judge_with(monkeypatch, ans)
    assert d is not None
    assert d["scope"] == "cn"
    assert d["scope_cn"] == "A股"


def test_scope_english_enum_kept(monkeypatch):
    ans = {"bias": "neutral", "conf": 0.8, "scope": "commodity",
           "event_type": "price", "impact": "low", "companies": []}
    d, _ = _judge_with(monkeypatch, ans)
    assert d["scope"] == "commodity" and d["scope_cn"] == "商品"


def test_scope_unknown_degrades_empty(monkeypatch):
    ans = {"bias": "neutral", "conf": 0.8, "scope": "foo-market",
           "event_type": "other", "impact": "low", "companies": []}
    d, _ = _judge_with(monkeypatch, ans)
    assert d is not None
    assert d["scope"] == "" and d["scope_cn"] == ""


def test_scope_missing_degrades_empty(monkeypatch):
    ans = {"bias": "bear", "conf": 0.9,
           "event_type": "risk", "impact": "medium", "companies": []}
    d, _ = _judge_with(monkeypatch, ans)
    assert d is not None
    assert d["scope"] == ""


def test_scope_enum_has_seven_values():
    assert set(news_ai._SCOPE_CN) == {
        "cn", "hk", "us", "commodity", "crypto", "fx", "macro"}


def test_normalize_scope_unit():
    assert news_ai._normalize_scope("港股") == "hk"
    assert news_ai._normalize_scope("US") == "us"
    assert news_ai._normalize_scope("外汇") == "fx"
    assert news_ai._normalize_scope("") == ""
    assert news_ai._normalize_scope(None) == ""
    assert news_ai._normalize_scope("未知") == ""
