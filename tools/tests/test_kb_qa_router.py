# -*- coding: utf-8 -*-
"""kb_qa 生成提供方分派（W27）：ollama 默认 / router 三级降级，不变更旧契约。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
QA_PATH = REPO / "tools" / "kb_corpus" / "kb_qa.py"


def _load_qa():
    spec = importlib.util.spec_from_file_location("kb_qa_router_under_test", QA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qa = _load_qa()
HITS = [{"id": "41692ee59293", "book": "rp_X", "chapter": "c", "page_start": 2,
         "unit_id": "41692ee59293", "rank": 1, "kind": "unit", "origin": "units",
         "fused_score": 1.0, "text": "M9 覆铜板支持 224Gbps。"}]


def fake_search(question, topk):
    return HITS


class FakeRouter:
    def __init__(self, ok=True, text="ROUTER 答案 [41692ee59293] p.2",
                 attempts=None):
        self.ok = ok
        self.text = text
        self.attempts = attempts or []
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return {"ok": self.ok, "text": self.text, "attempts": self.attempts}


def test_default_provider_is_ollama(monkeypatch):
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "ollama")
    monkeypatch.setattr(qa, "call_ollama", lambda *a, **k: "LOCAL")
    assert qa.chat("p") == "LOCAL"
    assert qa.llm_endpoint() == qa.OLLAMA_ENDPOINT


def test_router_dispatch_and_purpose(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "router")
    monkeypatch.setattr(qa, "_router_module", lambda: fake)
    assert qa.chat("问题", num_predict=64) == "ROUTER 答案 [41692ee59293] p.2"
    assert fake.calls and fake.calls[0]["purpose"] == "kb_qa"
    assert fake.calls[0]["max_tokens"] == 64
    assert qa.llm_endpoint().startswith("router(")


def test_router_failure_raises_kbqaerror(monkeypatch):
    fake = FakeRouter(ok=False, attempts=[{"tier": "t1", "status": 500}])
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "router")
    monkeypatch.setattr(qa, "_router_module", lambda: fake)
    with pytest.raises(qa.KbQaError, match="全部降级失败"):
        qa.chat("问题")


def test_router_empty_text_raises(monkeypatch):
    fake = FakeRouter(ok=True, text="   ")
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "router")
    monkeypatch.setattr(qa, "_router_module", lambda: fake)
    with pytest.raises(qa.KbQaError, match="空内容"):
        qa.chat("问题")


def test_invalid_provider_raises(monkeypatch):
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "nope")
    with pytest.raises(qa.KbQaError, match="KB_QA_LLM_PROVIDER"):
        qa.chat("问题")


def test_ask_single_uses_router_and_metadata(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "router")
    monkeypatch.setattr(qa, "_router_module", lambda: fake)
    result = qa.ask("M9 覆铜板？", mode="single", search_fn=fake_search, guard=False)
    assert result["answer"].startswith("ROUTER")
    assert result["llm"]["endpoint"].startswith("router(")
    assert result["fallback"] is False


def test_ask_single_router_failure_falls_back_to_rule(monkeypatch):
    fake = FakeRouter(ok=False, attempts=[{"tier": "t1", "status": 500}])
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "router")
    monkeypatch.setattr(qa, "_router_module", lambda: fake)
    result = qa.ask("M9 覆铜板？", mode="single", search_fn=fake_search, guard=False)
    assert result["fallback"] is True
    assert "规则降级" in result["answer"]


def test_invalid_provider_fails_fast(monkeypatch):
    """审计回归：KB_QA_LLM_PROVIDER 拼错必须在 ask 入口报错，不能静默回退规则答案。"""
    monkeypatch.setattr(qa, "DEFAULT_LLM_PROVIDER", "openai")
    with pytest.raises(qa.KbQaError) as exc:
        qa.ask("问题", search_fn=fake_search)
    assert "KB_QA_LLM_PROVIDER" in str(exc.value)
