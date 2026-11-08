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


def _live_wt(tmp_path):
    """造一个"活着"的 worktree。

    必须在目录里放 `.git` 指针：只判 `os.path.isdir` 会把空壳当活树，而
    `git -C <空壳> status` 会向上命中主仓 .git，读到主树状态（2026-11-08 判例）。
    """
    (tmp_path / ".git").write_text("gitdir: /tmp/fake-worktree\n", encoding="utf-8")
    return tmp_path


def test_recycle_dirty_worktree_kept(monkeypatch, tmp_path):
    fake = _FakeGit(dirty=" M portal/x.py\n")
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree(
        "T1", {"worktree": str(_live_wt(tmp_path)), "branch": "task/T1"})
    assert ok is False and "脏" in note
    assert not any(c[:2] == ("worktree", "remove") for c in fake.calls), "脏树不得 remove"


def test_recycle_clean_worktree_removes_and_deletes_branch(monkeypatch, tmp_path):
    fake = _FakeGit(dirty="")
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree(
        "T1", {"worktree": str(_live_wt(tmp_path)), "branch": "task/T1"})
    assert ok is True and "已回收" in note
    assert ("worktree", "remove", str(tmp_path)) in fake.calls
    assert ("branch", "-d", "task/T1") in fake.calls


def test_recycle_failure_not_raising(monkeypatch, tmp_path):
    fake = _FakeGit(dirty="", fail_on=("worktree",))
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree(
        "T1", {"worktree": str(_live_wt(tmp_path)), "branch": "task/T1"})
    assert ok is False and "remove 失败" in note  # 失败只返回原因，不抛


def test_recycle_timeout_with_pointer_gone_prunes(monkeypatch, tmp_path):
    """删到一半超时（.git 已失）→ 解注册 + 清残留，且不得报成"脏"。"""
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args[:2] == ("worktree", "remove"):
            (tmp_path / ".git").unlink()          # 模拟 git 先删指针再删树
            raise SystemExit("git worktree remove 超时（300s）")
        return ""

    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree(
        "T1", {"worktree": str(_live_wt(tmp_path)), "branch": "task/T1"})
    assert ok is False
    assert "删到一半" in note and "已解注册" in note, note
    assert "脏" not in note
    assert ("worktree", "prune") in calls
    assert not tmp_path.exists(), "残留目录应被清掉"


def test_recycle_dead_worktree_not_reported_dirty(monkeypatch, tmp_path):
    """空壳（.git 缺失）必须判"登记已死"，不能跑 status 得出"脏"的假结论。"""
    fake = _FakeGit(dirty=" M portal/x.py\n M portal/y.py\n")
    monkeypatch.setattr(devtask, "_git", fake)
    ok, note = devtask._recycle_worktree("T1", {"worktree": str(tmp_path), "branch": "task/T1"})
    assert ok is False
    assert "登记已死" in note, note
    assert "脏" not in note, "空壳不得被误判为脏树：%s" % note
    assert not any(c[:2] == ("status", "--porcelain") for c in fake.calls), \
        "空壳目录不得再跑 git status（会向上读到主仓状态）"
    assert ("worktree", "prune") in fake.calls, "空壳应就地解注册"


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
    monkeypatch.setenv(devtask.WT_TTL_ENV, "0.001")
    plan = devtask._prune_plan(MAIN)
    assert plan["worktrees"], "主仓应有 worktree 记录"
    ttl = devtask._ttl_hours()
    for it in plan["worktrees"]:
        assert "age_hours" in it and "ttl_red" in it
        # 规则断言（不做"全红"绝对判断）：并行会话可能在测试瞬间新建 worktree，
        # 其 age 可小于极小 TTL，故逐条按规则核对，避免时钟竞态 flaky。
        expect = it["age_hours"] is not None and it["age_hours"] > ttl
        assert it["ttl_red"] is expect, "ttl_red 与规则不符：%s" % it.get("tid")
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
    # 2026-11-08 校准：65 → 80（常态 55~60 落在旧 warn 线 52 之上，告警形同噪声）
    assert devtask._worktree_cap() == devtask.MAX_WORKTREES_DEFAULT == 80
    monkeypatch.setenv(devtask.MAX_WORKTREES_ENV, "70")
    assert devtask._worktree_cap() == 70
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv(devtask.MAX_WORKTREES_ENV, bad)
        assert devtask._worktree_cap() == 80


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


