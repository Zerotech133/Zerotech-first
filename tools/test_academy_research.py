# -*- coding: utf-8 -*-
"""研究院课题与 Worker 阶段绑定测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import academy_research as research


TOPIC = {
    "topic_id": "AC-TEST-1",
    "status": "intake",
    "history": [{"stage": "intake"}],
}


def worker(**overrides):
    value = {
        "topic_id": "AC-TEST-1",
        "stage": "intake",
        "execution_status": "done",
        "acceptance_result": {"passed": True},
        "meeting_ref": "meeting:test-1",
        "trace_id": "trace:test-1",
        "output_ref": {"path": "output.md", "sha256": "a" * 64, "size": 10},
    }
    value.update(overrides)
    return value


def test_worker_pass_only_proposes_next_stage_until_human_approval():
    topic = dict(TOPIC)
    result = research.worker_stage_transition(topic, worker())

    assert result["ok"] is True
    assert result["advanced"] is False
    assert result["next_stage"] == "hypothesis"
    assert result["references"]["meeting_ref"] == "meeting:test-1"
    assert topic["status"] == "intake"
    assert result["topic"]["status"] == "intake"


def test_approved_worker_advances_exactly_one_stage_and_records_refs():
    result = research.worker_stage_transition(TOPIC, worker(), approved=True, by="D1", now="2026-10-17T10:00:00")

    assert result["ok"] is True
    assert result["advanced"] is True
    assert result["topic"]["status"] == "hypothesis"
    history = result["topic"]["history"][-1]
    assert history["stage"] == "hypothesis"
    assert history["by"] == "D1"
    assert history["worker_refs"]["trace_id"] == "trace:test-1"
    assert history["worker_refs"]["output_ref"]["path"] == "output.md"


def test_failed_or_review_worker_does_not_advance():
    for changes in (
        {"acceptance_result": {"passed": False}},
        {"execution_status": "review"},
        {"execution_status": "failed"},
    ):
        result = research.worker_stage_transition(TOPIC, worker(**changes), approved=True)
        assert result["ok"] is False
        assert result["advanced"] is False
        assert result["topic"]["status"] == "intake"


def test_binding_rejects_wrong_stage_or_missing_reference():
    wrong_stage = research.worker_stage_transition(TOPIC, worker(stage="hypothesis"), approved=True)
    missing_ref = research.worker_stage_transition(TOPIC, worker(output_ref=None), approved=True)

    assert wrong_stage["ok"] is False
    assert "当前阶段工单" in wrong_stage["err"]
    assert missing_ref["ok"] is False
    assert "meeting_ref" in missing_ref["err"]


def test_persisted_entry_point_requires_approval_and_writes_only_after_it(tmp_path, monkeypatch):
    topics = tmp_path / "research_topics.json"
    topics.write_text(json.dumps([TOPIC], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(research, "TOPICS", topics)

    proposal = research.advance_topic_from_worker("AC-TEST-1", worker())
    assert proposal["ok"] is True
    assert proposal["advanced"] is False
    assert json.loads(topics.read_text(encoding="utf-8"))[0]["status"] == "intake"

    approved = research.advance_topic_from_worker("AC-TEST-1", worker(), approved=True, by="D1")
    assert approved["advanced"] is True
    assert json.loads(topics.read_text(encoding="utf-8"))[0]["status"] == "hypothesis"
