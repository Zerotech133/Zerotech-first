# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def test_cmd_check_allowlist(tmp_path):
    err = W._run_checks(tmp_path, [{"type": "cmd", "cmd": ["powershell.exe", "-Command", "echo x"]}])
    assert err.startswith("check_failed:cmd_not_allowed")
