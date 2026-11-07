# -*- coding: utf-8 -*-
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from legion_command import ide_worktree as W  # noqa: E402


def _git_has_worktree():
    """git worktree 子命令自 2.5 起才有；CentOS 7 自带 1.8.3.1 无此能力。"""
    try:
        p = subprocess.run(["git", "worktree", "list"],
                           capture_output=True, text=True, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# 2026-11-07 K-RELEASE-20261106：这是**环境能力守卫**，不是放宽硬门。
# 服务器 10.205.0.118 的 git 1.8.3.1 实测 `git: 'worktree' is not a git command`；
# Legion IDE（8103）本机专属、服务器上从未部署，故该面在 Linux 验收下记 SKIP。
# 解除条件：服务器 git 升级到 ≥2.5，或本模块改为不依赖 worktree 的实现。
pytestmark = pytest.mark.skipif(
    not _git_has_worktree(),
    reason="git < 2.5 无 worktree 子命令（CentOS 7 git 1.8.3.1）；Legion IDE worktree 能力仅开发机启用")


def _git(cwd, *args):
    # 2026-11-07 K-RELEASE-20261106：CentOS 7 自带 git 1.8.3.1 不支持 `git -C`
    # （不报"不支持"，只打 usage 并返回非 0，实测 Unknown option: -C）。
    # 改用 cwd= 传参，Windows/Linux 通用；与 13_verify.sh GITPORT 判例同口径。
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
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
