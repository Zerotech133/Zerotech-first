# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def test_grounded_command_allowlist():
    allowed = ["python -X utf8 tools/mission_worker.py run --mission M --task T1"]
    ok = W._validate_commands([("docs/a.md", "运行：`python -X utf8 tools/mission_worker.py run --mission M --task T1`")], allowed)
    assert ok == ""
    bad = W._validate_commands([("docs/a.md", "运行：`/start-auto-ordering`")], allowed)
    assert bad.startswith("ungrounded_command")


def test_command_is_grounded_edge():
    assert W._command_is_grounded("普通文字", [])
    assert W._command_is_grounded("python tools/x.py", ["python tools/x.py"])
    assert not W._command_is_grounded("/hello-world", [])


def test_run_checks(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("hello worker", encoding="utf-8")
    assert W._run_checks(tmp_path, [{"type": "file_exists", "path": "a.md"}]) == ""
    assert W._run_checks(tmp_path, [{"type": "file_contains", "path": "a.md", "text": "hello"}]) == ""
    assert W._run_checks(tmp_path, [{"type": "file_contains", "path": "a.md", "text": "nope"}]).startswith("check_failed")
    assert W._run_checks(tmp_path, [{"type": "file_min_chars", "path": "a.md", "min": 100}]).startswith("check_failed")
