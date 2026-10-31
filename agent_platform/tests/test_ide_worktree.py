# -*- coding: utf-8 -*-
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from legion_command import ide_worktree as W  # noqa: E402


def _git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    tasks = repo / ".worktrees" / "tasks.json"
    tasks.parent.mkdir()
    tasks.write_text(json.dumps({"T-1": {"status": "doing", "title": "test"}}), encoding="utf-8")
    monkeypatch.setattr(W, "REPO", repo)
    monkeypatch.setattr(W, "TASKS_FILE", tasks)
    monkeypatch.setattr(W, "WORKTREE_ROOT", tmp_path / "wt")
    return repo


def test_auto_worktree_diff_commit(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    ready = W.ensure_worktree("T-1")
    assert ready["ok"] and Path(ready["worktree"]).is_dir()
    wt = Path(ready["worktree"])
    (wt / "new.py").write_text("print('oh')\n", encoding="utf-8")
    d = W.diff("T-1")
    assert d["ok"] and d["has_changes"] and "new.py" in d["files"]
    c = W.commit("T-1", "test commit")
    assert c["ok"] and c["commit"]
    assert (wt / "new.py").is_file()
    log = _git(wt, "log", "-1", "--format=%s")
    assert "test commit" in log


def test_auto_worktree_ignores_git_env(tmp_path, monkeypatch):
    """全套件并发时 GIT_DIR/GIT_WORK_TREE 泄漏不得串仓（2026-11-01 修复）。"""
    _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "bogus-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "bogus-work-tree"))
    ready = W.ensure_worktree("T-1")
    assert ready["ok"] and Path(ready["worktree"]).is_dir()
