# -*- coding: utf-8 -*-
"""worktree_invariant_check 单测（W2 工作树/abort 不变量只读检查器）。

覆盖：
- 全绿 pass（doing/review + aborted + done 混合）；
- owner 违规四态（空/占位/多值 list/多值字符串）；
- branch 字段缺失 / git ref 不存在；
- worktree 字段缺失 / 目录不存在；
- 在制单重复 branch / 重复 worktree 路径；
- --no-git 跳过分支 ref 校验（离线降级口径）；
- aborted：无任何原因=违规；仅 note 兜底=known_debt（默认不阻断）；
  历史拼写 aborted_reason 兜底；"(未填)" 占位视为缺失；
- --fail-on-abort-reason 严格模式把 known_debt 升级为违规（退出码 1）；
- status 非法 / 工单值非 dict；
- 输入缺失（文件不存在/坏 JSON/顶层非 dict/git 仓库不可用）→ 退出码 2；
- 只读保证：tasks.json 字节不变；
- JSON / markdown 双输出与 --json-out / --md-out 落盘。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import worktree_invariant_check as wic  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def _git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@local")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _branch(repo: Path, name: str) -> None:
    result = _git(repo, "branch", name)
    assert result.returncode == 0, result.stderr


def _ticket(repo: Path, tid: str, *, status="doing", owner="DEV1",
            branch=None, worktree=None, make_worktree=True, **extra) -> dict:
    if branch is None:
        branch = "task/" + tid
    if worktree is None:
        worktree = str(repo / ".worktrees" / tid)
    if make_worktree:
        (repo / ".worktrees" / tid).mkdir(parents=True, exist_ok=True)
    task = {
        "desc": "测试工单", "branch": branch, "worktree": worktree,
        "status": status, "created": "2026-10-31 12:00:00",
        "project": "general", "owner": owner,
    }
    task.update(extra)
    return task


def _aborted(**extra) -> dict:
    task = {
        "desc": "弃单", "branch": "", "worktree": "", "status": "aborted",
        "created": "2026-10-31 10:00:00", "project": "general", "owner": "DEV1",
    }
    task.update(extra)
    return task


def _write_tasks(repo: Path, tasks: dict) -> Path:
    directory = repo / ".worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tasks.json"
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _check(repo: Path, tasks: dict, *, use_git=True, strict=False) -> dict:
    branch_refs = wic.load_git_branches(repo) if use_git else set()
    return wic.check(tasks, repo=repo, tasks_path=repo / ".worktrees" / "tasks.json",
                     branch_refs=branch_refs, git_checked=use_git, strict=strict)


def _codes(result: dict) -> list[str]:
    return [item["code"] for item in result["violations"]]


def _codes_of(result: dict, key: str) -> list[str]:
    return [item["code"] for item in result[key]]


# ---------------------------------------------------------------------------
# 1) pass
# ---------------------------------------------------------------------------
def test_clean_repo_pass(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    _branch(repo, "task/R1")
    tasks = {
        "T1": _ticket(repo, "T1"),
        "R1": _ticket(repo, "R1", status="review", owner="QA1"),
        "A1": _aborted(abort_reason="被 WO-XXX 取代"),
        "D1": {"status": "done", "owner": "", "desc": "d"},
    }
    result = _check(repo, tasks)
    assert result["verdict"] == "PASS"
    assert result["violations"] == []
    assert result["known_debt"] == []
    assert result["counts"]["tasks_total"] == 4
    assert result["counts"]["active"] == 2
    assert result["counts"]["doing"] == 1
    assert result["counts"]["review"] == 1
    assert result["counts"]["aborted"] == 1
    assert result["counts"]["aborted_missing_abort_reason"] == 0
    assert result["git"]["checked"] is True


def test_empty_tasks_pass(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {})
    assert result["verdict"] == "PASS"
    assert result["counts"]["tasks_total"] == 0
    assert result["violations"] == []


# ---------------------------------------------------------------------------
# 2) doing/review：owner
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("owner,expected", [
    ("", wic.CODE_ACTIVE_EMPTY_OWNER),
    ("unknown-agent", wic.CODE_ACTIVE_INVALID_OWNER),
    (["DEV1", "DEV2"], wic.CODE_ACTIVE_MULTI_OWNER),
    ("DEV1, DEV2", wic.CODE_ACTIVE_MULTI_OWNER),
    ("DEV1、DEV2", wic.CODE_ACTIVE_MULTI_OWNER),
])
def test_active_owner_invariants(tmp_path, owner, expected):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    result = _check(repo, {"T1": _ticket(repo, "T1", owner=owner)})
    assert expected in _codes(result)
    assert result["verdict"] == "FAIL"


def test_active_owner_list_single_element_ok(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    result = _check(repo, {"T1": _ticket(repo, "T1", owner=["DEV1"])})
    assert result["violations"] == []


def test_owner_slash_single_value_ok(tmp_path):
    """部门/席位联写（研发部/DEV1）是单 owner，不按多 owner 误伤。"""
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    result = _check(repo, {"T1": _ticket(repo, "T1", owner="研发部/DEV1")})
    assert result["violations"] == []


# ---------------------------------------------------------------------------
# 3) doing/review：branch / worktree
# ---------------------------------------------------------------------------
def test_missing_branch_field_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"T1": _ticket(repo, "T1", branch="")})
    assert wic.CODE_ACTIVE_MISSING_BRANCH in _codes(result)


def test_branch_ref_not_found_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")  # 不建 task/T1
    result = _check(repo, {"T1": _ticket(repo, "T1")})
    assert wic.CODE_ACTIVE_BRANCH_NOT_FOUND in _codes(result)


def test_missing_worktree_field_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    result = _check(repo, {"T1": _ticket(repo, "T1", worktree="",
                                        make_worktree=False)})
    assert wic.CODE_ACTIVE_MISSING_WORKTREE in _codes(result)


def test_worktree_path_not_found_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    result = _check(repo, {"T1": _ticket(repo, "T1", make_worktree=False)})
    assert wic.CODE_ACTIVE_WORKTREE_NOT_FOUND in _codes(result)


def test_worktree_relative_path_resolved_against_repo(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    (repo / ".worktrees" / "T1").mkdir(parents=True, exist_ok=True)
    result = _check(repo, {"T1": _ticket(
        repo, "T1", worktree=".worktrees/T1", make_worktree=False)})
    assert result["violations"] == []


# ---------------------------------------------------------------------------
# 4) doing/review：重复 branch / 重复 worktree
# ---------------------------------------------------------------------------
def test_duplicate_branch_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/SHARED")
    tasks = {
        "T1": _ticket(repo, "T1", branch="task/SHARED"),
        "T2": _ticket(repo, "T2", branch="task/SHARED"),
    }
    result = _check(repo, tasks)
    dup = [item for item in result["violations"]
           if item["code"] == wic.CODE_ACTIVE_DUPLICATE_BRANCH]
    assert len(dup) == 1
    assert set(dup[0]["tasks"]) == {"T1", "T2"}


def test_duplicate_worktree_path_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    _branch(repo, "task/T2")
    shared = str(repo / ".worktrees" / "SHARED")
    (repo / ".worktrees" / "SHARED").mkdir(parents=True, exist_ok=True)
    tasks = {
        "T1": _ticket(repo, "T1", worktree=shared, make_worktree=False),
        "T2": _ticket(repo, "T2", worktree=shared, make_worktree=False),
    }
    result = _check(repo, tasks)
    dup = [item for item in result["violations"]
           if item["code"] == wic.CODE_ACTIVE_DUPLICATE_WORKTREE]
    assert len(dup) == 1
    assert set(dup[0]["tasks"]) == {"T1", "T2"}


# ---------------------------------------------------------------------------
# 5) --no-git 降级口径
# ---------------------------------------------------------------------------
def test_no_git_skips_branch_ref_check(tmp_path):
    repo = _init_repo(tmp_path / "repo")  # 不建 task/T1
    result = _check(repo, {"T1": _ticket(repo, "T1")}, use_git=False)
    assert result["checks"]["active_branch_ref"] is False
    assert result["git"] == {"checked": False, "branch_count": 0}
    assert wic.CODE_ACTIVE_BRANCH_NOT_FOUND not in _codes(result)
    assert result["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# 6) aborted：原因口径（默认 / 严格）
# ---------------------------------------------------------------------------
def test_aborted_note_only_is_known_debt_not_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"A1": _aborted(note="2026-10-31 无分支清场")})
    assert result["verdict"] == "PASS"
    assert result["violations"] == []
    assert _codes_of(result, "known_debt") == [wic.CODE_ABORTED_MISSING_ABORT_REASON]
    assert result["counts"]["aborted_missing_abort_reason"] == 1
    assert result["counts"]["aborted_no_reason_signal"] == 0


def test_aborted_no_reason_at_all_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"A1": _aborted()})
    assert result["verdict"] == "FAIL"
    assert wic.CODE_ABORTED_NO_REASON in _codes(result)
    assert result["counts"]["aborted_no_reason_signal"] == 1
    assert result["counts"]["aborted_missing_abort_reason"] == 1


def test_aborted_placeholder_reason_is_missing(tmp_path):
    """devtask.py cmd_drop 无原因时写 "(未填)"——语义等于没写原因。"""
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"A1": _aborted(abort_reason="(未填)")})
    assert wic.CODE_ABORTED_NO_REASON in _codes(result)


def test_aborted_legacy_spelled_reason_is_known_debt(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"A1": _aborted(
        aborted_reason="历史拼写原因（存量 1 张）")})
    assert result["verdict"] == "PASS"
    assert _codes_of(result, "known_debt") == [wic.CODE_ABORTED_MISSING_ABORT_REASON]


def test_strict_mode_promotes_missing_reason(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    tasks = {"A1": _aborted(note="只有 note"), "A2": _aborted(abort_reason="正式原因")}
    result = _check(repo, tasks, strict=True)
    assert result["mode"] == "strict_abort_reason"
    assert result["verdict"] == "FAIL"
    assert wic.CODE_ABORTED_MISSING_ABORT_REASON in _codes(result)
    # 欠账仍如实保留（计数口径不因严格模式消失）
    assert result["counts"]["known_debt"] == 1
    assert result["counts"]["aborted_missing_abort_reason"] == 1


# ---------------------------------------------------------------------------
# 7) done / status / 结构
# ---------------------------------------------------------------------------
def test_done_not_checked(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    tasks = {"D1": {"status": "done", "owner": "", "branch": "", "worktree": "",
                    "desc": "历史完工单"}}
    result = _check(repo, tasks)
    assert result["verdict"] == "PASS"
    assert result["violations"] == []


def test_invalid_status_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"X1": {"status": "blocked", "owner": "DEV1"}})
    assert wic.CODE_TASK_STATUS_INVALID in _codes(result)


def test_missing_status_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"X1": {"owner": "DEV1", "desc": "无状态"}})
    assert wic.CODE_TASK_STATUS_INVALID in _codes(result)


def test_non_dict_entry_is_violation(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"X1": ["not", "a", "dict"], "T1": _ticket(repo, "T1")})
    assert wic.CODE_TASK_ENTRY_INVALID in _codes(result)


# ---------------------------------------------------------------------------
# 8) 输入缺失（退出码 2）
# ---------------------------------------------------------------------------
def test_load_tasks_missing_raises(tmp_path):
    with pytest.raises(wic.InputError):
        wic.load_tasks(tmp_path / "nope" / "tasks.json")


def test_load_tasks_bad_json_raises(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(wic.InputError):
        wic.load_tasks(path)


def test_load_tasks_top_level_list_raises(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(wic.InputError):
        wic.load_tasks(path)


def test_cli_missing_input_exit_2(tmp_path, capsys):
    repo = _init_repo(tmp_path / "repo")
    assert wic.main(["--tasks", str(repo / "missing.json"),
                     "--repo", str(repo)]) == 2
    assert "tasks.json 不存在" in capsys.readouterr().err


def test_cli_bad_json_exit_2(tmp_path, capsys):
    repo = _init_repo(tmp_path / "repo")
    path = repo / ".worktrees" / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    assert wic.main(["--tasks", str(path), "--repo", str(repo)]) == 2
    assert "顶层必须是 dict" in capsys.readouterr().err


def test_cli_git_unavailable_exit_2(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    path = _write_tasks(plain, {})
    assert wic.main(["--tasks", str(path), "--repo", str(plain)]) == 2
    err = capsys.readouterr().err
    assert "--no-git" in err


def test_cli_no_git_works_offline(tmp_path):
    """非 git 环境用 --no-git：只校验字段/目录存在，仍可出报告。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".worktrees" / "T1").mkdir(parents=True)
    path = _write_tasks(plain, {
        "T1": {"status": "doing", "owner": "DEV1", "branch": "task/T1",
               "worktree": str(plain / ".worktrees" / "T1"), "desc": "d"},
        "A1": _aborted(abort_reason="正式原因"),
    })
    assert wic.main(["--no-git", "--json", "--tasks", str(path),
                     "--repo", str(plain)]) == 0


