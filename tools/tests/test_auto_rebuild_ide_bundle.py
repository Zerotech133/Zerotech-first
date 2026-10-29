# -*- coding: utf-8 -*-
"""IDE bundle 收尾提交并发修复单测（2026-10-29）。

背景：devtask merge 后 `_auto_rebuild_ide_bundle` 旧实现用
`git commit -m ...` 提交整个暂存区；并行会话的 index 若含其它改动/误删，
收尾提交会把刚合入文件一起抹掉（WO-D1-OUTCOME 实证）。
本组锁死：提交必须 pathspec 限定 `portal/agent_ide_bundle.json`。
"""
import importlib.util
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location("devtask_ide_t", TOOLS / "devtask.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def test_commit_args_pathspec():
    args = m._ide_bundle_commit_args("WO-X")
    assert args[-2:] == ["--", "portal/agent_ide_bundle.json"]
    assert any("after merge WO-X" in a for a in args)


def test_pathspec_commit_isolates_other_staged(tmp_path):
    repo = tmp_path
    _git(repo, "init")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@local")
    (repo / "portal").mkdir()
    bundle = repo / "portal" / "agent_ide_bundle.json"
    bundle.write_text('{"v": 1}\n', encoding="utf-8")
    other = repo / "other.txt"
    other.write_text("other\n", encoding="utf-8")
    _git(repo, "add", "portal/agent_ide_bundle.json", "other.txt")
    args = m._ide_bundle_commit_args("T1", repo=str(repo))
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    head = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert "portal/agent_ide_bundle.json" in head.stdout
    assert "other.txt" not in head.stdout
    # 其它暂存改动未被收尾提交吞掉：仍在 index 中
    staged = _git(repo, "diff", "--cached", "--name-only")
    assert "other.txt" in staged.stdout
