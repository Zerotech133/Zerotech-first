# -*- coding: utf-8 -*-
"""日终三账快照（任务 / 计划 / 证据，只读一条命令）。

用途：17:00 日终与每日例检，把「任务账、计划账、证据账」聚合成一份可复核快照：

  python -X utf8 tools/daily_three_ledger.py                 # 跑三账 → deliverables/<date>_日终三账快照_v1.md/.json
  python -X utf8 tools/daily_three_ledger.py --json          # 同上，stdout 输出机器可读 JSON
  python -X utf8 tools/daily_three_ledger.py --markdown      # stdout 输出 Markdown（默认）
  python -X utf8 tools/daily_three_ledger.py --out C:\\Temp\\snap --no-write
  # 离线/复算：直接复用已捕获 JSON（不跑子进程；也用于单测 mock）
  python -X utf8 tools/daily_three_ledger.py --tasks-audit-json tba.json --evidence-json pack.json \
      --date 2026-10-31 --json

输入（全部只读）：
  1) 任务账：`.worktrees/tasks.json`（统计 total/doing/review/done/aborted、缺 owner）
     + `tools/task_board_audit.py --json`（漂移分类：merged_but_open / ahead_unmerged /
     branch_missing / worktree_missing / worktree_dirty / clean）；
  2) 计划账：`docs/plans/**/*.md` 的 `> STATUS:` 覆盖（真实计划数 / 有 STATUS /
     unspecified / 状态分布），不修改任何文件；
  3) 证据账：`tools/m1_evidence_pack.py --json --no-write`（M1 信任链证据包；必需判据
     M1-KPI / W1-DRILL / W2-INVARIANT），从中派生 M1 KPI 摘要与 M4 派生状态。

判定口径（每本账）：
  - 任务账：tasks.json 可读且漂移=0 → pass；漂移>0 → fail；tasks.json/审计不可读 → unknown；
  - 计划账：真实计划 unspecified=0（覆盖率 100%）→ pass；unspecified>0 → fail；目录缺失 → unknown；
  - 证据账：m1 pack verdict=pass → pass；fail → fail；unknown/工具失败/非 JSON → unknown。
总判定：任一账 fail → fail（退出码 1）；无 fail 但有 unknown → unknown（退出码 2）；全 pass → pass（退出码 0）。
**失败项一律标 unknown 并保留错误原文，不伪造数值**。

安全边界：只读；不写任务板/计划/approvals/tasks.json、不重启服务、不代签、不跑生产回填。
输出只写 `<out>/<date>_日终三账快照_v1.md` 与 `.json` 两个文件（`--no-write` 时零写入）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "daily-three-ledger/v1"
REPO = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = REPO / "deliverables"
DEFAULT_TIMEOUT = 240.0
TASK_AUDIT_TIMEOUT = 120.0

STATUS_RE = re.compile(r"(?im)^\s*>\s*STATUS\s*[:：]\s*([^\n]{0,200})")
NON_PLAN_FILES = {"README.md", "PLAN_REGISTER_20261031.md", "PLAN_REGISTER_ALL_20261031.md"}
DRIFT_CATEGORIES = ("merged_but_open", "ahead_unmerged", "branch_missing",
                    "worktree_missing", "worktree_dirty")
LEDGER_STATUS = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
NOTES = (
    "只读聚合：不写任务板/计划/approvals/tasks.json、不重启服务、不代签、不跑生产回填",
    "失败项（工具缺失/超时/输出非 JSON/文件不可读）一律标 unknown 并保留错误原文，不伪造数值",
    "任务账=任务账本统计+漂移分类；计划账=docs/plans STATUS 覆盖；证据账=M1 证据包派生",
    "本快照不替代 owner 的关单/补状态动作；缺口需按 owner 清单人工处理",
)


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def ensure_utf8_stdout() -> None:
    """Windows 控制台默认 cp936：统一 UTF-8，避免中文报告乱码/写失败。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 非标准流
            pass


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=15)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, (proc.stdout or "").strip()


