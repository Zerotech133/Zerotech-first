# -*- coding: utf-8 -*-
"""LLM-as-judge 测试（WO-EVAL-JUDGE）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "judge", str(_WT / "agent_platform" / "evals" / "judge.py")
)
J = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(J)


def _sandbox(tmp_path, monkeypatch, limit="2"):
    monkeypatch.setattr(J, "OUT", tmp_path / "evals")
    monkeypatch.setattr(J, "BUDGET", tmp_path / "evals" / "b.json")
    monkeypatch.setattr(J, "LATEST", tmp_path / "evals" / "latest.json")
    monkeypatch.setenv("ZHAOYUN_JUDGE_DAILY_LIMIT", limit)


def _adapter(reply):
    def call(model, messages, max_tokens=256):
        return {"text": reply, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return {"call": call}


def test_judge_ok_parses_json(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    rep = J.judge_text(
        "治理建议正文",
        adapters=_adapter(
            '{"score": 4, "rationale": "有证据但缺回滚", "flags": ["缺回滚"]}'
        ),
    )
    assert rep["ok"] and rep["score"] == 4 and rep["source"] == "judge"
    assert rep["budget"]["remaining"] == 1
    assert rep["usage"]["input"] == 10


def test_judge_budget_exhausted(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, limit="1")
    a = _adapter('{"score": 5, "rationale": "ok"}')
    assert J.judge_text("x", adapters=a)["ok"] is True
    rep = J.judge_text("y", adapters=a)
    assert rep["ok"] is False and rep["error"] == "budget_exceeded"


def test_judge_no_fake_score_on_bad_reply(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    rep = J.judge_text("x", adapters=_adapter("我觉得很好"))  # 非 JSON
    assert rep["ok"] is False and rep["source"] == "unparseable"
    rep2 = J.judge_text(
        "x",
        adapters={"call": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))},
    )
    assert rep2["ok"] is False and rep2["source"] == "unavailable"


def test_judge_report_latest(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    d = tmp_path / "gov"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"advice": "建议 A"}), encoding="utf-8")
    monkeypatch.setattr(J, "PLATFORM", tmp_path)
    import json as _json

    assert _json is not None
    # governance_ai 路径基于 PLATFORM；临时写入 tmp/data/governance_ai
    (tmp_path / "data" / "governance_ai").mkdir(parents=True)
    (tmp_path / "data" / "governance_ai" / "a.json").write_text(
        json.dumps({"advice": "建议 A"}), encoding="utf-8"
    )
    rep = J.judge_latest(
        limit=1, adapters=_adapter('{"score": 3, "rationale": "一般"}')
    )
    assert rep["count"] == 1 and rep["items"][0]["result"]["score"] == 3
    assert J.latest()["count"] == 1


def test_judge_salvages_truncated_json(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    reply = '{"score": 4, "rationale": "边界清晰但缺引用", "flags": ["缺引用"'
    rep = J.judge_text("x", adapters=_adapter(reply))
    assert rep["ok"] is True and rep["score"] == 4
    assert rep["source"] == "judge" and rep["partial"] is True
