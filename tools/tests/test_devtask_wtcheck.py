# -*- coding: utf-8 -*-
"""WO-DEVTASK-WTCHECK-20261031 回归测试：W2-3 worktree 校验（audit-worktrees / list --strict）。

覆盖：
  ① _worktree_violations 纯函数：全部合规→[]；目录缺/未注册/分支缺 各自命中；
     done/aborted 历史项不校验；worktree/branch 字段覆盖默认路径
  ② _resolve_main_repo：在任务 worktree 内调用回退到主仓（含 .worktrees）
  ③ _audit_worktrees 真实仓只读：返回 (violations, active)，违规项状态必为 active
  ④ CLI：audit-worktrees 人读/`--json`（rc 与 violations 一致）、未知参数报错；
     list 默认 rc=0（行为不变）、list --strict rc 与 audit 一致
  ⑤ 接线静态证据：main 分发含 audit-worktrees、docstring 有说明

只读：不创建/删除/移动任何 worktree；真实仓仅跑 git worktree list/branch 与读 tasks.json。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402

REPO = Path(devtask.__file__).resolve().parents[1]


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(Path(devtask.__file__)), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(cwd or REPO), timeout=180)


# ---------- ① 纯函数规则 ----------
def test_violations_all_ok(tmp_path):
    wt = tmp_path / ".worktrees" / "T1"
    wt.mkdir(parents=True)
    tasks = {"T1": {"status": "doing", "branch": "task/T1"}}
    reg = {os.path.normcase(os.path.abspath(str(wt)))}
    viol = devtask._worktree_violations(tasks, str(tmp_path / ".worktrees"), reg, {"task/T1"})
    assert viol == []


def test_violations_missing_dir(tmp_path):
    tasks = {"T1": {"status": "doing", "branch": "task/T1"}}
    viol = devtask._worktree_violations(tasks, str(tmp_path / ".worktrees"), set(), {"task/T1"})
    assert len(viol) == 1 and viol[0]["missing"] == ["dir", "registered"]


def test_violations_not_registered(tmp_path):
    wt = tmp_path / ".worktrees" / "T1"
    wt.mkdir(parents=True)
    tasks = {"T1": {"status": "doing", "branch": "task/T1"}}
    viol = devtask._worktree_violations(tasks, str(tmp_path / ".worktrees"), set(), {"task/T1"})
    assert viol[0]["missing"] == ["registered"]
    assert viol[0]["checks"] == {"dir": True, "registered": False, "branch": True}


def test_violations_missing_branch(tmp_path):
    wt = tmp_path / ".worktrees" / "T1"
    wt.mkdir(parents=True)
    reg = {os.path.normcase(os.path.abspath(str(wt)))}
    tasks = {"T1": {"status": "review"}}
    viol = devtask._worktree_violations(tasks, str(tmp_path / ".worktrees"), reg, set())
    assert viol[0]["missing"] == ["branch"]
    assert viol[0]["branch"] == "task/T1"


def test_violations_ignore_done_and_aborted(tmp_path):
    tasks = {"D1": {"status": "done"}, "A1": {"status": "aborted"}}
    assert devtask._worktree_violations(tasks, str(tmp_path), set(), set()) == []


def test_violations_custom_worktree_and_branch_fields(tmp_path):
    wt = tmp_path / "custom_dir"
    wt.mkdir()
    reg = {os.path.normcase(os.path.abspath(str(wt)))}
    tasks = {"T9": {"status": "doing", "worktree": str(wt), "branch": "custom/br"}}
    assert devtask._worktree_violations(tasks, str(tmp_path), reg, {"custom/br"}) == []
    viol = devtask._worktree_violations(tasks, str(tmp_path), reg, {"other"})
    assert viol and viol[0]["missing"] == ["branch"]


# ---------- ② 主仓解析 ----------
def test_resolve_main_repo(tmp_path):
    resolved = devtask._resolve_main_repo(str(REPO))
    assert os.path.isdir(os.path.join(resolved, ".worktrees")), resolved
    # 在 worktree 内同样应解析到主仓（若本测试就跑在 worktree 内则相等）
    assert os.path.basename(resolved) in ("老田的AI助手", os.path.basename(str(REPO)))


# ---------- ③ 真实仓只读校验 ----------
def test_audit_worktrees_real_repo():
    viol, active = devtask._audit_worktrees()
    assert isinstance(active, int) and active >= 0
    assert isinstance(viol, list)
    for v in viol:
        assert v["status"] in devtask.WTCHECK_ACTIVE_STATES
        assert set(v["checks"].keys()) == {"dir", "registered", "branch"}
        assert v["missing"] and all(k in v["checks"] for k in v["missing"])


# ---------- ④ CLI ----------
def test_cli_json_contract(tmp_path):
    r = _run("audit-worktrees", "--json")
    data = json.loads(r.stdout)
    assert set(data.keys()) == {"repo", "active", "violations", "ok", "rc"}
    assert r.returncode == (1 if data["violations"] else 0)
    assert data["ok"] is (not data["violations"])
    assert data["rc"] == r.returncode
    assert os.path.isdir(os.path.join(data["repo"], ".worktrees")), data["repo"]


def test_cli_human_output():
    r = _run("audit-worktrees")
    assert "audit-worktrees（W2-3）" in r.stdout
    assert "汇总：" in r.stdout
    assert ("PASS" in r.stdout) or ("FAIL" in r.stdout)
    # 有违规时 rc=1；无违规 rc=0
    assert r.returncode == (1 if "[VIOL]" in r.stdout else 0)


def test_cli_unknown_flag_rejected():
    r = _run("audit-worktrees", "--nope")
    assert r.returncode != 0
    assert "未知参数" in (r.stdout + r.stderr)


def test_list_default_unchanged_and_strict_consistent():
    base = _run("list", "--project", "__none_existent__")
    assert base.returncode == 0, "list 默认行为必须不变（rc=0）"
    strict = _run("list", "--project", "__none_existent__", "--strict")
    audit = _run("audit-worktrees", "--json")
    assert strict.returncode == audit.returncode, "list --strict 应与 audit-worktrees rc 一致"


# ---------- ⑤ 接线静态证据 ----------
def test_wiring_static():
    src = open(Path(devtask.__file__), encoding="utf-8").read()
    assert '"audit-worktrees": lambda: cmd_audit_worktrees(*rest)' in src
    assert "audit-worktrees [--json] [--quiet] [--repo <path>]" in src
    assert "WTCHECK_ACTIVE_STATES" in src
    assert "def cmd_audit_worktrees" in src