def git_info(repo: Path) -> dict:
    """commit 短 sha / 分支；非 git 目录或 git 不可用 → 全部 unknown（不伪造）。"""
    rc, out = _git(repo, "rev-parse", "--short", "HEAD")
    commit = out if rc == 0 and out else ""
    rc_b, branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return {"commit": commit or "", "branch": (branch if rc_b == 0 else "") or "",
            "known": bool(commit)}


def resolve_main_repo(repo: Path) -> Path:
    """worktree 内运行时，任务账/工具以主仓为准（git common dir 的父目录）。"""
    repo = Path(repo)
    if not (repo / ".git").exists():
        return repo
    try:
        proc = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                               "--git-common-dir"], cwd=str(repo),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            common = Path((proc.stdout or "").strip())
            if common.name == ".git":
                return common.parent
    except (OSError, subprocess.SubprocessError):
        pass
    return repo


def _run_json_tool(name: str, script: Path, args: list[str], timeout: float,
                   cwd: Path) -> dict:
    """子进程跑 ``<tool> --json [args]``；有合法 JSON stdout 即视为采集成功（退出码以报告为准）。"""
    meta = {"name": name, "mode": "subprocess", "path": str(script), "report": None,
            "ok": False, "error": "", "exit_code": None, "duration_ms": 0}
    script = Path(script)
    if not script.is_file():
        meta["error"] = "工具不存在：%s" % script
        return meta
    cmd = [sys.executable, "-X", "utf8", str(script), "--json", *args]
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        meta["error"] = "工具超时（>%ss）：%s" % (timeout, Path(script).name)
        return meta
    except OSError as exc:
        meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        meta["error"] = "工具无法执行：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
    meta["exit_code"] = proc.returncode
    stdout = (proc.stdout or "").strip()
    if not stdout:
        meta["error"] = "工具无 JSON 输出（exit=%s）：%s" % (
            proc.returncode, (proc.stderr or "").strip()[-200:] or "-")
        return meta
    try:
        meta["report"] = json.loads(stdout)
    except ValueError as exc:
        meta["error"] = "工具 stdout 非 JSON（exit=%s）：%s: %s" % (
            proc.returncode, type(exc).__name__, str(exc)[:120])
        return meta
    meta["ok"] = True
    return meta


def _load_json_file(name: str, path) -> dict:
    """读取已捕获 JSON 报告（mock/离线复用）；读不到 → 结构化失败（不抛）。"""
    p = Path(path)
    meta = {"name": name, "mode": "file", "path": str(p), "report": None,
            "ok": False, "error": "", "exit_code": None, "duration_ms": 0}
    if not p.is_file():
        meta["error"] = "报告文件不存在：%s" % p
        return meta
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        meta["error"] = "报告文件不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    try:
        meta["report"] = json.loads(text)
    except ValueError as exc:
        meta["error"] = "报告文件非 JSON：%s（%s）" % (p, type(exc).__name__)
        return meta
    meta["ok"] = True
    return meta


def _source_meta(src: dict) -> dict:
    report = src.get("report")
    schema = report.get("schema") if isinstance(report, dict) else None
    return {"name": src.get("name"), "mode": src.get("mode"),
            "path": src.get("path"), "ok": bool(src.get("ok")),
            "error": src.get("error") or "", "exit_code": src.get("exit_code"),
            "duration_ms": src.get("duration_ms"), "schema": schema}


