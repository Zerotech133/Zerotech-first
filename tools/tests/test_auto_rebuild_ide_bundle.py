# -*- coding: utf-8 -*-
"""IDE bundle 自动重建单测（2026-10-29 并发修复 → 2026-10-31 Phase2 去 tracked）。

历史：旧实现 merge 后用 `git commit -m ...` 提交整个暂存区（并行 index 含其它改动
时会把刚合入文件一起抹掉，WO-D1-OUTCOME），后改为 pathspec 限定提交。
2026-10-31 K-ORG-20261031-BUNDLEP2B（Phase2）：bundle 生成物去 tracked + ignored；
`_auto_rebuild_ide_bundle` 改为「重建 + 刷新 _vault 部署副本、不 git add/commit」。
本组锁死：Phase1 源判定矩阵不变；重建路径绝不产生 git 提交；部署副本刷新幂等。
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


# ---------------------------------------------------------------- W5 P1-6 Phase2
# K-ORG-20261031-BUNDLEP2B（2026-10-31）：bundle 生成物 untracked+ignored；
# 旧 `_ide_bundle_commit_args`（pathspec 收尾提交）已移除，重建只刷新 _vault 副本。

def test_commit_helper_removed():
    assert not hasattr(m, "_ide_bundle_commit_args")
    assert hasattr(m, "_refresh_bundle_deploy_copy")
    assert hasattr(m, "_bundle_deploy_target")


def test_refresh_deploy_copy_and_idempotent(tmp_path):
    repo = tmp_path / "repo"
    (repo / "portal").mkdir(parents=True)
    src = repo / "portal" / "agent_ide_bundle.json"
    src.write_bytes(b'{"v": 1}\n')
    deploy = tmp_path / "deploy" / "agent_ide_bundle.json"
    r1 = m._refresh_bundle_deploy_copy(repo=str(repo), deploy=str(deploy))
    assert r1["ok"] is True and r1["bytes"] == len(b'{"v": 1}\n')
    assert deploy.read_bytes() == src.read_bytes()
    r2 = m._refresh_bundle_deploy_copy(repo=str(repo), deploy=str(deploy))
    assert r2["ok"] is True and deploy.read_bytes() == src.read_bytes()  # 幂等

    r3 = m._refresh_bundle_deploy_copy(repo=str(tmp_path / "empty"), deploy=str(deploy))
    assert r3["ok"] is False and r3["error"] == "src_missing"


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


def _tmp_repo_with_bundle(tmp_path):
    repo = tmp_path / "repo"
    (repo / "portal").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "tools" / "build_agent_ide_bundle.py").write_text("# fake\n", encoding="utf-8")
    (repo / "portal" / "agent_ide_bundle.json").write_bytes(b'{"v": 2}\n')
    deploy = tmp_path / "deploy" / "agent_ide_bundle.json"
    return repo, deploy


def test_auto_rebuild_refreshes_deploy_copy_without_commit(monkeypatch, tmp_path, capsys):
    """Phase2：源变 → 重建 + 刷新部署副本；绝不 git add/commit。"""
    monkeypatch.delenv("DEVTOOL_IDE_BUNDLE_DRY_RUN", raising=False)
    repo, deploy = _tmp_repo_with_bundle(tmp_path)
    monkeypatch.setattr(m, "REPO", str(repo))
    monkeypatch.setenv("DEVTOOL_IDE_BUNDLE_DEPLOY", str(deploy))
    calls = []
    monkeypatch.setattr(m.subprocess, "run", _fake_run_factory(calls))
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["agent_platform/registry/org.json"])
    assert any("build_agent_ide_bundle.py" in " ".join(c) for c in calls), calls
    assert not any(("commit" in c) or ("add" in c) for c in calls), calls
    assert deploy.is_file() and deploy.read_bytes() == b'{"v": 2}\n'
    out = capsys.readouterr().out
    assert "不产生提交" in out and "部署副本已刷新" in out


def test_auto_rebuild_build_failure_no_deploy_no_commit(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEVTOOL_IDE_BUNDLE_DRY_RUN", raising=False)
    repo, deploy = _tmp_repo_with_bundle(tmp_path)
    monkeypatch.setattr(m, "REPO", str(repo))
    monkeypatch.setenv("DEVTOOL_IDE_BUNDLE_DEPLOY", str(deploy))

    import types
    calls = []

    def fake(cmd, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(m.subprocess, "run", fake)
    m._auto_rebuild_ide_bundle("WO-X", changed_files=["agent_platform/prompts/a.md"])
    assert not deploy.exists()                     # 构建失败不刷新副本
    assert not any(("commit" in c) or ("add" in c) for c in calls), calls
    assert "重建失败" in capsys.readouterr().out
