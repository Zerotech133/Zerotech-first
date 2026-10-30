# -*- coding: utf-8 -*-
"""git_worktree_guard 单测（2026-10-31 P110）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import git_worktree_guard as g  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _init(repo: Path):
    _git(repo, "init")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@local")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")


def test_normal_repo_not_bare(tmp_path):
    _init(tmp_path)
    assert g.is_bare(tmp_path) is False
    assert g.ensure_worktree(tmp_path, log=lambda *_: None) is True


def test_bare_flip_detected_and_repaired(tmp_path):
    _init(tmp_path)
    _git(tmp_path, "config", "core.bare", "true")
    assert g.is_bare(tmp_path) is True
    logs = []
    ok = g.ensure_worktree(tmp_path, log=logs.append)
    assert ok is True
    assert g.is_bare(tmp_path) is False
    assert any("bare" in ln for ln in logs)


def test_check_mode_returns_status(tmp_path, capsys):
    _init(tmp_path)
    assert g.main(["--repo", str(tmp_path), "--check"]) == 0
    _git(tmp_path, "config", "core.bare", "true")
    assert g.main(["--repo", str(tmp_path), "--check"]) == 1
