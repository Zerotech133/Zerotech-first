# -*- coding: utf-8 -*-
"""质量闸作用域单测（2026-10-30 · 分支级闸）。

目标：证明闸门脚本解析到任务 worktree，而不是仓库根（master）。
防回归：旧实现固定 `REPO/tools/test_gate.ps1`，master 有失败测试会锁死所有合并。
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location("devtask_branch_gate_t", TOOLS / "devtask.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_gate_script_resolves_to_worktree(tmp_path, monkeypatch):
    wt = tmp_path / ".worktrees" / "T-GATE"
    (wt / "tools").mkdir(parents=True)
    script = wt / "tools" / "test_gate.ps1"
    script.write_text("# gate\n", encoding="utf-8")
    monkeypatch.setattr(m, "WT_DIR", str(tmp_path / ".worktrees"))
    assert m._gate_script_for("T-GATE") == str(script)


def test_gate_script_missing_worktree_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "WT_DIR", str(tmp_path / ".worktrees"))
    assert m._gate_script_for("T-NOPE") == ""


def test_gate_script_missing_file_returns_empty(tmp_path, monkeypatch):
    wt = tmp_path / ".worktrees" / "T-GATE"
    (wt / "tools").mkdir(parents=True)
    monkeypatch.setattr(m, "WT_DIR", str(tmp_path / ".worktrees"))
    assert m._gate_script_for("T-GATE") == ""


def test_quality_gate_uses_worktree_script():
    src = (TOOLS / "devtask.py").read_text(encoding="utf-8")
    assert "gate_script = _gate_script_for(tid)" in src
    assert '"-File", gate_script' in src
    assert "在任务 worktree 跑 test_gate" in src
    # 旧行为锚点必须消失
    assert 'os.path.join(REPO, "tools", "test_gate.ps1")' not in src