# ---------------------------------------------------------------------------
# 9) CLI 退出码 / 输出 / 落盘 / 只读
# ---------------------------------------------------------------------------
def _clean_fixture(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _branch(repo, "task/T1")
    path = _write_tasks(repo, {"T1": _ticket(repo, "T1"),
                               "A1": _aborted(abort_reason="正式原因")})
    return repo, path


def test_cli_json_pass_exit_0(tmp_path, capsys):
    repo, path = _clean_fixture(tmp_path)
    assert wic.main(["--json", "--tasks", str(path), "--repo", str(repo)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PASS"
    assert payload["mode"] == "default"
    assert payload["counts"]["active"] == 1
    assert payload["git"]["checked"] is True
    assert payload["violations"] == []


def test_cli_json_violation_exit_1(tmp_path, capsys):
    repo = _init_repo(tmp_path / "repo")
    path = _write_tasks(repo, {"T1": _ticket(repo, "T1")})  # branch/worktree 均缺 ref/目录
    assert wic.main(["--json", "--tasks", str(path), "--repo", str(repo)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "FAIL"
    assert wic.CODE_ACTIVE_BRANCH_NOT_FOUND in [v["code"] for v in payload["violations"]]


def test_cli_strict_flag_exit_1(tmp_path, capsys):
    repo = _init_repo(tmp_path / "repo")
    path = _write_tasks(repo, {"A1": _aborted(note="只有 note")})
    assert wic.main(["--json", "--tasks", str(path), "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert wic.main(["--json", "--fail-on-abort-reason", "--tasks", str(path),
                     "--repo", str(repo)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "strict_abort_reason"
    assert payload["counts"]["violations"] == 1


def test_cli_markdown_default(tmp_path, capsys):
    repo, path = _clean_fixture(tmp_path)
    assert wic.main(["--tasks", str(path), "--repo", str(repo)]) == 0
    text = capsys.readouterr().out
    assert "# W2 工作树/abort 不变量 · 只读检查" in text
    assert "## 判定" in text
    assert "**PASS**" in text
    assert "## 计数" in text
    assert "## 口径" in text
    assert "只读" in text


def test_cli_report_files_written_and_tasks_untouched(tmp_path, capsys):
    repo, path = _clean_fixture(tmp_path)
    before = path.read_bytes()
    out_dir = tmp_path / "reports"
    assert wic.main(["--tasks", str(path), "--repo", str(repo),
                     "--json-out", str(out_dir / "w2.json"),
                     "--md-out", str(out_dir / "w2.md")]) == 0
    capsys.readouterr()
    assert path.read_bytes() == before  # tasks.json 只读：字节不变
    payload = json.loads((out_dir / "w2.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"
    assert payload["tasks_path"] == str(path)
    markdown = (out_dir / "w2.md").read_text(encoding="utf-8")
    assert markdown.startswith("# W2 工作树/abort 不变量 · 只读检查")


def test_cli_default_run_does_not_modify_tasks(tmp_path, capsys):
    repo, path = _clean_fixture(tmp_path)
    before = path.read_bytes()
    assert wic.main(["--tasks", str(path), "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert path.read_bytes() == before


def test_render_markdown_escapes_pipe(tmp_path):
    """branch 名含 "|" 时表格单元必须转义，否则 markdown 表格断列。"""
    repo = _init_repo(tmp_path / "repo")
    result = _check(repo, {"T1": _ticket(repo, "T1", branch="task/A|B")})
    markdown = wic.render_markdown(result)
    in_table = [line for line in markdown.splitlines()
                if "ACTIVE_BRANCH_NOT_FOUND" in line]
    assert in_table and "task/A\\|B" in in_table[0]


def test_md_cell_escapes_pipe_and_newline():
    assert wic._md_cell("a|b") == "a\\|b"
    assert wic._md_cell("a\nb") == "a b"
