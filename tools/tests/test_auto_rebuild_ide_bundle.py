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


# ---------------------------------------------------------------- W5 P1-6 Phase1
# 2026-10-31：bundle 只在 merge 触及 agent 源（org/team_/hr/prompts）时重烘+提交；
# 其余 merge 跳过；支持 DEVTOOL_IDE_BUNDLE_DRY_RUN=1 只打印决策不落盘。

def test_bundle_sources_changed_matrix():
    assert m._bundle_sources_changed(["agent_platform/registry/org.json"])
    assert m._bundle_sources_changed(["agent_platform/registry/team_invest.json"])
    assert m._bundle_sources_changed(["agent_platform/hr/DEV1/resume.yaml"])
    assert m._bundle_sources_changed(["agent_platform/prompts/chaplain.md"])
    assert m._bundle_sources_changed(["./agent_platform/registry/org.json"])
    assert m._bundle_sources_changed([".\\agent_platform\\hr\\DEV1\\resume.yaml"])
    assert not m._bundle_sources_changed([])
    assert not m._bundle_sources_changed(None)
    assert not m._bundle_sources_changed([
        "docs/plans/x.md",
        "portal/static/a.js",
        "agent_platform/tests/test_x.py",
        "agent_platform/promptsX/a.md",          # 前缀不得误命中
        "agent_platform/registry/org.json.bak",  # 精确文件/前缀纪律
    ])


def test_merge_changed_files_best_effort(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("merge_gated")
    fake.branch_changed_files = lambda tid: ["agent_platform/prompts/a.md"]
    monkeypatch.setitem(sys.modules, "merge_gated", fake)
    assert m._merge_changed_files("T1") == ["agent_platform/prompts/a.md"]
    fake.branch_changed_files = lambda tid: (_ for _ in ()).throw(RuntimeError("boom"))
    assert m._merge_changed_files("T1") == []


def _fake_run_factory(calls, status_out=""):
    import types

    def fake(cmd, **kwargs):
        calls.append(list(cmd))
        r = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "status" in cmd and "--porcelain" in cmd:
            r.stdout = status_out
        return r

    return fake


def test_auto_rebuild_skips_when_sources_unchanged(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("源未变时不应调用任何子进程（不重建/不提交）")

    monkeypatch.setattr(m.subprocess, "run", boom)
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["docs/plans/x.md", "tools/devtask.py"])
    out = capsys.readouterr().out
    assert "源未变" in out


def test_auto_rebuild_dry_run_when_sources_changed(monkeypatch, capsys):
    monkeypatch.setenv("DEVTOOL_IDE_BUNDLE_DRY_RUN", "1")

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应调用子进程（不重建/不提交）")

    monkeypatch.setattr(m.subprocess, "run", boom)
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["agent_platform/registry/org.json"])
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_auto_rebuild_unchanged_bundle_skips_commit(monkeypatch, capsys):
    monkeypatch.delenv("DEVTOOL_IDE_BUNDLE_DRY_RUN", raising=False)
    calls = []
    monkeypatch.setattr(m.subprocess, "run", _fake_run_factory(calls, status_out=""))
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["agent_platform/prompts/a.md"])
    assert any("build_agent_ide_bundle.py" in " ".join(c) for c in calls), calls
    assert not any("commit" in c for c in calls), calls
    assert "已是最新" in capsys.readouterr().out


def test_auto_rebuild_commits_when_bundle_changed(monkeypatch, capsys):
    monkeypatch.delenv("DEVTOOL_IDE_BUNDLE_DRY_RUN", raising=False)
    calls = []
    monkeypatch.setattr(
        m.subprocess, "run",
        _fake_run_factory(calls, status_out=" M portal/agent_ide_bundle.json\n"))
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["agent_platform/hr/DEV1/resume.yaml"])
    assert any("add" in c for c in calls), calls
    commits = [c for c in calls if "commit" in c]
    assert commits, calls
    assert commits[0][-2:] == ["--", "portal/agent_ide_bundle.json"]
