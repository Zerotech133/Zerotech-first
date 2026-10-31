# -*- coding: utf-8 -*-
"""task_ledger_reconcile 单测（K-ORG-20261031-LEDGERRECON，2026-10-31）。

覆盖：
  · merged / unmerged / no_branch / no_worktree / bad_json 分类与 exit code；
  · dry-run 零写入；--apply 仅白名单 merged+doing、备份+原子写、幂等；
  · 拒绝越权（非 DSH-Lead / review / unmerged 不动）；
  · CAS 并发保护（加载后文件被改 → 放弃写入、不覆盖）；
  · 默认 worktree 路径（repo/.worktrees/<tid>）与 .git 存在性判定。
全部用 tmp tasks.json + fake git runner，绝不碰真实账本。
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MOD_PATH = REPO / "tools" / "task_ledger_reconcile.py"


def _load():
    spec = importlib.util.spec_from_file_location("task_ledger_reconcile_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _make_runner(branches):
    """branches: {branch: {"exists": bool, "merged": bool}} → fake git runner。

    未在字典中的分支 = 不存在（no_branch）；字典中有该键即视为存在（exists 默认 True）。
    """
    def run(cmd):
        args = list(cmd[3:]) if len(cmd) > 3 else []
        if args[:2] == ["rev-parse", "--verify"]:
            branch = args[3].replace("refs/heads/", "") if len(args) > 3 else ""
            st = branches.get(branch)
            if st is not None and st.get("exists", True):
                return 0, "abc123\n", ""
            return 1, "", "unknown revision"
        if args[:2] == ["merge-base", "--is-ancestor"]:
            branch = args[2].replace("refs/heads/", "") if len(args) > 2 else ""
            st = branches.get(branch) or {}
            return (0 if st.get("merged") else 1), "", ""
        return 0, "", ""
    return run


def _write_tasks(tmp_path, entries):
    tasks_path = tmp_path / ".worktrees" / "tasks.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    return tasks_path


def _mk_worktree(tmp_path, tid):
    wt = tmp_path / ".worktrees" / tid
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: fake\n", encoding="utf-8")
    return wt


def _entry(status="doing", owner="DSH-Lead", branch=None, worktree=None):
    e = {"status": status, "owner": owner, "desc": "t"}
    if branch:
        e["branch"] = branch
    if worktree:
        e["worktree"] = str(worktree)
    return e


def test_classification_and_exit_code(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    wt_b = _mk_worktree(tmp_path, "T-B")
    tasks_path = _write_tasks(tmp_path, {
        "T-A": _entry(branch="task/T-A"),                       # merged
        "T-B": _entry(branch="task/T-B", worktree=wt_b),       # unmerged + worktree
        "T-C": _entry(status="review", branch="task/T-C"),     # no_branch（review 也扫）
        "T-D": _entry(branch="task/T-D"),                      # unmerged + no_worktree
    })
    runner = _make_runner({"task/T-A": {"merged": True},
                           "task/T-B": {"merged": False},
                           "task/T-D": {"merged": False}})
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner", runner)
    rc = M.main(["--json", "--tasks", str(tasks_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["by_status"] == {"doing": 3, "review": 1}
    assert out["counts"] == {"merged": 1, "unmerged": 1, "no_branch": 1, "no_worktree": 1}
    assert out["needs_action"] == 3
    by = {it["tid"]: it["classification"] for it in out["items"]}
    assert by == {"T-A": "merged", "T-B": "unmerged",
                  "T-C": "no_branch", "T-D": "no_worktree"}


def test_all_consistent_exit0(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    wt = _mk_worktree(tmp_path, "T-OK")
    tasks_path = _write_tasks(tmp_path, {"T-OK": _entry(branch="task/T-OK", worktree=wt)})
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner",
                        _make_runner({"task/T-OK": {"merged": False}}))
    assert M.main(["--json", "--tasks", str(tasks_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["needs_action"] == 0 and out["counts"]["unmerged"] == 1


def test_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    tasks_path = _write_tasks(tmp_path, {"T-A": _entry(branch="task/T-A")})
    before = tasks_path.read_bytes()
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner",
                        _make_runner({"task/T-A": {"merged": True}}))
    rc = M.main(["--tasks", str(tasks_path)])
    capsys.readouterr()
    assert rc == 1
    assert tasks_path.read_bytes() == before, "默认只读，不得写盘"


def test_apply_only_whitelist_merged_doing_with_backup(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    wt_b = _mk_worktree(tmp_path, "T-B")
    tasks_path = _write_tasks(tmp_path, {
        "T-A": _entry(branch="task/T-A"),                    # merged doing DSH-Lead → 关
        "T-B": _entry(branch="task/T-B", worktree=wt_b),     # unmerged → 不动
        "T-C": _entry(status="review", branch="task/T-C"),   # review merged → 不动
        "T-D": _entry(owner="ZHAOYUN", branch="task/T-D"),   # 非白名单 → 拒
    })
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner", _make_runner({
        "task/T-A": {"merged": True}, "task/T-B": {"merged": False},
        "task/T-C": {"merged": True}, "task/T-D": {"merged": True}}))
    rc = M.main(["--json", "--apply", "--tasks", str(tasks_path), "--owners", "DSH-Lead"])
    out = json.loads(capsys.readouterr().out)
    data = json.loads(tasks_path.read_text(encoding="utf-8"))
    assert data["T-A"]["status"] == "done"
    assert "账本对账自动关闭" in data["T-A"]["note"]
    assert data["T-B"]["status"] == "doing"
    assert data["T-C"]["status"] == "review"
    assert data["T-D"]["status"] == "doing"                  # 越权不关
    assert out["apply"]["applied"] == ["T-A"]
    assert any("T-D" in r for r in out["apply"]["refused"])
    backup = Path(out["apply"]["backup"])
    assert backup.is_file() and backup.name.startswith("tasks.json.bak-")
    assert not list(tasks_path.parent.glob("tasks.ledger.*.tmp")), "原子写不得残留 tmp"
    # 仍有 needs_action（T-C review merged / T-D merged 等）→ rc=1
    assert rc == 1


def test_apply_refuses_non_whitelist_only(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    tasks_path = _write_tasks(tmp_path, {"T-X": _entry(owner="ZHAOYUN", branch="task/T-X")})
    before = tasks_path.read_bytes()
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner",
                        _make_runner({"task/T-X": {"merged": True}}))
    rc = M.main(["--apply", "--tasks", str(tasks_path), "--owners", "DSH-Lead"])
    capsys.readouterr()
    assert rc == 1
    assert tasks_path.read_bytes() == before


def test_apply_idempotent(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    tasks_path = _write_tasks(tmp_path, {"T-A": _entry(branch="task/T-A")})
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner",
                        _make_runner({"task/T-A": {"merged": True}}))
    assert M.main(["--apply", "--tasks", str(tasks_path)]) == 0
    capsys.readouterr()
    first = tasks_path.read_text(encoding="utf-8")
    assert M.main(["--apply", "--tasks", str(tasks_path)]) == 0
    capsys.readouterr()
    assert tasks_path.read_text(encoding="utf-8") == first, "重复 apply 应幂等且不再写"


def test_bad_json_fail_visible(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    tasks_path = tmp_path / ".worktrees" / "tasks.json"
    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text("{不是 JSON", encoding="utf-8")
    before = tasks_path.read_bytes()
    monkeypatch.setattr(M, "REPO", repo)
    rc = M.main(["--json", "--tasks", str(tasks_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["classification"] == "bad_json"
    assert tasks_path.read_bytes() == before


def test_cas_concurrent_guard(tmp_path, monkeypatch):
    repo = tmp_path
    tasks_path = _write_tasks(tmp_path, {"T-A": _entry(branch="task/T-A")})
    runner = _make_runner({"task/T-A": {"merged": True}})
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "_default_runner", runner)
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    result = M.reconcile(tasks, repo, runner=runner)
    orig_copy = M.shutil.copy2

    def racing_copy(src, dst, *a, **k):
        orig_copy(src, dst, *a, **k)
        # 模拟备份期间另一会话写入新内容
        tasks_path.write_text(json.dumps({"OTHER": {"status": "doing"}},
                                         ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(M.shutil, "copy2", racing_copy)
    out = M.apply_merged_doing(result, tasks, tasks_path, ["DSH-Lead"], runner=runner)
    assert out["error"] and "并发" in out["error"]
    assert out["applied"] == []
    # 文件保持并发方写入的内容，未被本工具覆盖
    assert json.loads(tasks_path.read_text(encoding="utf-8")) == {"OTHER": {"status": "doing"}}


def test_default_worktree_path(tmp_path, monkeypatch):
    repo = tmp_path
    monkeypatch.setattr(M, "REPO", repo)
    item = M.classify_entry("T-N", {"status": "doing"},
                            repo, runner=_make_runner({"task/T-N": {"merged": False}}))
    assert item["worktree"] == str(tmp_path / ".worktrees" / "T-N")
    assert item["classification"] == "no_worktree"
