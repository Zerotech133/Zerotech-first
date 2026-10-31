# -*- coding: utf-8 -*-
"""W2 工作树 / abort 不变量只读检查器（2026-10-31 · WO-LEGION-W2-INVARIANT-20261031）。

背景：研发计划 v2 §W2「任务/计划/证据一本账」的三条硬要求——
  1. 新单未建 worktree 不得 doing；
  2. 一单一 owner；
  3. aborted 必须有原因。
本工具把这三条不变量（外加两条数据卫生规则：重复 branch / 重复 worktree）做成
**可重复执行的只读检查**，供每日对账与 M1/G1 验收；不替代 devtask 状态机。

检查口径：
- doing/review（在制，唯一需要「活着」的状态）：
    * owner 非空、非占位、单值（一单多 owner = 违规）；
    * branch 字段非空，且本地 git 分支 refs 中存在（--no-git 可跳过 ref 校验）；
    * worktree 字段非空，且目录存在（相对路径按 --repo 解析）；
    * 在制工单之间 branch / worktree 路径不得重复（同一份施工面只能挂一单）。
- aborted（弃单）：
    * 必须有原因：abort_reason / note / 历史拼写 aborted_reason 任一非空，
      否则违规（ABORTED_NO_REASON）；
    * 仅缺**显式** abort_reason（有 note 或历史拼写兜底）的存量，计入
      known_debt 并如实计数，默认模式不阻断；--fail-on-abort-reason 严格模式
      下升级为违规。存量欠账只统计，**不代写**（本工具没有任何写回逻辑）。
- done 不检查（终态已清场，历史字段噪声大）；status 缺失/非法计违规
  （devtask 状态机只允许 doing/review/done/aborted）。

只读保证：
- 不修改 .worktrees/tasks.json，不新建/关闭/改派工单，不碰 git 工作区；
- 报告只打印到 stdout，或写入显式指定的 --json-out / --md-out 文件。

退出码：0=无阻断违规；1=有阻断违规；2=输入缺失/不可读（tasks.json 缺失、
JSON 非法、顶层非 dict、需要 git 校验但仓库不可用）。

用法：
  python -X utf8 tools/worktree_invariant_check.py
  python -X utf8 tools/worktree_invariant_check.py --json
  python -X utf8 tools/worktree_invariant_check.py --fail-on-abort-reason   # 严格
  python -X utf8 tools/worktree_invariant_check.py --json-out reports/w2.json --md-out reports/w2.md
  python -X utf8 tools/worktree_invariant_check.py --tasks <path> --repo <repo> --no-git

在 linked worktree 内执行时，--repo 默认回落到主工作树根（同 tools/_repo_paths.py
口径），确保总是对账主仓的 .worktrees/tasks.json。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1.0"
TOOL_NAME = "worktree_invariant_check"

ACTIVE_STATUSES = ("doing", "review")
TERMINAL_STATUSES = ("done", "aborted")
KNOWN_STATUSES = frozenset(ACTIVE_STATUSES + TERMINAL_STATUSES)

# owner 占位值：新建单时若缺 DEVTOOL_AGENT/AGENT_NAME，devtask 会落
# unknown-agent——它不是「一个人」，在制单 owner 必须显式到人/席位。
OWNER_PLACEHOLDERS = frozenset({
    "", "-", "--", "?", "??", "unknown", "unknown-agent", "none", "null",
    "n/a", "na", "tbd", "todo", "待定", "未定", "无", "空",
})
# 一单多 owner 的常见写法；"/" 不作分隔符（"研发部/DEV1" 这类部门-席位联写是单值）。
OWNER_SEPARATORS = (",", "，", "、", "|", ";", "；")
# 原因占位值：devtask.py cmd_drop 无原因时会写 "(未填)"，语义等于没写。
REASON_PLACEHOLDERS = frozenset({
    "(未填)", "（未填）", "未填", "-", "--", "none", "null", "n/a", "na",
    "tbd", "todo", "无",
})

# 违规 code（阻断）
CODE_ACTIVE_EMPTY_OWNER = "ACTIVE_EMPTY_OWNER"
CODE_ACTIVE_MULTI_OWNER = "ACTIVE_MULTI_OWNER"
CODE_ACTIVE_INVALID_OWNER = "ACTIVE_INVALID_OWNER"
CODE_ACTIVE_MISSING_BRANCH = "ACTIVE_MISSING_BRANCH"
CODE_ACTIVE_BRANCH_NOT_FOUND = "ACTIVE_BRANCH_NOT_FOUND"
CODE_ACTIVE_MISSING_WORKTREE = "ACTIVE_MISSING_WORKTREE"
CODE_ACTIVE_WORKTREE_NOT_FOUND = "ACTIVE_WORKTREE_NOT_FOUND"
CODE_ACTIVE_DUPLICATE_BRANCH = "ACTIVE_DUPLICATE_BRANCH"
CODE_ACTIVE_DUPLICATE_WORKTREE = "ACTIVE_DUPLICATE_WORKTREE"
CODE_ABORTED_NO_REASON = "ABORTED_NO_REASON"
CODE_ABORTED_MISSING_ABORT_REASON = "ABORTED_MISSING_ABORT_REASON"
CODE_TASK_ENTRY_INVALID = "TASK_ENTRY_INVALID"
CODE_TASK_STATUS_INVALID = "TASK_STATUS_INVALID"


class InputError(Exception):
    """输入缺失/不可用（对应退出码 2）。"""


# ----------------------------------------------------------------------
# 路径 / 真源读取
# ----------------------------------------------------------------------
def _fallback_repo() -> Path:
    """本文件所在仓库根（tools/ 的上一级）。"""
    return Path(__file__).resolve().parents[1]


def resolve_main_repo() -> Path:
    """主工作树根：linked worktree 回落到主仓；解析失败退回本文件所在仓。

    与 tools/_repo_paths.py 同口径（该模块不可导入时用本文件位置兜底）。
    """
    tools_dir = str(Path(__file__).resolve().parent)
    try:
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from _repo_paths import repo_root  # noqa: PLC0415 —— 可选依赖，延迟导入
    except Exception:  # noqa: BLE001 —— 独立拷贝/未带 _repo_paths 时兜底
        return _fallback_repo()
    try:
        return Path(repo_root(Path(__file__)))
    except Exception:  # noqa: BLE001 —— 解析异常不得反噬只读检查
        return _fallback_repo()


def load_tasks(tasks_path) -> dict:
    """读取 tasks.json（只读）。缺失/不可读/非法 JSON/顶层非 dict → InputError。"""
    path = Path(tasks_path)
    if not path.is_file():
        raise InputError("tasks.json 不存在：%s" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError("tasks.json 不可读：%s（%s）" % (path, exc))
    except json.JSONDecodeError as exc:
        raise InputError("tasks.json 非法 JSON：%s（%s）" % (path, exc))
    if not isinstance(raw, dict):
        raise InputError("tasks.json 顶层必须是 dict（键=工单 ID），实际 %s"
                         % type(raw).__name__)
    return raw


def load_git_branches(repo) -> set[str]:
    """本地 git 分支短名集合（只读）。非 git 仓库/无 git → InputError（提示 --no-git）。"""
    repo = Path(repo)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "for-each-ref",
             "--format=%(refname:short)", "refs/heads"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError("git 不可用（%s）；如需跳过分支 ref 校验请加 --no-git" % exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise InputError("不是可用的 git 仓库：%s（%s）；如需跳过分支 ref 校验请加 --no-git"
                         % (repo, detail))
    return {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}


# ----------------------------------------------------------------------
# 字段解析
# ----------------------------------------------------------------------
def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def owners_of(task: dict) -> list[str]:
    """owner 归一为名单：list 展开；字符串按常见分隔符拆；空值→[]。"""
    raw = task.get("owner")
    if isinstance(raw, (list, tuple)):
        return [name for name in (_as_text(item) for item in raw) if name]
    text = _as_text(raw)
    if not text:
        return []
    for sep in OWNER_SEPARATORS:
        text = text.replace(sep, "\x00")
    return [part.strip() for part in text.split("\x00") if part.strip()]


def _explicit_abort_reason(task: dict) -> str:
    """显式 abort_reason（占位值视为缺失）。"""
    value = _as_text(task.get("abort_reason"))
    if not value or value.lower() in REASON_PLACEHOLDERS:
        return ""
    return value


def _legacy_abort_reason(task: dict) -> str:
    """历史拼写 aborted_reason（devtask 写侧已不产生，存量账本里有）。"""
    value = _as_text(task.get("aborted_reason"))
    if not value or value.lower() in REASON_PLACEHOLDERS:
        return ""
    return value


def resolve_worktree_path(value, repo) -> Path:
    """worktree 字段 → 绝对路径（相对路径按 repo 解析，不做存在性判断）。"""
    raw = _as_text(value)
    path = Path(raw)
    if not path.is_absolute():
        path = Path(repo) / path
    return Path(os.path.normpath(str(path)))


def _violation(code, *, task, status, detail, tasks=None) -> dict:
    """违规条目（JSON 结构稳定，便于每日对账消费）。"""
    group = [str(item) for item in tasks] if tasks else ([str(task)] if task else [])
    return {
        "code": code,
        "task": str(task) if task else "",
        "tasks": group,
        "status": status or "",
        "detail": detail,
    }


# ----------------------------------------------------------------------
# 核心检查（纯函数：不读文件系统之外的写路径）
# ----------------------------------------------------------------------
def check(tasks: dict, *, repo, tasks_path=None, branch_refs=None,
          git_checked=False, strict=False) -> dict:
    """扫描 tasks（dict）并返回检查报告（dict，JSON 可序列化）。"""
    repo = Path(repo)
    branch_refs = set(branch_refs or ())
    violations: list[dict] = []
    known_debt: list[dict] = []
    by_status: dict[str, int] = {}
    active: list[tuple[str, dict]] = []
    aborted: list[tuple[str, dict]] = []

    for raw_tid in sorted(tasks, key=str):
        task = tasks[raw_tid]
        tid = str(raw_tid)
        if not isinstance(task, dict):
            violations.append(_violation(
                CODE_TASK_ENTRY_INVALID, task=tid, status="",
                detail="工单值不是 dict（%s），账本结构损坏" % type(task).__name__))
            continue
        status = _as_text(task.get("status"))
        by_status[status] = by_status.get(status, 0) + 1
        if status not in KNOWN_STATUSES:
            violations.append(_violation(
                CODE_TASK_STATUS_INVALID, task=tid, status=status,
                detail="status 缺失/非法：%r（合法值：doing/review/done/aborted）"
                       % status))
            continue
        if status in ACTIVE_STATUSES:
            active.append((tid, task))
        elif status == "aborted":
            aborted.append((tid, task))
        # done：终态不检查（清场后 branch/worktree 字段可为空）

    # ---- 1) doing/review：owner / branch / worktree ----
    branch_groups: dict[str, list[str]] = {}
    worktree_groups: dict[str, list[str]] = {}
    worktree_display: dict[str, str] = {}
    for tid, task in active:
        status = _as_text(task.get("status"))
        names = owners_of(task)
        if not names:
            violations.append(_violation(
                CODE_ACTIVE_EMPTY_OWNER, task=tid, status=status,
                detail="在制工单 owner 为空（一单一 owner，必须有明确责任人）"))
        elif len(names) > 1:
            violations.append(_violation(
                CODE_ACTIVE_MULTI_OWNER, task=tid, status=status,
                detail="在制工单 owner 多值（一单一 owner）：%s" % " / ".join(names)))
        elif names[0].lower() in OWNER_PLACEHOLDERS:
            violations.append(_violation(
                CODE_ACTIVE_INVALID_OWNER, task=tid, status=status,
                detail="在制工单 owner 为占位值：%r" % names[0]))

        branch = _as_text(task.get("branch"))
        if not branch:
            violations.append(_violation(
                CODE_ACTIVE_MISSING_BRANCH, task=tid, status=status,
                detail="在制工单缺 branch 字段（新单未建 worktree 不得 doing）"))
        else:
            branch_groups.setdefault(branch, []).append(tid)
            if git_checked and branch not in branch_refs:
                violations.append(_violation(
                    CODE_ACTIVE_BRANCH_NOT_FOUND, task=tid, status=status,
                    detail="branch=%r 在本地 git 分支中不存在" % branch))

        worktree = _as_text(task.get("worktree"))
        if not worktree:
            violations.append(_violation(
                CODE_ACTIVE_MISSING_WORKTREE, task=tid, status=status,
                detail="在制工单缺 worktree 字段（新单未建 worktree 不得 doing）"))
        else:
            wt_path = resolve_worktree_path(worktree, repo)
            if not wt_path.is_dir():
                violations.append(_violation(
                    CODE_ACTIVE_WORKTREE_NOT_FOUND, task=tid, status=status,
                    detail="worktree 目录不存在：%s" % wt_path))
            else:
                key = os.path.normcase(str(wt_path))
                worktree_groups.setdefault(key, []).append(tid)
                worktree_display.setdefault(key, str(wt_path))

    for branch, tids in sorted(branch_groups.items()):
        if len(tids) > 1:
            violations.append(_violation(
                CODE_ACTIVE_DUPLICATE_BRANCH, task=tids[0], status="",
                tasks=tids,
                detail="在制工单共用同一 branch %r：%s" % (branch, ", ".join(tids))))
    for key, tids in sorted(worktree_groups.items()):
        if len(tids) > 1:
            violations.append(_violation(
                CODE_ACTIVE_DUPLICATE_WORKTREE, task=tids[0], status="",
                tasks=tids,
                detail="在制工单共用同一 worktree 路径 %s：%s"
                       % (worktree_display[key], ", ".join(tids))))

    # ---- 2) aborted：必须有原因（显式 abort_reason 为准） ----
    missing_reason: list[str] = []
    no_reason_signal: list[str] = []
    for tid, task in aborted:
        explicit = _explicit_abort_reason(task)
        if explicit:
            continue
        missing_reason.append(tid)
        note = _as_text(task.get("note"))
        fallback = _legacy_abort_reason(task)
        if note or fallback:
            source = "note" if note else "历史拼写 aborted_reason"
            known_debt.append(_violation(
                CODE_ABORTED_MISSING_ABORT_REASON, task=tid, status="aborted",
                detail="缺显式 abort_reason（有 %s 兜底；存量欠账只计数，不代写）"
                       % source))
        else:
            no_reason_signal.append(tid)
            violations.append(_violation(
                CODE_ABORTED_NO_REASON, task=tid, status="aborted",
                detail="aborted 无任何原因（abort_reason/note 均空）"))

    # ---- 3) 严格模式：缺显式 abort_reason 的存量升级为阻断违规 ----
    if strict:
        for item in known_debt:
            promoted = dict(item)
            promoted["tasks"] = list(item["tasks"])
            promoted["detail"] = item["detail"] + "（严格模式 --fail-on-abort-reason 计为违规）"
            violations.append(promoted)

    violations.sort(key=lambda item: (item["code"], item["task"], item["detail"]))
    known_debt.sort(key=lambda item: (item["code"], item["task"]))
    aborted_total = len(aborted)
    counts = {
        "tasks_total": len(tasks),
        "by_status": {key: by_status[key] for key in sorted(by_status)},
        "active": len(active),
        "doing": by_status.get("doing", 0),
        "review": by_status.get("review", 0),
        "done": by_status.get("done", 0),
        "aborted": aborted_total,
        "aborted_with_abort_reason": aborted_total - len(missing_reason),
        "aborted_missing_abort_reason": len(missing_reason),
        "aborted_no_reason_signal": len(no_reason_signal),
        "violations": len(violations),
        "known_debt": len(known_debt),
    }
    verdict = "PASS" if not violations else "FAIL"
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(repo),
        "tasks_path": str(tasks_path or ""),
        "mode": "strict_abort_reason" if strict else "default",
        "git": {"checked": bool(git_checked), "branch_count": len(branch_refs)},
        "checks": {
            "active_owner": True,
            "active_branch_field": True,
            "active_branch_ref": bool(git_checked),
            "active_worktree_field": True,
            "active_worktree_path": True,
            "active_duplicate_branch": True,
            "active_duplicate_worktree": True,
            "aborted_reason": True,
            "strict_abort_reason": bool(strict),
        },
        "counts": counts,
        "violations": violations,
        "known_debt": known_debt,
        "verdict": verdict,
    }


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------
def _md_cell(text) -> str:
    """markdown 表格单元转义（防竖线/换行破坏表格）。"""
    return _as_text(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_markdown(result: dict) -> str:
    """报告 → markdown（默认 stdout 口径）。"""
    counts = result["counts"]
    lines = ["# W2 工作树/abort 不变量 · 只读检查", ""]
    lines.append("- 生成时间：%s" % result["generated_at"])
    lines.append("- 数据源：`%s`（只读，不改写）" % result["tasks_path"])
    lines.append("- 仓库：`%s`" % result["repo"])
    if result["git"]["checked"]:
        lines.append("- git 分支校验：已启用（本地分支 %d 个）"
                     % result["git"]["branch_count"])
    else:
        lines.append("- git 分支校验：已跳过（--no-git，仅校验字段/目录存在）")
    if result["mode"] == "strict_abort_reason":
        lines.append("- 模式：**严格**（--fail-on-abort-reason：aborted 缺显式 "
                     "abort_reason 计违规）")
    else:
        lines.append("- 模式：默认（aborted 缺显式 abort_reason 仅计 known_debt，"
                     "不阻断；严格模式加 --fail-on-abort-reason）")
    lines.append("")
    lines.append("## 判定")
    lines.append("")
    exit_code = 0 if result["verdict"] == "PASS" else 1
    lines.append("**%s**（退出码 %d）：阻断违规 %d 条 / 已知欠账 %d 张"
                 % (result["verdict"], exit_code, counts["violations"],
                    counts["known_debt"]))
    lines.append("")
    lines.append("## 计数")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---:|")
    rows = [
        ("工单总数", counts["tasks_total"]),
        ("doing", counts["doing"]),
        ("review", counts["review"]),
        ("在制合计（doing+review）", counts["active"]),
        ("done", counts["done"]),
        ("aborted", counts["aborted"]),
        ("aborted 有显式 abort_reason", counts["aborted_with_abort_reason"]),
        ("aborted 缺显式 abort_reason", counts["aborted_missing_abort_reason"]),
        ("其中完全无原因（note 也空）", counts["aborted_no_reason_signal"]),
        ("阻断违规", counts["violations"]),
        ("已知欠账（不阻断）", counts["known_debt"]),
    ]
    for label, value in rows:
        lines.append("| %s | %d |" % (label, value))
    lines.append("")
    lines.append("## 阻断违规（%d）" % counts["violations"])
    lines.append("")
    if result["violations"]:
        lines.append("| # | code | 工单 | status | 说明 |")
        lines.append("|---:|---|---|---|---|")
        for index, item in enumerate(result["violations"], 1):
            lines.append("| %d | `%s` | %s | %s | %s |" % (
                index, item["code"], _md_cell(item["task"]),
                _md_cell(item["status"]), _md_cell(item["detail"])))
    else:
        lines.append("无。")
    lines.append("")
    lines.append("## 已知欠账（不阻断，%d）" % counts["known_debt"])
    lines.append("")
    if result["known_debt"]:
        lines.append("| # | code | 工单 | 说明 |")
        lines.append("|---:|---|---|---|")
        for index, item in enumerate(result["known_debt"], 1):
            lines.append("| %d | `%s` | %s | %s |" % (
                index, item["code"], _md_cell(item["task"]),
                _md_cell(item["detail"])))
    else:
        lines.append("无。")
    lines.append("")
    lines.append("## 口径")
    lines.append("")
    lines.append("- doing/review：owner 非空/非占位/单值；branch 字段非空且本地 git "
                 "分支存在；worktree 字段非空且目录存在；在制单之间 branch/worktree "
                 "不得重复。")
    lines.append("- aborted：abort_reason 或 note（含历史拼写 aborted_reason）非空才"
                 "不违规；仅缺显式 abort_reason 计入 known_debt，"
                 "--fail-on-abort-reason 严格模式升级为违规。")
    lines.append("- done 不检查（终态已清场）；status 缺失/非法计违规。")
    lines.append("- 只读：不修改 tasks.json、不新建/关闭/改派工单；报告仅在显式 "
                 "--json-out/--md-out 时落盘。")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="W2 工作树/abort 不变量只读检查器（不写回 tasks.json）")
    ap.add_argument("--tasks", default="",
                    help="tasks.json 路径（默认 <repo>/.worktrees/tasks.json）")
    ap.add_argument("--repo", default="",
                    help="仓库主工作树根（默认自动解析：linked worktree 回落主仓）")
    ap.add_argument("--json", action="store_true",
                    help="stdout 输出 JSON（默认输出 markdown）")
    ap.add_argument("--json-out", default="", help="JSON 报告落盘路径（显式才写）")
    ap.add_argument("--md-out", default="", help="markdown 报告落盘路径（显式才写）")
    ap.add_argument("--fail-on-abort-reason", action="store_true",
                    help="严格模式：aborted 缺显式 abort_reason 即算违规（默认仅计"
                         " known_debt 不阻断）")
    ap.add_argument("--no-git", action="store_true",
                    help="跳过 git 分支 ref 校验（非 git 环境/离线对账用）")
    args = ap.parse_args(argv)

    repo = Path(args.repo) if args.repo else resolve_main_repo()
    tasks_path = Path(args.tasks) if args.tasks else repo / ".worktrees" / "tasks.json"

    try:
        tasks = load_tasks(tasks_path)
        branch_refs: set[str] = set()
        git_checked = False
        if not args.no_git:
            branch_refs = load_git_branches(repo)
            git_checked = True
    except InputError as exc:
        print("[ERR] %s" % exc, file=sys.stderr)
        return 2

    result = check(
        tasks,
        repo=repo,
        tasks_path=tasks_path,
        branch_refs=branch_refs,
        git_checked=git_checked,
        strict=args.fail_on_abort_reason,
    )
    markdown = render_markdown(result)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"

    for target, content in ((args.json_out, payload), (args.md_out, markdown)):
        if not target:
            continue
        path = Path(target)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            print("[ERR] 报告写入失败：%s（%s）" % (path, exc), file=sys.stderr)
            return 2
        print("[OK] 报告已写入：%s" % path, file=sys.stderr)

    sys.stdout.write(payload if args.json else markdown)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
