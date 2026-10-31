# -*- coding: utf-8 -*-
"""WO-WORKTREE-MECH-20261031 回归测试：merge 自动回收 / TTL 红灯 / 并发上限。

覆盖：
  ① _recycle_worktree：脏树保留不删、干净树 remove+branch -d、失败不抛（不阻断合并）
  ② _should_keep_worktree：--keep-worktree 与 env DEVTOOL_KEEP_WORKTREE
  ③ TTL：_ttl_hours env 解析（非法回默认 72）、_worktree_age_hours、_format_prune_wt 的
     [TTL-RED] 标注、_prune_plan 真实计划带 age_hours/ttl_red（env TTL 极小→全红，极大→无红）
  ④ 并发上限：_concurrency_verdict 边界（ok/warn 80%/block）、_worktree_cap env 覆写、
     _enforce_worktree_cap 达上限 SystemExit
  ⑤ 接线静态证据：cmd_merge 调 _recycle_worktree、--keep-worktree 在 merge 解析、cmd_new 调
     _enforce_worktree_cap、cmd_prune 用 _format_prune_wt

只读：真实仓仅做 `git worktree list`/`branch --merged` 只读查询，不删除任何 worktree。
"""
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


def _main_repo():
    """worktree 内跑测试时取主仓（git-common-dir 的父目录）；主干内跑即 REPO。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if out:
            return os.path.dirname(out)
    except Exception:  # noqa: BLE001
        pass
    return str(REPO)


MAIN = _main_repo()


# ---------- ① merge 自动回收 ----------
class _FakeGit:
    def __init__(self, dirty="", fail_on=None):
        self.calls = []
        self.dirty = dirty
        self.fail_on = fail_on or ()

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return self.dirty
        for k in self.fail_on:
            if args[:1] == (k,):
                raise SystemExit("boom:%s" % k)
        return ""


def test_recycle_dirty_worktree_kept(monkeypatch, tmp_path):
    fake = _FakeGit(dirty=" M portal/x.py\n")
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree("T1", {"worktree": str(tmp_path), "branch": "task/T1"})
    assert ok is False and "脏" in note
    assert not any(c[:2] == ("worktree", "remove") for c in fake.calls), "脏树不得 remove"


def test_recycle_clean_worktree_removes_and_deletes_branch(monkeypatch, tmp_path):
    fake = _FakeGit(dirty="")
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree("T1", {"worktree": str(tmp_path), "branch": "task/T1"})
    assert ok is True and "已回收" in note
    assert ("worktree", "remove", str(tmp_path)) in fake.calls
    assert ("branch", "-d", "task/T1") in fake.calls


def test_recycle_failure_not_raising(monkeypatch, tmp_path):
    fake = _FakeGit(dirty="", fail_on=("worktree",))
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree("T1", {"worktree": str(tmp_path), "branch": "task/T1"})
    assert ok is False and "remove 失败" in note  # 失败只返回原因，不抛


def test_recycle_missing_worktree_dir(monkeypatch, tmp_path):
    ok, note = devtask._recycle_worktree("T1", {"worktree": str(tmp_path / "nope"), "branch": "task/T1"})
    assert ok is False and "不存在" in note


def test_should_keep_worktree_flag_and_env(monkeypatch):
    monkeypatch.delenv(devtask.KEEP_WORKTREE_ENV, raising=False)
    assert devtask._should_keep_worktree([]) is False
    assert devtask._should_keep_worktree(["--keep-worktree"]) is True
    monkeypatch.setenv(devtask.KEEP_WORKTREE_ENV, "1")
    assert devtask._should_keep_worktree([]) is True
    monkeypatch.setenv(devtask.KEEP_WORKTREE_ENV, "0")
    assert devtask._should_keep_worktree([]) is False


# ---------- ② TTL ----------
def test_ttl_hours_env_parse(monkeypatch):
    monkeypatch.delenv(devtask.WT_TTL_ENV, raising=False)
    assert devtask._ttl_hours() == devtask.WT_TTL_HOURS_DEFAULT == 72.0
    monkeypatch.setenv(devtask.WT_TTL_ENV, "12")
    assert devtask._ttl_hours() == 12.0
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv(devtask.WT_TTL_ENV, bad)
        assert devtask._ttl_hours() == 72.0


def test_worktree_age_hours_from_created_and_mtime(tmp_path):
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S")
    age = devtask._worktree_age_hours(str(tmp_path), {"created": old})
    assert 99 < age < 102, age
    age2 = devtask._worktree_age_hours(str(tmp_path), {})
    assert age2 is not None and age2 < 1
    assert devtask._worktree_age_hours(str(tmp_path / "nope"), None) is None


def test_format_prune_wt_ttl_red_annotation():
    it = {"path": r"C:\x\wt", "reason": "分支已合并", "skip": False,
          "age_hours": 100.0, "ttl_red": True}
    line = devtask._format_prune_wt(it, False)
    assert "[TTL-RED]" in line and "[DRY]" in line and "age=100h" in line
    it2 = dict(it, ttl_red=False, age_hours=1.0)
    assert "[TTL-RED]" not in devtask._format_prune_wt(it2, False)
    it3 = dict(it, skip=True)
    assert devtask._format_prune_wt(it3, False).startswith("  [SKIP][TTL-RED] ")


def test_prune_plan_carries_ttl_fields(monkeypatch):
    monkeypatch.setenv(devtask.WT_TTL_ENV, "0.001")  # 极小 TTL → 全部工作树年龄都超阈
    plan = devtask._prune_plan(MAIN)
    assert plan["worktrees"], "主仓应有 worktree 记录"
    for it in plan["worktrees"]:
        assert "age_hours" in it and "ttl_red" in it
    assert all(it["ttl_red"] for it in plan["worktrees"]), "极小 TTL 下应全部 TTL-RED"
    monkeypatch.setenv(devtask.WT_TTL_ENV, "1000000")
    plan2 = devtask._prune_plan(MAIN)
    assert not any(it["ttl_red"] for it in plan2["worktrees"]), "极大 TTL 下应无红灯"


# ---------- ③ 并发上限 ----------
def test_concurrency_verdict_boundaries():
    assert devtask._concurrency_verdict(0, 60) == "ok"
    assert devtask._concurrency_verdict(47, 60) == "ok"
    assert devtask._concurrency_verdict(48, 60) == "warn"   # 恰好 80%
    assert devtask._concurrency_verdict(59, 60) == "warn"
    assert devtask._concurrency_verdict(60, 60) == "block"
    assert devtask._concurrency_verdict(61, 60) == "block"
    assert devtask._concurrency_verdict(999, 0) == "ok"     # cap 非法→不拦


def test_worktree_cap_env(monkeypatch):
    monkeypatch.delenv(devtask.MAX_WORKTREES_ENV, raising=False)
    assert devtask._worktree_cap() == devtask.MAX_WORKTREES_DEFAULT == 60
    monkeypatch.setenv(devtask.MAX_WORKTREES_ENV, "70")
    assert devtask._worktree_cap() == 70
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv(devtask.MAX_WORKTREES_ENV, bad)
        assert devtask._worktree_cap() == 60


def test_enforce_cap_blocks_when_count_at_cap(monkeypatch):
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda repo=None: 60)
    monkeypatch.setenv(devtask.MAX_WORKTREES_ENV, "60")
    with pytest.raises(SystemExit) as ei:
        devtask._enforce_worktree_cap()
    assert "上限" in str(ei.value)
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda repo=None: 59)
    devtask._enforce_worktree_cap()  # 不抛（warn 只打印）


def test_active_worktree_count_real_repo():
    n = devtask._active_worktree_count(MAIN)
    assert isinstance(n, int) and n >= 0


# ---------- ④ 接线静态证据 ----------
def test_merge_and_new_prune_wiring():
    src = open(Path(devtask.__file__), encoding="utf-8").read()
    assert "--keep-worktree" in src
    assert "_recycle_worktree(tid, t)" in src, "cmd_merge 必须调用自动回收"
    assert "_should_keep_worktree(flags)" in src
    assert "_enforce_worktree_cap()" in src, "cmd_new 必须前置并发闸"
    assert "_format_prune_wt(it, apply_)" in src, "cmd_prune 必须用 TTL 标注格式器"
    assert "[TTL-RED]" in src
    # help/docstring 标注
    assert "--keep-worktree=跳过自动回收" in src
