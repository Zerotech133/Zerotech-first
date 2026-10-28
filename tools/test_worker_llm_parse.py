# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def test_extract_worker_json_fenced():
    text = '说明\n```json\n{"files":[{"path":"a.md","content":"x"}]}\n```'
    assert W._extract_worker_json(text)["files"][0]["path"] == "a.md"


def test_extract_worker_json_rejects_schema():
    try:
        W._extract_worker_json('{"foo": 1}')
    except ValueError as e:
        assert "schema" in str(e)
    else:
        raise AssertionError("should reject")
