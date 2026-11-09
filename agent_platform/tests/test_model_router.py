"""模型路由测试（WO-ZHAOYUN-CAP2）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "model_router.py"
_spec = importlib.util.spec_from_file_location("model_router", str(_TOOL))
MR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MR)


def test_route_defaults_and_prefer():
    plan = MR.route("governance")
    assert plan[0]["model"] == "deepseek/deepseek-flash"
    assert [p["rank"] for p in plan] == [1, 2, 3]
    pref = MR.route("governance", prefer=["ollama/qwen3.5:9b"])
    assert pref[0]["model"] == "ollama/qwen3.5:9b"
    deep = MR.route("deep-reasoning")
    assert deep[0]["model"] == "deepseek/deepseek-v4-pro"
    glm = MR.route("glm-quota")
    assert any(
        p["model"].startswith("glm/") and p["risk"] == "quota_429_risk" for p in glm
    )


def test_invoke_fallback_and_usage_log(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(MR, "USAGE_LOG", log)
    # 隔离 trace DB：避免测试 span 污染生产 traces.sqlite
    monkeypatch.setattr(MR, "LLM_TRACE_DB", str(tmp_path / "traces.sqlite"))
    calls = []

    def fake_call(model, messages, max_tokens=256):
        calls.append(model)
        if model.startswith("deepseek/"):
            raise RuntimeError("429 quota")
        return "本地回复"

    res = MR.invoke([{"role": "user", "content": "hi"}], adapters={"call": fake_call})
    assert res["ok"] is True and res["model"] == "ollama/qwen3.5:9b"
    assert len(calls) == 3  # flash 429 -> v4 429 -> local 成功
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3
    summary = MR.usage_summary()
    assert summary["count"] == 3


def test_invoke_all_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(MR, "USAGE_LOG", tmp_path / "u.jsonl")
    monkeypatch.setattr(MR, "LLM_TRACE_DB", str(tmp_path / "traces.sqlite"))

    def boom(*a, **k):
        raise RuntimeError("down")

    res = MR.invoke([{"role": "user", "content": "x"}], adapters={"call": boom})
    assert res["ok"] is False and res["used"] is False
    assert len(res["attempts"]) == 3


def test_empty_reply_treated_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(MR, "USAGE_LOG", tmp_path / "u.jsonl")
    monkeypatch.setattr(MR, "LLM_TRACE_DB", str(tmp_path / "traces.sqlite"))

    def empty(model, messages, max_tokens=256):
        return ""

    res = MR.invoke([{"role": "user", "content": "x"}], adapters={"call": empty})
    assert res["ok"] is False and len(res["attempts"]) == 3
