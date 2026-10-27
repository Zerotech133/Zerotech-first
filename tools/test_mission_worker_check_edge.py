# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def test_empty_contains_text_must_fail(tmp_path):
    (tmp_path / "a.md").write_text("abc", encoding="utf-8")
    err = W._run_checks(tmp_path, [{"type": "file_contains", "path": "a.md", "text": ""}])
    assert err.startswith("check_failed:file_contains_empty")