# ---------------------------------------------------------------------------
# 任务账
# ---------------------------------------------------------------------------
def read_tasks_counts(tasks_path: Path) -> dict:
    """读 tasks.json 统计（只读；失败抛 OSError/ValueError 由调用方标 unknown）。"""
    tasks = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    if not isinstance(tasks, dict):
        raise ValueError("tasks.json 顶层不是对象")
    by_status: dict[str, int] = {}
    missing_owner = 0
    for item in tasks.values():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "?")
        by_status[status] = by_status.get(status, 0) + 1
        owner = str(item.get("owner") or "").strip()
        if owner in ("", "unknown-agent", "[UNOWNED]"):
            missing_owner += 1
    doing = by_status.get("doing", 0)
    review = by_status.get("review", 0)
    return {"tasks_total": len(tasks), "by_status": by_status,
            "doing": doing, "review": review, "open_doing_review": doing + review,
            "done": by_status.get("done", 0), "aborted": by_status.get("aborted", 0),
            "missing_owner": missing_owner}


def collect_task_ledger(repo=None, tasks_path=None, audit_json=None,
                        timeout: float = TASK_AUDIT_TIMEOUT) -> dict:
    """任务账：tasks.json 统计 + task_board_audit 漂移分类（只读）。"""
    repo = Path(repo or REPO)
    tp = Path(tasks_path) if tasks_path else (repo / ".worktrees" / "tasks.json")
    result = {"ledger": "task", "status": "unknown", "counts": {},
              "drift": {}, "gaps": [], "source": {}, "error": ""}
    try:
        counts = read_tasks_counts(tp)
    except (OSError, ValueError) as exc:
        result["error"] = "tasks.json 不可读：%s: %s" % (type(exc).__name__, str(exc)[:200])
        result["source"] = {"tasks_path": str(tp), "audit": None}
        return result
    result["counts"] = counts
    if audit_json:
        audit_src = _load_json_file("task_board_audit", audit_json)
    else:
        audit_src = _run_json_tool("task_board_audit",
                                   TOOLS_DIR / "task_board_audit.py", [],
                                   timeout, cwd=repo)
    result["source"] = {"tasks_path": str(tp),
                        "audit": _source_meta(audit_src)}
    report = audit_src.get("report") if audit_src.get("ok") else None
    if not isinstance(report, dict):
        result["error"] = audit_src.get("error") or "task_board_audit 报告缺失"
        return result
    if not report.get("ok"):
        result["error"] = "task_board_audit 返回 ok=false：%s" % (
            str(report.get("error") or "")[:200] or "-")
        return result
    summary = {str(k): int(v) for k, v in (report.get("summary") or {}).items()}
    drift_total = sum(n for cat, n in summary.items() if cat not in ("clean",))
    rows = []
    for row in report.get("rows") or []:
        category = str(row.get("category") or "?")
        if category in ("clean",):
            continue
        rows.append({"tid": row.get("tid"), "status": row.get("status"),
                     "owner": row.get("owner"), "category": category,
                     "ahead": row.get("ahead"),
                     "worktree_exists": row.get("worktree_exists"),
                     "dirty": row.get("dirty")})
    result["drift"] = {"by_category": summary, "drift_total": drift_total,
                       "rows_total": len(report.get("rows") or [])}
    result["gaps"] = rows[:60]
    result["status"] = "fail" if drift_total > 0 else "pass"
    if len(rows) > 60:
        result["gaps_truncated"] = len(rows) - 60
    return result


# ---------------------------------------------------------------------------
# 计划账
# ---------------------------------------------------------------------------
def scan_plans(plans_dir: Path) -> dict:
    """扫描 docs/plans/**/*.md 的 STATUS 覆盖（只读，不写文件）。"""
    files = sorted(Path(plans_dir).rglob("*.md"))
    plan_docs, with_status, unspecified = 0, 0, []
    by_status: dict[str, int] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.name in NON_PLAN_FILES:
            continue
        plan_docs += 1
        match = STATUS_RE.search(text[:6000])
        raw = (match.group(1).strip() if match else "")
        low = raw.lower()
        if match:
            with_status += 1
        if low.startswith("superseded") or "superseded" in low:
            cat = "superseded"
        elif low.startswith("pending"):
            cat = "pending_approval"
        elif low.startswith("frozen"):
            cat = "frozen"
        elif low.startswith("unspecified") or not match:
            cat = "unspecified"
        elif low.startswith("done"):
            cat = "done"
        elif low.startswith("proposed"):
            cat = "proposed"
        elif low.startswith("declared") or match:
            cat = "declared"
        else:  # pragma: no cover - 兜底
            cat = "other"
        by_status[cat] = by_status.get(cat, 0) + 1
        if cat == "unspecified":
            unspecified.append(str(path.relative_to(Path(plans_dir)).as_posix()))
    coverage = round((with_status / plan_docs) * 100, 2) if plan_docs else 0.0
    return {"files_total": len(files), "plan_docs": plan_docs,
            "with_status": with_status, "unspecified": len(unspecified),
            "unspecified_files": unspecified[:30],
            "coverage_pct": coverage, "by_status": by_status}


