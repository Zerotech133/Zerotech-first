# -*- coding: utf-8 -*-
"""llm_router 三级降级 + 成本监控测试（task-183，全部 mock，离线不联网）。

覆盖：① tier1 正常+成本落账；② 超时降级 tier2；③ 429/500 降级 ollama；
④ 全失败；⑤ 预算告警；⑥ 密钥脱敏；⑦ rag_lite ask 集成（router/回滚）；
⑧ env 模型覆盖。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

spec = importlib.util.spec_from_file_location("llm_router_under_test",
                                              ROOT / "tools" / "kb_corpus" / "llm_router.py")
lr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lr
spec.loader.exec_module(lr)

BAILIAN_OK = json.dumps({"choices": [{"message": {"content": "云端答案"}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
OLLAMA_OK = json.dumps({"message": {"content": "本地答案"},
                        "prompt_eval_count": 10, "eval_count": 20})


def _log(tmp_path):
    return str(tmp_path / "cost.jsonl")


def test_tier1_success_and_cost_log(tmp_path):
    transport = lambda url, headers, payload, timeout: (200, BAILIAN_OK)
    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["ok"] is True and r["tier"] == 1 and r["provider"] == lr.TIER_V4D
    assert r["text"] == "云端答案"
    assert r["cost_cny"] > 0 and r["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}
    rows = lr.load_cost_log(Path(_log(tmp_path)))
    assert len(rows) == 1 and rows[0]["provider"] == lr.TIER_V4D
    assert rows[0]["total_tokens"] == 150
    assert r["budget_warning"] is False


def test_timeout_degrades_to_tier2(tmp_path):
    calls = {"n": 0}

    def transport(url, headers, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timeout")
        return 200, BAILIAN_OK

    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["ok"] is True and r["tier"] == 2 and r["provider"] == lr.TIER_V2
    assert len(r["attempts"]) == 2
    assert "TimeoutError" in r["attempts"][0]["error"]


def test_429_and_500_degrade_to_ollama(tmp_path):
    seq = [(429, "rate limited"), (500, "server error"), (200, OLLAMA_OK)]

    def transport(url, headers, payload, timeout):
        return seq.pop(0)

    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["ok"] is True and r["tier"] == 3 and r["provider"] == lr.TIER_OLLAMA
    assert r["text"] == "本地答案" and r["cost_cny"] == 0.0
    assert [a["status"] for a in r["attempts"]] == [429, 500, 200]


def test_all_tiers_fail(tmp_path):
    def transport(url, headers, payload, timeout):
        raise urllib.error.URLError("network down")

    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["ok"] is False and r["error"] == "all_tiers_failed"
    assert len(r["attempts"]) == 3 and r["text"] == ""


def test_budget_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_BUDGET_CNY", "0.0001")
    transport = lambda url, headers, payload, timeout: (200, BAILIAN_OK)
    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["ok"] is True
    assert r["daily_cost_cny"] > r["budget_cny"]
    assert r["budget_warning"] is True


def test_secret_redaction(tmp_path):
    def transport(url, headers, payload, timeout):
        raise RuntimeError("auth failed for sk-ABC123xyz789")

    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    err = r["attempts"][0]["error"]
    assert "sk-ABC123xyz789" not in err and "sk-****" in err


def test_rag_lite_ask_integration(tmp_path, monkeypatch):
    import rag_lite
    hits = [{"doc_id": "d1", "video_id": "v1", "channel_id": "c1", "kind": "video",
             "title": "标题", "upload_date": "2026-01-01", "score": 0.9, "text": "资料"}]
    monkeypatch.setattr(rag_lite, "retrieve", lambda q, k=5: hits)

    class FakeRouter:
        @staticmethod
        def generate(prompt, **kw):
            assert "只依据下面编号资料回答" in prompt     # strict prompt 默认
            return {"ok": True, "text": "路由答案 [S1]", "tier": 1, "provider": "bailian_v4d",
                    "model": "m", "cost_cny": 0.01, "daily_cost_cny": 0.01, "budget_cny": 20,
                    "budget_warning": False, "attempts": []}

    monkeypatch.setattr(rag_lite, "_router_module", lambda: FakeRouter)
    monkeypatch.delenv("RAG_ROUTER_DISABLE", raising=False)
    out = rag_lite.ask("如何评估策略稳健性？")
    assert out["answer"] == "路由答案 [S1]"
    assert out["router"]["tier"] == 1 and out["sources"][0]["sid"] == "S1"
    # 回滚：RAG_ROUTER_DISABLE=1 → 旧本地 chat
    monkeypatch.setenv("RAG_ROUTER_DISABLE", "1")
    monkeypatch.setattr(rag_lite, "chat", lambda prompt, **kw: "旧本地答案")
    out2 = rag_lite.ask("问题")
    assert out2["answer"] == "旧本地答案"
    assert out2["router"]["provider"] == "ollama_legacy"


def test_env_model_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODEL_V4D", "custom-model-x")
    transport = lambda url, headers, payload, timeout: (200, BAILIAN_OK)
    r = lr.generate("问题", transport=transport, cost_log=_log(tmp_path))
    assert r["model"] == "custom-model-x"


def test_default_cost_log_is_daily(tmp_path, monkeypatch):
    """默认成本日志必须按本地日期分文件，而不是写死某一天。"""
    from datetime import datetime
    monkeypatch.delenv("LLM_COST_LOG", raising=False)
    monkeypatch.setattr(lr, "REPORTS", tmp_path)
    p = lr._cost_log_path(datetime(2026, 11, 5, 9, 0).astimezone())
    assert p == tmp_path / "llm_cost_20261105.jsonl"
    assert p != lr._cost_log_path(datetime(2026, 11, 6, 9, 0).astimezone())
    # 显式 env 覆盖保持不变
    monkeypatch.setenv("LLM_COST_LOG", str(tmp_path / "custom.jsonl"))
    assert lr._cost_log_path() == tmp_path / "custom.jsonl"
