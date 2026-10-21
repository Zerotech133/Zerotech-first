# -*- coding: utf-8 -*-
"""summary 独立只读路径及健康状态。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import academy_research as research


def check_summary(result, *, complete, reason, topics):
    assert result["meta"] == {"complete": complete, "reason": reason}
    assert result["topics"] == topics
    assert result["n"] == len(topics)
    assert set(result["by_stage"]) == set(research.STAGES)
    assert result["by_stage"] == {
        stage: sum(row.get("status") == stage for row in topics)
        for stage in research.STAGES
    }


@pytest.mark.parametrize(
    ("payload", "reason", "topics", "complete"),
    [
        (b"[]", "empty", [], True),
        (b'[ {"status": "intake", "topic_id": "AC-1"} ]', "nonempty", [{"status": "intake", "topic_id": "AC-1"}], True),
        (b"{not json}", "invalid_json", [], False),
        (b'{"secret": "do not expose"}', "invalid_type", [], False),
        (b'[ {"status": "intake"}, 42 ]', "invalid_element", [], False),
        (b"\xff", "invalid_json", [], False),
    ],
)
def test_summary_reads_without_changing_file(tmp_path, monkeypatch, payload, reason, topics, complete):
    target = tmp_path / "research_topics.json"
    target.write_bytes(payload)
    monkeypatch.setattr(research, "TOPICS", target)
    monkeypatch.setattr(research, "load_topics", lambda *args, **kwargs: pytest.fail("summary 不得走旧读取路径"))

    result = research.summary()

    check_summary(result, complete=complete, reason=reason, topics=topics)
    assert target.read_bytes() == payload
    assert "do not expose" not in str(result)


def test_missing_file_does_not_create_parent_or_file(tmp_path, monkeypatch):
    target = tmp_path / "uncreated" / "research_topics.json"
    monkeypatch.setattr(research, "TOPICS", target)

    check_summary(research.summary(), complete=False, reason="missing", topics=[])
    assert not target.parent.exists()


def test_read_oserror_does_not_expose_exception_or_change_bytes(tmp_path, monkeypatch):
    target = tmp_path / "research_topics.json"
    payload = b"[]"
    target.write_bytes(payload)
    monkeypatch.setattr(research, "TOPICS", target)

    def fail_read(self, *args, **kwargs):
        assert self == target
        raise OSError("secret path and content")

    with monkeypatch.context() as patch:
        patch.setattr(type(target), "read_text", fail_read)
        result = research.summary()

    check_summary(result, complete=False, reason="read_error", topics=[])
    assert "secret path and content" not in str(result)
    assert target.read_bytes() == payload


def test_existing_directory_is_not_a_regular_file(tmp_path, monkeypatch):
    target = tmp_path / "research_topics.json"
    target.mkdir()
    monkeypatch.setattr(research, "TOPICS", target)

    check_summary(research.summary(), complete=False, reason="not_regular_file", topics=[])
    assert target.is_dir()
    assert list(target.iterdir()) == []