def collect_plan_ledger(plans_dir=None, repo=None) -> dict:
    """计划账：真实计划 STATUS 覆盖（只读）。"""
    directory = Path(plans_dir or (Path(repo or REPO) / "docs" / "plans"))
    result = {"ledger": "plan", "status": "unknown", "counts": {},
              "gaps": [], "source": {"plans_dir": str(directory)}, "error": ""}
    if not directory.is_dir():
        result["error"] = "计划目录不存在：%s" % directory
        return result
    try:
        counts = scan_plans(directory)
    except OSError as exc:
        result["error"] = "计划目录不可读：%s: %s" % (type(exc).__name__, str(exc)[:200])
        return result
    result["counts"] = counts
    if counts["plan_docs"] == 0:
        result["error"] = "计划目录内无计划文档"
        return result
    result["gaps"] = [{"file": f} for f in counts["unspecified_files"]]
    result["status"] = "pass" if counts["unspecified"] == 0 else "fail"
    return result


# ---------------------------------------------------------------------------
# 证据账
# ---------------------------------------------------------------------------
def collect_evidence_ledger(repo=None, evidence_json=None,
                            timeout: float = DEFAULT_TIMEOUT) -> dict:
    """证据账：调用 m1_evidence_pack --json --no-write（或复用已捕获 JSON）。"""
    repo = Path(repo or REPO)
    result = {"ledger": "evidence", "status": "unknown", "pack": {},
              "gaps": [], "source": {}, "error": ""}
    if evidence_json:
        src = _load_json_file("m1_evidence_pack", evidence_json)
    else:
        main_repo = resolve_main_repo(repo)
        src = _run_json_tool("m1_evidence_pack",
                             Path(main_repo) / "tools" / "m1_evidence_pack.py",
                             ["--no-write"], timeout, cwd=main_repo)
    result["source"] = _source_meta(src)
    report = src.get("report") if src.get("ok") else None
    if not isinstance(report, dict):
        result["error"] = src.get("error") or "m1_evidence_pack 报告缺失"
        return result
    verdict = str(report.get("verdict") or "unknown").lower()
    if verdict not in ("pass", "fail", "unknown"):
        result["error"] = "m1_evidence_pack verdict 非法：%r" % verdict
        return result
    criteria = []
    for item in report.get("criteria") or []:
        if not isinstance(item, dict):
            continue
        criteria.append({"id": str(item.get("id") or "?"),
                         "required": bool(item.get("required", True)),
                         "status": str(item.get("status") or "unknown"),
                         "detail": str(item.get("detail") or "")[:240]})
    services = report.get("services") or {}
    kpi_detail = next((c["detail"] for c in criteria if c["id"] == "M1-KPI"), "")
    result["pack"] = {
        "verdict": verdict,
        "counts": report.get("counts") or {},
        "verdict_reasons": report.get("verdict_reasons") or {},
        "criteria": criteria,
        "kpi_summary": {"m1_kpi": kpi_detail,
                        "m4_signoff": (services.get("m4") or {}).get("status"),
                        "m4_detail": (services.get("m4") or {}).get("detail")},
        "gaps": report.get("gaps") or [],
    }
    result["gaps"] = [c for c in criteria if c["status"] != "pass"]
    result["status"] = verdict
    if verdict == "unknown" and not result["error"]:
        result["error"] = str(report.get("error") or "m1_evidence_pack unknown")
    return result


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def build_snapshot(date=None, repo=None, tasks_path=None, plans_dir=None,
                   audit_json=None, evidence_json=None,
                   timeout: float = DEFAULT_TIMEOUT,
                   now: dt.datetime | None = None) -> dict:
    """聚合三账快照（只读；不写任何文件）。失败项标 unknown，不伪造。"""
    started = time.perf_counter()
    now = now or dt.datetime.now().astimezone()
    repo = Path(repo or REPO)
    if date:
        try:
            day = dt.datetime.strptime(str(date), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("--date 需为 YYYY-MM-DD：%r" % date)
    else:
        day = now.date()
    task = collect_task_ledger(repo=repo, tasks_path=tasks_path,
                               audit_json=audit_json)
    plan = collect_plan_ledger(plans_dir=plans_dir, repo=repo)
    evidence = collect_evidence_ledger(repo=repo, evidence_json=evidence_json,
                                       timeout=timeout)
    ledgers = [task, plan, evidence]
    statuses = [item["status"] for item in ledgers]
    if "fail" in statuses:
        verdict = "fail"
    elif "unknown" in statuses:
        verdict = "unknown"
    else:
        verdict = "pass"
    counts = {"ledgers_total": len(ledgers),
              "pass": statuses.count("pass"),
              "fail": statuses.count("fail"),
              "unknown": statuses.count("unknown")}
    gaps = []
    for item in ledgers:
        for gap in item.get("gaps") or []:
            gaps.append({"ledger": item["ledger"], "status": item["status"], **gap})
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "date": day.isoformat(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "repo": str(repo),
        "host": {"os": os.name, "python": sys.version.split()[0]},
        "commit": git_info(repo),
        "verdict": verdict,
        "passed": verdict == "pass",
        "counts": counts,
        "ledgers": {"task": task, "plan": plan, "evidence": evidence},
        "gaps": gaps,
        "paths": {"tasks_json": task.get("source", {}).get("tasks_path"),
                  "plans_dir": plan.get("source", {}).get("plans_dir"),
                  "evidence_source": evidence.get("source", {}).get("path")},
        "notes": list(NOTES),
    }


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def _md_cell(text) -> str:
    return str(text if text is not None else "-").replace("|", "\\|").replace("\n", " ").strip()


def _label(status) -> str:
    return LEDGER_STATUS.get(str(status), "UNKNOWN")


def render_markdown(snap: dict) -> str:
    """Markdown 快照（日报/评审可直接引用）。"""
    commit = snap.get("commit") or {}
    counts = snap.get("counts") or {}
    ledgers = snap.get("ledgers") or {}
    lines = [
        "# 日终三账快照（任务 / 计划 / 证据）v1",
        "",
        "- 生成时间：%s" % snap.get("generated_at"),
        "- 日期：%s ｜ 仓库：`%s`" % (snap.get("date"), snap.get("repo")),
        "- commit：`%s`（%s）｜ 主机：os=%s · python=%s" % (
            commit.get("commit") or "-", commit.get("branch") or "-",
            (snap.get("host") or {}).get("os"), (snap.get("host") or {}).get("python")),
        "",
        "**判定：%s**（三账 pass %s / fail %s / unknown %s，共 %s）" % (
            _label(snap.get("verdict")), counts.get("pass"), counts.get("fail"),
            counts.get("unknown"), counts.get("ledgers_total")),
        "",
        "## 三账汇总",
        "",
        "| 账 | 状态 | 关键数字 | 缺口数 |",
        "|---|---|---|---:|",
    ]

    task = ledgers.get("task") or {}
    task_counts = task.get("counts") or {}
    task_drift = task.get("drift") or {}
    task_key = "total=%s · doing/review=%s/%s · drift=%s" % (
        task_counts.get("tasks_total", "-"), task_counts.get("doing", "-"),
        task_counts.get("review", "-"), task_drift.get("drift_total", "unknown"))
    plan = ledgers.get("plan") or {}
    plan_counts = plan.get("counts") or {}
    plan_key = "plan=%s · STATUS=%s · unspecified=%s · coverage=%s%%" % (
        plan_counts.get("plan_docs", "unknown"), plan_counts.get("with_status", "unknown"),
        plan_counts.get("unspecified", "unknown"), plan_counts.get("coverage_pct", "unknown"))
    evidence = ledgers.get("evidence") or {}
    pack = evidence.get("pack") or {}
    ev_counts = pack.get("counts") or {}
    ev_key = "pack=%s · pass/fail=%s/%s" % (
        _label(pack.get("verdict") or evidence.get("status")),
        ev_counts.get("pass", "unknown"), ev_counts.get("fail", "unknown"))
    lines += [
        "| 任务账 | %s | %s | %s |" % (_label(task.get("status")), task_key,
                                      len(task.get("gaps") or [])),
        "| 计划账 | %s | %s | %s |" % (_label(plan.get("status")), plan_key,
                                      len(plan.get("gaps") or [])),
        "| 证据账 | %s | %s | %s |" % (_label(evidence.get("status")), ev_key,
                                      len(evidence.get("gaps") or [])),
        "",
        "## 任务账（tasks.json + task_board_audit）",
        "",
    ]
    if task.get("counts"):
        lines += ["| 指标 | 值 |", "|---|---:|",
                  "| 工单总数 | %s |" % task_counts.get("tasks_total"),
                  "| doing / review | %s / %s |" % (task_counts.get("doing"),
                                                    task_counts.get("review")),
                  "| done / aborted | %s / %s |" % (task_counts.get("done"),
                                                    task_counts.get("aborted")),
                  "| 缺 owner（空/unknown-agent） | %s |" % task_counts.get("missing_owner"),
                  "| 漂移合计 | %s |" % task_drift.get("drift_total", "UNKNOWN")]
        drift = task_drift.get("by_category") or {}
        if drift:
            lines += ["", "| 漂移分类 | 数量 |", "|---|---:|"]
            for cat in DRIFT_CATEGORIES + ("clean",):
                if cat in drift:
                    lines.append("| %s | %s |" % (cat, drift[cat]))
        rows = task.get("gaps") or []
        if rows:
            lines += ["", "| 工单 | 状态 | owner | 漂移分类 |", "|---|---|---|---|"]
            for row in rows:
                lines.append("| %s | %s | %s | %s |" % (
                    _md_cell(row.get("tid")), _md_cell(row.get("status")),
                    _md_cell(row.get("owner")), _md_cell(row.get("category"))))
            if task.get("gaps_truncated"):
                lines.append("")
                lines.append("> 另有 %s 条漂移行未列出（见 JSON）。" % task["gaps_truncated"])
        if task.get("error"):
            lines += ["", "> 任务账 unknown：%s" % _md_cell(task["error"])]
    else:
        lines += ["> 任务账 UNKNOWN：%s" % _md_cell(task.get("error") or "tasks.json 缺失")]

    lines += ["", "## 计划账（docs/plans STATUS 覆盖）", ""]
    if plan.get("counts"):
        lines += ["| 指标 | 值 |", "|---|---:|",
                  "| .md 文件总数 | %s |" % plan_counts.get("files_total"),
                  "| 真实计划文档 | %s |" % plan_counts.get("plan_docs"),
                  "| 有显式 `> STATUS:` | %s |" % plan_counts.get("with_status"),
                  "| unspecified | %s |" % plan_counts.get("unspecified"),
                  "| 覆盖率 | %s%% |" % plan_counts.get("coverage_pct")]
        by_status = plan_counts.get("by_status") or {}
        if by_status:
            lines += ["", "| 状态 | 数量 |", "|---|---:|"]
            for key in sorted(by_status):
                lines.append("| %s | %s |" % (key, by_status[key]))
        files = plan_counts.get("unspecified_files") or []
        if files:
            lines += ["", "未标注计划（前 %d）：" % len(files), ""]
            for item in files:
                lines.append("- `%s`" % _md_cell(item))
        if plan.get("error"):
            lines += ["", "> 计划账 unknown：%s" % _md_cell(plan["error"])]
    else:
        lines += ["> 计划账 UNKNOWN：%s" % _md_cell(plan.get("error") or "计划目录缺失")]

    lines += ["", "## 证据账（M1 evidence pack 派生）", ""]
    if evidence.get("pack"):
        kpi = pack.get("kpi_summary") or {}
        reasons = pack.get("verdict_reasons") or {}
        lines += ["| 指标 | 值 |", "|---|---|",
                  "| M1 pack verdict | %s |" % _label(pack.get("verdict")),
                  "| M1 KPI | %s |" % _md_cell(kpi.get("m1_kpi") or "-"),
                  "| M4 签字（派生） | %s（%s） |" % (
                      _md_cell(kpi.get("m4_signoff")), _md_cell(kpi.get("m4_detail"))),
                  "| required fail | %s |" % _md_cell(
                      ", ".join(reasons.get("required_fail") or []) or "无"),
                  "| required unknown | %s |" % _md_cell(
                      ", ".join(reasons.get("required_unknown") or []) or "无"),
                  "| advisory 非 pass | %s |" % _md_cell(
                      ", ".join(reasons.get("advisory_not_pass") or []) or "无")]
        criteria = pack.get("criteria") or []
        if criteria:
            lines += ["", "| 判据 | 必需 | 状态 | 摘要 |", "|---|---|---|---|"]
            for item in criteria:
                lines.append("| %s | %s | %s | %s |" % (
                    _md_cell(item.get("id")), "是" if item.get("required") else "否",
                    _label(item.get("status")), _md_cell(item.get("detail"))))
        if evidence.get("error"):
            lines += ["", "> 证据账 unknown：%s" % _md_cell(evidence["error"])]
    else:
        lines += ["> 证据账 UNKNOWN：%s" % _md_cell(evidence.get("error") or "m1 pack 缺失")]

    lines += ["", "## unknown 与缺口清单", ""]
    gaps = snap.get("gaps") or []
    if gaps:
        lines += ["| # | 账 | 对象 | 说明 |", "|---|---|---|---|"]
        for index, gap in enumerate(gaps, 1):
            target = gap.get("file") or gap.get("tid") or gap.get("id") or "-"
            detail = gap.get("detail") or gap.get("category") or ""
            if gap.get("file"):
                detail = "计划缺 STATUS（unspecified）"
            lines.append("| %d | %s | %s | %s |" % (
                index, _md_cell(gap.get("ledger")), _md_cell(target),
                _md_cell(detail)))
    else:
        lines.append("无缺口（三账全部 PASS）。")

    lines += ["", "## 输入源采集", "",
              "| 源 | 模式 | 状态 | 耗时(ms) | 路径/说明 |", "|---|---|---|---:|---|"]
    for key in ("task", "plan", "evidence"):
        item = ledgers.get(key) or {}
        src = item.get("source") or {}
        if key == "task":
            audit = src.get("audit") or {}
            lines.append("| 任务账 | %s | %s | %s | `%s` / audit=%s |" % (
                _md_cell(audit.get("mode") or "file"),
                "OK" if audit.get("ok") else "FAIL",
                _md_cell(audit.get("duration_ms")),
                _md_cell(src.get("tasks_path")),
                _md_cell(audit.get("path") if audit.get("ok") else
                         (audit.get("error") or "-"))))
        elif key == "plan":
            lines.append("| 计划账 | file | %s | - | `%s` |" % (
                "OK" if item.get("status") != "unknown" else "FAIL",
                _md_cell(src.get("plans_dir"))))
        else:
            lines.append("| 证据账 | %s | %s | %s | `%s` |" % (
                _md_cell(src.get("mode")), "OK" if src.get("ok") else "FAIL",
                _md_cell(src.get("duration_ms")),
                _md_cell(src.get("path") if src.get("ok") else
                         (src.get("error") or "-"))))
    if snap.get("paths"):
        lines += ["", "## 路径", ""]
        for key, value in (snap.get("paths") or {}).items():
            lines.append("- %s：`%s`" % (key, value))

    lines += ["", "## 说明", ""]
    for note in snap.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


def output_paths(out_dir, date=None) -> tuple[Path, Path]:
    """``<out>/<date>_日终三账快照_v1.md/.json``（date 形如 YYYY-MM-DD）。"""
    stamp = str(date).strip() or dt.date.today().isoformat()
    directory = Path(out_dir)
    return (directory / ("%s_日终三账快照_v1.md" % stamp),
            directory / ("%s_日终三账快照_v1.json" % stamp))


def write_snapshot(snap: dict, out_dir=None) -> dict:
    """把快照写入 markdown + json（只写这两个文件，不写其它任何东西）。"""
    md_path, json_path = output_paths(out_dir or DEFAULT_OUT_DIR, snap.get("date"))
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(snap) + "\n", encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="日终三账快照（任务/计划/证据，只读；生成 deliverables/<date>_日终三账快照_v1.md/.json）")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="stdout 输出 JSON（文件仍会生成）")
    output.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    parser.add_argument("--out", default="", help="输出目录（默认 <repo>/deliverables）")
    parser.add_argument("--no-write", action="store_true", help="只打印，不写 deliverables 文件")
    parser.add_argument("--date", default="", help="快照日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--repo", default="", help="仓库根（默认工具所在仓库）")
    parser.add_argument("--tasks", default="", help="tasks.json 路径（默认 <repo>/.worktrees/tasks.json）")
    parser.add_argument("--plans-dir", default="", help="计划目录（默认 <repo>/docs/plans）")
    parser.add_argument("--tasks-audit-json", default="",
                        help="复用已捕获 task_board_audit --json 报告（离线/单测 mock）")
    parser.add_argument("--evidence-json", default="",
                        help="复用已捕获 m1_evidence_pack --json 报告（离线/单测 mock）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="证据账子进程超时秒（默认 %s）" % DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    ensure_utf8_stdout()

    if args.date:
        try:
            dt.datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("参数错误：--date 需为 YYYY-MM-DD（收到 %r）" % args.date,
                  file=sys.stderr)
            return 2

    try:
        snap = build_snapshot(
            date=(args.date or None),
            repo=(args.repo or None),
            tasks_path=(args.tasks or None),
            plans_dir=(args.plans_dir or None),
            audit_json=(args.tasks_audit_json or None),
            evidence_json=(args.evidence_json or None),
            timeout=float(args.timeout),
        )
    except Exception as exc:  # noqa: BLE001 - 参数/环境异常也要给出结构化输出
        snap = {
            "schema": SCHEMA,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date": dt.date.today().isoformat(),
            "verdict": "unknown",
            "passed": False,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
            "counts": {}, "ledgers": {}, "gaps": [],
        }
        print("[daily-three-ledger] 快照生成失败（unknown）：%s" % snap["error"],
              file=sys.stderr)

    written = {}
    if not args.no_write and snap.get("ledgers"):
        try:
            written = write_snapshot(snap, args.out or DEFAULT_OUT_DIR)
        except OSError as exc:
            print("[warn] 快照落盘失败（不影响判定）：%s" % exc, file=sys.stderr)

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(snap) if snap.get("ledgers")
              else "日终三账快照生成失败（unknown）：%s" % snap.get("error"))
    if written:
        print("[daily-three-ledger] 已写入 %s / %s" % (
            written.get("markdown"), written.get("json")), file=sys.stderr)
    verdict = str(snap.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
