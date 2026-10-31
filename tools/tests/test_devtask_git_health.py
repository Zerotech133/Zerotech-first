# -*- coding: utf-8 -*-
"""P140：devtask 启动级 core.bare 自愈守卫。"""
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def test_git_health_autofix_recovers_bare_true(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "core.bare", "true"], check=True)
    import devtask
    assert devtask._git_health_autofix(repo) is True
    r = subprocess.run(["git", "-C", str(repo), "config", "core.bare"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "false"


def test_git_health_autofix_healthy_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    import devtask
    assert devtask._git_health_autofix(repo) is True


def test_git_health_autofix_no_git(tmp_path):
    import devtask
    assert devtask._git_health_autofix(tmp_path / "nope") is True
