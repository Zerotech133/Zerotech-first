# -*- coding: utf-8 -*-
"""seat_write_guard 检测层单测（TFGUARD）。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seat_write_guard as G  # noqa: E402


def test_allow_paths():
    for p in ("deliverables/x.md", "work/y.json", "docs/plans/a.md",
              "agent_platform/registry/org.json"):
        assert G.check_path(p)["allowed"] is True, p


def test_deny_paths():
    for p in ("quant_backtest/data/x.json", "agent_platform/bridge_inbox/inbox/a.json",
              "portal/data/council.json", "portal/work/secret.txt"):
        assert G.check_path(p)["allowed"] is False, p


def test_exception():
    assert G.check_path("portal/data/task_exit_audit/task_exit_summary.json")["allowed"] is True


def test_home_and_unknown():
    assert G.check_path(str(G.HOME / "memories" / "WORKING_MEMORY.md"))["allowed"] is True
    assert G.check_path("random_dir/a.txt")["allowed"] is False


def test_exec_confirm():
    assert G.check_exec("taskkill /PID 1 /F")["requires_confirm"] is True
    assert G.check_exec("schtasks /create /tn x")["requires_confirm"] is True
    assert G.check_exec("python -X utf8 tools/devtask.py list")["requires_confirm"] is False


def test_cli_codes(capsys):
    assert G.main(["--check", "deliverables/x.md", "--json"]) == 0
    assert G.main(["--check", "quant_backtest/data/x.json", "--json"]) == 1
    assert G.main(["--exec", "shutdown /r", "--json"]) == 3
