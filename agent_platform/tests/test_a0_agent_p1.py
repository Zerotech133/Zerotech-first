# -*- coding: utf-8 -*-
"""A0 权威 binding 与成对判读审计字段的纯本地回归测试。"""
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import a0_agent as a0  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "registry").mkdir()
    (tmp_path / "data" / "shadow").mkdir(parents=True)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "a0_compare.md").write_text("判读", encoding="utf-8")
    (tmp_path / "registry" / "org.json").write_text(json.dumps({
        "chief": {"agent_id": "A0", "llm_binding": "glm/old"},
        "departments": [{"dept": "chief_line", "agent_profiles": [
            {"agent_id": "A0", "llm_binding": "glm/new"}]}],
    }), encoding="utf-8")
    monkeypatch.setattr(a0, "BASE", tmp_path)
    monkeypatch.setattr(a0, "COMPARE_PERSONA", tmp_path / "prompts" / "a0_compare.md")
    cand, champ = tmp_path / "candidate.md", tmp_path / "champion.md"
    cand.write_text("候选 1", encoding="utf-8")
    champ.write_text("冠军 2", encoding="utf-8")
    args = Namespace(candidate=str(cand), champion=str(champ), evidence=None,
                     date="2026-10-26", target="trial", model=None, live=True)
    return tmp_path, args


def test_chief_model_precedence_and_fallback(sandbox):
    root, _ = sandbox
    org = root / "registry" / "org.json"
    assert a0.chief_model() == "glm/new"
    org.write_text(json.dumps({"chief": {"agent_id": "A0", "llm_binding": "glm/old"}}), encoding="utf-8")
    assert a0.chief_model() == "glm/old"
    org.write_text(json.dumps({"chief": {"agent_id": "A0", "llm_binding": ""}}), encoding="utf-8")
    assert a0.chief_model() == "ollama/qwen3.5:9b"
    org.write_text("[]", encoding="utf-8")
    assert a0.chief_model() == "ollama/qwen3.5:9b"
    org.unlink()
    assert a0.chief_model() == "ollama/qwen3.5:9b"


def _compare(sandbox, monkeypatch, verdict, status="ok", model=None):
    root, args = sandbox
    args.model = model
    calls = []

    def fake_route(persona, user, model, metadata=None):
        calls.append(model)
        metadata["actual"] = "ollama/qwen3.5:9b" if status == "degraded" else model
        return json.dumps(verdict, ensure_ascii=False), status

    monkeypatch.setattr(a0, "route_chat", fake_route)
    a0.run_compare(args)
    result = json.loads((root / "data" / "shadow" / "a0_compare_2026-10-26_trial.json").read_text(encoding="utf-8"))
    return result, calls


def test_compare_uses_authoritative_binding_and_accepts_valid(sandbox, monkeypatch):
    result, calls = _compare(sandbox, monkeypatch, a0.MOCK_COMPARE)
    assert calls == ["glm/new"]
    assert result["model"] == result["requested"] == result["actual"] == "glm/new"
    assert result["status"] == "ok" and result["degraded"] is False
    assert result["accepted"] is True and result["quality"]["ok"] is True


def test_compare_mock_is_never_accepted(sandbox, monkeypatch):
    root, args = sandbox
    args.live = False
    a0.run_compare(args)
    result = json.loads((root / "data" / "shadow" / "a0_compare_2026-10-26_trial.json").read_text(encoding="utf-8"))
    assert result["status"] == "mock"
    assert result["accepted"] is False


def test_compare_quality_failure_keeps_verdict_for_audit(sandbox, monkeypatch):
    invalid = dict(a0.MOCK_COMPARE, reasons=[])
    result, calls = _compare(sandbox, monkeypatch, invalid, model="glm/manual")
    assert calls == ["glm/manual"]
    assert result["quality"]["ok"] is False and result["accepted"] is False
    assert result["verdict"] == invalid


def test_compare_fallback_records_actual_and_rejects(sandbox, monkeypatch):
    result, _ = _compare(sandbox, monkeypatch, a0.MOCK_COMPARE, status="degraded")
    assert result["requested"] == "glm/new"
    assert result["actual"] == "ollama/qwen3.5:9b"
    assert result["degraded"] is True and result["status"] == "degraded"
    assert result["quality"]["ok"] is True and result["accepted"] is False


@pytest.mark.parametrize("verdict", [
    {"winner": "candidate", "confidence": "high", "reasons": [None, 3], "missing": ""},
    {"winner": "candidate", "confidence": "high", "reasons": [
        {"side": "candidate", "point": [1], "evidence": {"x": 1}},
        {"side": "champion", "point": "文字", "evidence": "文字"}], "missing": []},
    [1, 2],
    {"winner": "tie", "confidence": "low", "reasons": "bad", "missing": 10},
])
def test_valid_json_wrong_shape_is_rejected_without_crash(sandbox, monkeypatch, verdict):
    result, _ = _compare(sandbox, monkeypatch, verdict)
    assert result["quality"]["ok"] is False
    assert result["accepted"] is False
    assert result["verdict"] == verdict


def test_route_chat_retains_tuple_contract_and_tracks_real_fallback(monkeypatch):
    def fail(*args, **kwargs):
        raise a0.LLMError("offline")

    class FakeOllama:
        def chat(self, persona, user, model):
            assert model == "qwen3.5:9b"
            return "fallback", "normal"

    monkeypatch.setattr(a0, "glm_chat", fail)
    monkeypatch.setattr(a0, "OllamaLLM", FakeOllama)
    meta = {}
    assert a0.route_chat("s", "u", "glm/new", metadata=meta) == ("fallback", "degraded")
    assert meta["actual"] == "ollama/qwen3.5:9b"
    assert a0.route_chat("s", "u", "glm/new") == ("fallback", "degraded")