# ---------- ③b 空壳登记判活 + 删不动兜底（2026-11-08 两个真坑） ----------

def test_wt_pointer_ok(tmp_path):
    """判活的唯一依据是 `.git` 指针，不是目录存不存在。"""
    assert devtask._wt_pointer_ok(str(tmp_path)) is False
    (tmp_path / ".git").write_text("gitdir: /tmp/x\n", encoding="utf-8")
    assert devtask._wt_pointer_ok(str(tmp_path)) is True


def test_prune_worktrees_marks_dead_and_skips_main(monkeypatch, tmp_path):
    """`git worktree list` 里的空壳要被标 dead；主树即使没有 .git 文件也不算 dead。"""
    live = tmp_path / "live"
    live.mkdir()
    (live / ".git").write_text("gitdir: /tmp/l\n", encoding="utf-8")
    shell = tmp_path / "shell"
    shell.mkdir()                      # 只有目录，没有 .git —— 典型"删到一半"
    main = tmp_path / "main"
    main.mkdir()
    (main / ".git").mkdir()            # 主树的 .git 是**目录**，不是指针文件

    porcelain = "\n".join([
        "worktree %s" % main, "HEAD aaa", "branch refs/heads/master", "",
        "worktree %s" % live, "HEAD bbb", "branch refs/heads/task/L", "",
        "worktree %s" % shell, "HEAD ccc", "detached", "",
    ])

    class _R:
        returncode = 0
        stdout = porcelain
        stderr = ""

    monkeypatch.setattr(devtask, "_prune_git", lambda *a, **k: _R())
    items = {os.path.basename(x["path"]): x for x in devtask._prune_worktrees(str(tmp_path))}
    assert items["shell"]["dead"] is True, "缺 .git 指针必须判 dead"
    assert items["shell"]["exists"] is True, "目录还在，exists 仍为 True"
    assert items["live"]["dead"] is False
    assert items["main"]["dead"] is False


def test_active_worktree_count_excludes_dead(monkeypatch, tmp_path):
    """空壳不该挤占并发名额，否则闸门长期虚高。"""
    monkeypatch.setattr(devtask, "_prune_worktrees", lambda repo: [
        {"path": str(tmp_path), "branch": "master", "exists": True, "dead": False, "bare": False},
        {"path": str(tmp_path / "a"), "branch": "task/A", "exists": True, "dead": False, "bare": False},
        {"path": str(tmp_path / "b"), "branch": "task/B", "exists": True, "dead": False, "bare": False},
        {"path": str(tmp_path / "shell"), "branch": None, "exists": True, "dead": True, "bare": False},
    ])
    assert devtask._active_worktree_count(str(tmp_path)) == 2


def test_prune_remove_worktree_fallback_when_pointer_gone(monkeypatch, tmp_path):
    """删大目录超时、指针已失 → prune 解注册 + 清残留目录，算成功。"""
    shell = tmp_path / "shell"
    shell.mkdir()
    (shell / "junk.txt").write_text("x" * 32, encoding="utf-8")
    calls = []

    def fake_git(repo, *args, **kwargs):
        calls.append(args)
        rc = 124 if args[:2] == ("worktree", "remove") else 0

        class _R:
            returncode = rc
            stdout = ""
            stderr = "timeout" if rc else ""
        return _R()

    monkeypatch.setattr(devtask, "_prune_git", fake_git)
    ok, note = devtask._prune_remove_worktree(str(tmp_path), str(shell), timeout=1)
    assert ok is True, note
    assert ("worktree", "prune") in calls, "指针已失时必须解注册"
    assert not shell.exists(), "残留目录应被清掉"
    assert "指针已失" in note


def test_prune_remove_worktree_keeps_tree_when_pointer_alive(monkeypatch, tmp_path):
    """指针还在 → 是真的删不动（被占用/权限），不得再动手，交人工。"""
    live = tmp_path / "live"
    live.mkdir()
    _live_wt(live)
    calls = []

    def fake_git(repo, *args, **kwargs):
        calls.append(args)

        class _R:
            returncode = 1
            stdout = ""
            stderr = "fatal: could not remove"
        return _R()

    monkeypatch.setattr(devtask, "_prune_git", fake_git)
    ok, note = devtask._prune_remove_worktree(str(tmp_path), str(live), timeout=1)
    assert ok is False
    assert "remove 失败" in note
    assert live.exists(), "删不动时不得清目录"
    assert ("worktree", "prune") not in calls


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
