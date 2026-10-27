# -*- coding: utf-8 -*-
"""LLM usage/成本留证测试（WO-TRACE-USAGE）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WT))
sys.path.insert(0, str(_WT / "agent_platform"))

from observability import trace as TR  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MR = _load("model_router", _WT / "agent_platform" / "tools" / "model_router.py")


def test_usage_tokens_provider_and_estimate():
    tok = MR._usage_tokens(
        {"prompt_tokens": 100, "completion_tokens": 50},
        [{"role": "user", "content": "x"}],
        "hello",
    )
    assert tok == {"input": 100, "output": 50, "source": "provider", "total": 150}
    est = MR._usage_tokens({}, [{"role": "user", "content": "abcd" * 10}], "abcdefgh")
    assert est["source"] == "estimated" and est["input"] >= 1 and est["output"] >= 1


def test_invoke_records_usage_span(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(TR, "DB", db)

    def fake(model, messages, max_tokens=256):
        return {
            "text": "回复内容" * 10,
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }

    res = MR.invoke([{"role": "user", "content": "你好"}], adapters={"call": fake})
    assert res["ok"] and res["usage"]["source"] == "provider"
    assert res["usage"]["input"] == 120 and res["usage"]["output"] == 30
    tr = TR.list_traces()
    assert tr["ok"] and tr["items"], tr
    tid = tr["items"][0]["trace_id"]
    detail = TR.get_trace(tid)
    span = detail["spans"][0]
    assert span["name"] == "llm.call"
    assert span["attrs"]["gen_ai.usage.input_tokens"] == 120
    assert span["attrs"]["gen_ai.usage.output_tokens"] == 30
    assert span["cost"] > 0
