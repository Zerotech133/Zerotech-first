# -*- coding: utf-8 -*-
"""计划 v2 §5「周日 KPI 看板」只读聚合器（weekly_kpi_board）。

背景
----
研发计划 v2（`docs/plans/2026-10-31_零界研发计划_v2_收口与闭环.md`）§5 规定：
**每周日 20:00** 出一次指标看板。本工具把 12 项指标做成**一条命令、一次聚合**：
逐项给出「当前值 / 目标 / 状态 / 证据 / blocker」，同时落 Markdown + JSON。

设计原则（与仓库既有检查器一致）
--------------------------------
- **只读**：不写 `tasks.json`、不写计划/outcome/approval、不碰生产 artifact、
  不启停任何服务；只做文件读取 + 只读 git 命令 + 复用既有只读检查器。
- **失败未知不伪造**：任何输入缺失/不可读 → 该项 `unknown` 并保留错误原文，
  绝不填猜测值；已知口径限制写进该指标 evidence，不冒充 pass。
- **复用既有工具**，不重造口径：
    * missing_worktree → `tools/worktree_invariant_check.py`（在制 worktree/branch/
      owner/aborted 不变量，W2 官方口径）；
    * version all_match → `tools/release_artifact_guard.py check()`（current.json →
      junction → manifest → 逐文件 sha256/bytes 的**离线** all_match 判据）；
    * 计划台账 → `docs/plans/PLAN_REGISTER_ALL_*.json`（governance 全量台账），
      并用 `tools/plan_register.py build()` 做交叉核对（若可导入）；
    * TaskStore → `agent_platform/data/org/tasks/tasks_*.jsonl`（append-only 快照，
      取每个 task_id 的最后状态；只读解析，不实例化写路径）；
    * E2E / fast gate → `tools/test_gate.ps1` 的 fast 套件清单 + `%TEMP%` 最近 gate 日志。
- **口径显式**：fast gate 覆盖采用 W5/计划同口径「测试资产非空行占比」，
  **不是 coverage.py 真实代码行覆盖**（后者需另立工单）；ACAD1 候选数≠可交易/冻结，
  No-Go 结论由 W3 报告另行裁决，不在此伪造。

指标与 11-07 里程碑目标（计划 v2 §5）
------------------------------------
  1 devtask open            ≤20
  2 unknown-agent / missing_owner     0
  3 missing_worktree        0
  4 TaskStore doing         ≤3
  5 计划 STATUS 覆盖        100%
  6 version all_match       true
  7 E2E artifact            入 fast gate
  8 D1 周单 doing           ≤3
  9 D1 outcome hit 覆盖     ≥1
 10 ACAD1 候选              1
 11 fast gate 覆盖          30%
 12 bundle 提交             0 增长

用法
----
    python -X utf8 tools/weekly_kpi_board.py                      # 落盘 md+json 并打印 md
    python -X utf8 tools/weekly_kpi_board.py --json               # 打印 JSON
    python -X utf8 tools/weekly_kpi_board.py --no-write           # 只看不落盘
    python -X utf8 tools/weekly_kpi_board.py --date 2026-11-08    # 指定看板日期
    python -X utf8 tools/weekly_kpi_board.py --out C:\\项目\\deliverables
    python -X utf8 tools/weekly_kpi_board.py --repo <主仓> --release-root <release 根>

输出：`<out>/<date>_周日KPI看板_v1.md` 与 `<out>/<date>_周日KPI看板_v1.json`
（默认 out = 主仓 `deliverables/`；默认 date = 2026-11-01，即首次使用日）。

退出码
------
  0 = 全部指标 pass（warn 不阻断）；
  1 = 至少一个 fail；
  2 = 无 fail 但有 unknown（输入缺失/取数失败，如实上报）。

边界：不写任何账本/生产文件；不联网（无 HTTP 探针，live 版本探测属
`tools/trust_chain_kpi.py` 职责）；不重启服务。
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import glob
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

SCHEMA_VERSION = "1.0"
SCHEMA = "weekly_kpi_board/v1"
TOOL_NAME = "weekly_kpi_board"

# 计划 v2 §5 的首个看板日与首个里程碑（11-07 列）
DEFAULT_DATE = "2026-11-01"
DEFAULT_MILESTONE = "2026-11-07"
DEFAULT_OUT_DIR = "deliverables"
PLAN_V2_PUBLISHED = dt.datetime(2026, 10, 31, 9, 30, 0)  # STATUS 09:30「研发计划 v2 发布」

# 11-07 里程碑目标（计划 v2 §5 列）
TARGET_DEVTASK_OPEN = 20
TARGET_UNKNOWN_AGENT = 0
TARGET_MISSING_WORKTREE = 0
TARGET_TASKSTORE_DOING = 3
TARGET_PLAN_COVERAGE_PCT = 100.0
TARGET_D1_WEEK_DOING = 3
TARGET_OUTCOME_HIT = 1
TARGET_ACAD_CANDIDATES = 1
TARGET_FAST_GATE_COVERAGE_PCT = 30.0
TARGET_BUNDLE_GROWTH = 0

# fast gate 覆盖口径：五个测试套件根（与 W5 报告 §1.2 同口径）
COVERAGE_SUITE_DIRS = ("portal/tests", "k_stock/tests", "agent_platform/tests",
                       "quant_backtest/tests", "tools/tests", "tools/datawarehouse/tests")
FAST_SUITES = ("portal", "kstock", "ide", "agent_key", "quant_key", "tools_key", "datawarehouse")
E2E_ARTIFACT_TEST = "agent_platform/tests/test_ide_release_artifact.py"

DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
GATE_LOG_PATTERNS = ("test_gate_*.log", "gate_after_*.log", "audit_gate_*.log")
GATE_LOG_MAX_AGE_DAYS = 7
REGISTER_MAX_AGE_DAYS = 7

STATUS_PASS, STATUS_WARN, STATUS_FAIL, STATUS_UNKNOWN = "pass", "warn", "fail", "unknown"
STATUS_LABEL = {STATUS_PASS: "PASS", STATUS_WARN: "WARN",
                STATUS_FAIL: "FAIL", STATUS_UNKNOWN: "UNKNOWN"}
STATUS_ICON = {STATUS_PASS: "✅", STATUS_WARN: "⚠️",
               STATUS_FAIL: "❌", STATUS_UNKNOWN: "❓"}

# 在制 owner 占位值（与 worktree_invariant_check / devtask 口径对齐）
OWNER_PLACEHOLDERS = frozenset(
    ["", "unknown", "unknown-agent", "unknown_agent", "?", "-", "tbd", "none",
     "null", "unowned", "[unowned]", "code-upgrade"])


class InputError(Exception):
    """输入缺失/不可读（collector 内部抛出，交由 build_board 标 unknown）。"""


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
@dataclasses.dataclass
class Ctx:
    """一次看板聚合的上下文（全部只读输入）。"""
    repo: Path
    date: str
    now: dt.datetime
    release_root: Path = DEFAULT_RELEASE_ROOT
    gate_log_dirs: list = dataclasses.field(default_factory=list)
    milestone: str = DEFAULT_MILESTONE

    @property
    def tasks_path(self) -> Path:
        return self.repo / ".worktrees" / "tasks.json"

    @property
    def plans_dir(self) -> Path:
        return self.repo / "docs" / "plans"


def _now_local() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def _now_iso(now: Optional[dt.datetime] = None) -> str:
    return (now or _now_local()).isoformat(timespec="seconds")


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InputError("文件不可读：%s（%s）" % (path, exc)) from exc


def _read_json(path: Path):
    try:
        return json.loads(_read_text(path))
    except ValueError as exc:
        raise InputError("JSON 非法：%s（%s）" % (path, exc)) from exc


def _read_any_text(path: Path) -> str:
    """容错解码：UTF-8(BOM) / UTF-16 / UTF-8。

    PowerShell 的 `*>` 重定向默认 UTF-16LE（BOM ff fe），gate 日志必须用本函数读，
    否则 `GATE PASS` 会因 NUL 交织而漏判（→ 假 unknown）。与 trust_chain_kpi 同款。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError("文件不可读：%s（%s）" % (path, exc)) from exc
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _load_tasks(ctx: Ctx) -> dict:
    data = _read_json(ctx.tasks_path)
    if not isinstance(data, dict):
        raise InputError("tasks.json 顶层不是 dict：%s" % ctx.tasks_path)
    return data


def _git(repo: Path, *args: str, timeout: float = 20.0):
    """只读 git；失败返回 (rc, '')，不抛异常给调用方（调用方自行降级）。"""
    try:
        res = subprocess.run(["git"] + list(args), cwd=str(repo), capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=timeout)
        return res.returncode, (res.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _import_tool(tools_dir: Path, name: str):
    """同目录工具动态导入（不打包、不修改 sys.path 残留）。"""
    key = "__wkb_%s__" % name
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    import importlib
    tools_dir = str(tools_dir)
    inserted = tools_dir not in sys.path
    if inserted:
        sys.path.insert(0, tools_dir)
    try:
        mod = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        raise InputError("导入 %s 失败：%s" % (name, exc)) from exc
    finally:
        if inserted:
            try:
                sys.path.remove(tools_dir)
            except ValueError:
                pass
    sys.modules[key] = mod
    return mod


def _metric(key: str, title: str, value, display: str, target, status: str,
            evidence: str = "", blocker: str = "", detail=None) -> dict:
    if status not in STATUS_LABEL:
        raise ValueError("非法状态：%r" % status)
    return {"key": key, "title": title, "value": value, "display": display,
            "target": target, "status": status,
            "evidence": _as_text(evidence), "blocker": _as_text(blocker),
            "detail": detail if detail is not None else {}}


def _pct(num: float, den: float) -> float:
    return round(num * 100.0 / den, 1) if den else 0.0


# ----------------------------------------------------------------------
# 1) devtask open / 2) unknown-agent
# ----------------------------------------------------------------------
def _active_pairs(tasks: dict) -> list:
    return [(str(tid), task) for tid, task in sorted(tasks.items())
            if isinstance(task, dict) and task.get("status") in ("doing", "review")]


def _owner_tokens(task: dict) -> list:
    raw = _as_text(task.get("owner"))
    if not raw:
        return []
    return [tok.strip() for tok in re.split(r"[/,;、]", raw) if tok.strip()]


def collect_devtask_open(ctx: Ctx) -> dict:
    tasks = _load_tasks(ctx)
    active = _active_pairs(tasks)
    doing = sum(1 for _, t in active if t.get("status") == "doing")
    review = sum(1 for _, t in active if t.get("status") == "review")
    value = len(active)
    status = STATUS_PASS if value <= TARGET_DEVTASK_OPEN else STATUS_FAIL
    names = ", ".join(tid for tid, _ in active[:12]) or "（无）"
    blocker = ("" if status == STATUS_PASS else
               "在制 %d 张 > 11-07 目标 %d；需 owner 按 11-06 前 merge/abort 收口"
               % (value, TARGET_DEVTASK_OPEN))
    return _metric(
        "devtask_open", "devtask open（doing+review）", value,
        "%d 张（doing %d / review %d）" % (value, doing, review),
        "≤%d" % TARGET_DEVTASK_OPEN, status,
        evidence="来源 %s；在制：%s" % (ctx.tasks_path, names),
        blocker=blocker,
        detail={"doing": doing, "review": review,
                "tasks": [tid for tid, _ in active]})


def collect_unknown_agent(ctx: Ctx) -> dict:
    tasks = _load_tasks(ctx)
    active = _active_pairs(tasks)
    owners: dict[str, int] = {}
    bad = []
    for tid, task in active:
        tokens = _owner_tokens(task)
        if not tokens:
            bad.append(tid)
            owners["<空>"] = owners.get("<空>", 0) + 1
            continue
        for tok in tokens:
            owners[tok] = owners.get(tok, 0) + 1
        if any(tok.lower() in OWNER_PLACEHOLDERS for tok in tokens):
            bad.append(tid)
    value = len(bad)
    status = STATUS_PASS if value == TARGET_UNKNOWN_AGENT else STATUS_FAIL
    top = " / ".join("%s %d" % (k, v) for k, v in
                     sorted(owners.items(), key=lambda kv: -kv[1])[:8]) or "（无在制）"
    return _metric(
        "unknown_agent", "unknown-agent / missing_owner（信息项）", value,
        "%d 张（信息项）" % value, "0", status,
        evidence="在制 owner 分布：%s%s" % (top, "" if not bad else "；异常：%s" % ", ".join(bad[:10])),
        blocker="" if status == STATUS_PASS else "在制存在无主/占位 owner：%s" % ", ".join(bad[:10]),
        detail={"owner_dist": dict(sorted(owners.items(), key=lambda kv: -kv[1])),
                "bad_tasks": bad})


# ----------------------------------------------------------------------
# 3) missing_worktree（复用 worktree_invariant_check）
# ----------------------------------------------------------------------
def _run_worktree_check(ctx: Ctx) -> dict:
    """调用 tools/worktree_invariant_check.check()（W2 官方不变量口径）。"""
    wic = _import_tool(ctx.repo / "tools", "worktree_invariant_check")
    tasks_path = ctx.tasks_path
    tasks = wic.load_tasks(tasks_path)
    rc, out = _git(ctx.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    refs = [line for line in out.splitlines() if line.strip()] if rc == 0 else None
    return wic.check(tasks, repo=ctx.repo, tasks_path=tasks_path,
                     branch_refs=refs, git_checked=refs is not None)


def collect_missing_worktree(ctx: Ctx) -> dict:
    rep = _run_worktree_check(ctx)
    violations = rep.get("violations") or []
    counts = rep.get("counts") or {}

    def _match(v: dict, keyword: str) -> bool:
        code = _as_text(v.get("code"))
        return keyword in code

    wt_missing = [v for v in violations if _match(v, "WORKTREE")]
    branch_missing = [v for v in violations if _match(v, "BRANCH")]
    value = len(wt_missing)
    status = STATUS_PASS if value == TARGET_MISSING_WORKTREE else STATUS_FAIL
    ev = ("worktree_invariant_check verdict=%s；在制 %s；worktree 缺失 %d / branch 缺失 %d"
          % (rep.get("verdict"), counts.get("active"), value, len(branch_missing)))
    if wt_missing:
        ev += "；明细：%s" % "; ".join(_as_text(v.get("task")) for v in wt_missing[:10])
    return _metric(
        "missing_worktree", "missing_worktree（在制无工作树）", value,
        "%d 张" % value, "0", status, evidence=ev,
        blocker="" if status == STATUS_PASS else "在制缺 worktree：%s"
        % ", ".join(_as_text(v.get("task")) for v in wt_missing[:10]),
        detail={"worktree_missing": [v.get("task") for v in wt_missing],
                "branch_missing": [v.get("task") for v in branch_missing],
                "inconsistent": counts.get("violations")})


# ----------------------------------------------------------------------
# 4) TaskStore doing
# ----------------------------------------------------------------------
def _taskstore_latest(ctx: Ctx) -> dict:
    """只读解析 append-only 快照：每个 task_id 取最后出现的 status。"""
    base = ctx.repo / "agent_platform" / "data" / "org" / "tasks"
    files = sorted(glob.glob(str(base / "tasks_*.jsonl")))
    if not files:
        raise InputError("TaskStore 账本不存在：%s（tasks_*.jsonl）" % base)
    latest: dict[str, dict] = {}
    bad_lines = 0
    for path in files:
        text = _read_text(Path(path))
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad_lines += 1
                continue
            tid = _as_text(row.get("task_id"))
            if not tid:
                continue
            latest[tid] = row
    if not latest:
        raise InputError("TaskStore 账本无有效行：%s" % base)
    return {"latest": latest, "files": [Path(p).name for p in files],
            "bad_lines": bad_lines}


def collect_taskstore_doing(ctx: Ctx) -> dict:
    data = _taskstore_latest(ctx)
    latest = data["latest"]
    counts: dict[str, int] = {}
    for row in latest.values():
        st = _as_text(row.get("status")) or "<空>"
        counts[st] = counts.get(st, 0) + 1
    doing = counts.get("doing", 0)
    status = STATUS_PASS if doing <= TARGET_TASKSTORE_DOING else STATUS_FAIL
    ev = "账本 %s（%d 行快照，坏行 %d）；status 分布：%s" % (
        ", ".join(data["files"]), len(latest), data["bad_lines"],
        " / ".join("%s %d" % kv for kv in sorted(counts.items(), key=lambda kv: -kv[1])))
    return _metric(
        "taskstore_doing", "TaskStore doing", doing, "%d 单" % doing,
        "≤%d" % TARGET_TASKSTORE_DOING, status, evidence=ev,
        blocker="" if status == STATUS_PASS else "TaskStore doing 超目标，需收口",
        detail={"status_counts": counts, "files": data["files"]})


# ----------------------------------------------------------------------
# 5) 计划 STATUS 覆盖（governance 全量台账 + plan_register 交叉核对）
# ----------------------------------------------------------------------
def _generated_register_name(path_text: str) -> bool:
    name = Path(_as_text(path_text)).name
    return name.startswith(("PLAN_REGISTER", "INDEX_")) or name.lower() in ("readme.md",)


def _parse_dt(text: str) -> Optional[dt.datetime]:
    text = _as_text(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def collect_plan_status(ctx: Ctx) -> dict:
    regs = sorted(glob.glob(str(ctx.plans_dir / "PLAN_REGISTER_ALL_*.json")),
                  key=lambda p: Path(p).stat().st_mtime, reverse=True)
    if not regs:
        raise InputError("未找到 PLAN_REGISTER_ALL_*.json：%s" % ctx.plans_dir)
    reg_path = Path(regs[0])
    data = _read_json(reg_path)
    plans = data.get("plans") or data.get("rows") or []
    total = int(data.get("total") or len(plans))
    if not total or not isinstance(plans, list):
        raise InputError("计划台账结构异常：total=%r plans=%r"
                         % (data.get("total"), type(plans).__name__))
    counts = data.get("counts") or {}
    unspecified = [p for p in plans if _as_text(p.get("state") or p.get("status")) == "unspecified"]
    exempt = [p for p in unspecified if _generated_register_name(p.get("path") or p.get("file"))]
    real_unspecified = [p for p in unspecified if p not in exempt]
    real_total = max(0, total - len(exempt))
    covered = max(0, real_total - len(real_unspecified))
    coverage = _pct(covered, real_total)
    generated = _parse_dt(_as_text(data.get("generated_at")))
    age_days = None
    if generated is not None:
        age_days = round((ctx.now.replace(tzinfo=None) - generated).total_seconds() / 86400.0, 1)
    stale = age_days is not None and age_days > REGISTER_MAX_AGE_DAYS
    status = STATUS_PASS if (not real_unspecified and not stale) else (
        STATUS_WARN if (not real_unspecified and stale) else STATUS_FAIL)
    # plan_register 交叉核对（可选，失败只记口径差异不判负）
    cross = ""
    try:
        pr = _import_tool(ctx.repo / "tools", "plan_register")
        rep = pr.build()
        cross = "；plan_register 交叉：plans=%d status_marked=%d/%d（口径=docs/plans 顶层）" % (
            rep.get("total"), rep.get("status_marked"), rep.get("total"))
    except Exception as exc:  # noqa: BLE001
        cross = "；plan_register 交叉核对不可用（%s）" % exc.__class__.__name__
    display = "%.1f%%（%d/%d）" % (coverage, covered, real_total)
    ev = ("台账 %s（generated_at=%s%s）；counts=%s；真实计划 %d 份，未标注 unspecified %d"
          "（已排除生成台账自指 %d）%s"
          % (reg_path.name, data.get("generated_at"), 
             "" if age_days is None else "，%.1f 天前" % age_days,
             json.dumps(counts, ensure_ascii=False), real_total,
             len(real_unspecified), len(exempt), cross))
    blocker = ""
    if real_unspecified:
        blocker = "未标状态计划 %d 份：%s" % (
            len(real_unspecified),
            ", ".join(Path(_as_text(p.get("path") or p.get("file"))).name
                      for p in real_unspecified[:8]))
    elif stale:
        blocker = "计划台账已 %.1f 天未刷新（>%d 天）；需重跑 index_governance_assets" % (
            age_days, REGISTER_MAX_AGE_DAYS)
    return _metric(
        "plan_status", "计划 STATUS 覆盖", coverage, display,
        "%d%%" % int(TARGET_PLAN_COVERAGE_PCT), status, evidence=ev, blocker=blocker,
        detail={"register": str(reg_path), "total": total, "counts": counts,
                "exempt_unspecified": [p.get("path") for p in exempt],
                "real_unspecified": [p.get("path") for p in real_unspecified],
                "age_days": age_days})


# ----------------------------------------------------------------------
# 6) version all_match（复用 release_artifact_guard，离线口径）
# ----------------------------------------------------------------------
def _run_release_guard(ctx: Ctx) -> dict:
    guard = _import_tool(ctx.repo / "tools", "release_artifact_guard")
    return guard.check(ctx.release_root)


def collect_version_all_match(ctx: Ctx) -> dict:
    res = _run_release_guard(ctx)
    ok = bool(res.get("ok"))
    exit_code = int(res.get("exit_code") or 0)
    pointer = res.get("pointer") or {}
    manifest = res.get("manifest") or {}
    summary = res.get("summary") or {}
    junction = res.get("junction") or {}
    version = _as_text(pointer.get("version"))
    ev = ("release_artifact_guard（离线 all_match 口径）：pointer=%s manifest=%s "
          "files_ok=%s/%s junction_drift=%s"
          % (version or "?", _as_text(manifest.get("version")) or "?",
             summary.get("files_ok"), summary.get("files_total"), junction.get("drift")))
    errors = res.get("errors") or []
    if errors:
        ev += "；errors=%s" % "; ".join(_as_text(e) for e in errors[:5])
    if ok:
        return _metric("version_all_match", "version all_match", True, "all_match=true",
                       "true", STATUS_PASS, evidence=ev,
                       detail={"pointer_version": version, "files_ok": summary.get("files_ok"),
                               "files_total": summary.get("files_total")})
    if exit_code == 2:
        # 环境错误（release root/current.json 不可读）→ 未知，不伪造失败
        return _metric("version_all_match", "version all_match", None, "unknown",
                       "true", STATUS_UNKNOWN, evidence=ev,
                       blocker="发布物不可读（环境错误 exit_code=2），需人工确认 release 根")
    return _metric("version_all_match", "version all_match", False, "all_match=false",
                   "true", STATUS_FAIL, evidence=ev,
                   blocker="artifact 完整性失败（篡改/缺文件/junction drift）：%s"
                   % "; ".join(_as_text(e) for e in errors[:3]),
                   detail={"errors": errors})


# ----------------------------------------------------------------------
# 7) E2E artifact（test_ide_release_artifact 入 fast gate + 最近 gate 结果）
# ----------------------------------------------------------------------
def _extract_paths_block(text: str) -> Optional[str]:
    """抽取 test_gate.ps1 中 `$paths = @{ ... }` 块（花括号配对）。"""
    m = re.search(r"\$paths\s*=\s*@\{", text)
    if not m:
        return None
    start = text.index("{", m.start())
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _strip_ps_comments(value: str) -> str:
    return "\n".join(line for line in value.splitlines() if not line.strip().startswith("#"))


def parse_gate_paths(text: str) -> dict:
    """解析 test_gate.ps1 的 `$paths`（套件→目录/文件清单）。解析失败抛 InputError。"""
    block = _extract_paths_block(text)
    if block is None:
        raise InputError("test_gate.ps1 中未找到 $paths 块")
    out: dict[str, list] = {}
    # 条目形如：'suite' = 'path'  或  'suite' = @( ... )
    entry_re = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*)'\s*=\s*", re.M)
    matches = list(entry_re.finditer(block))
    if not matches:
        raise InputError("$paths 块内未解析到套件条目")
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block) - 1
        chunk = block[start:end].strip()
        if chunk.startswith("@("):
            inner_end = chunk.find(")")
            if inner_end < 0:
                continue
            inner = _strip_ps_comments(chunk[2:inner_end])
            out[name] = re.findall(r"'([^']+\.py)'", inner)
        else:
            single = re.match(r"'([^']+)'", chunk)
            if single:
                out[name] = [single.group(1)]
    return out


def _test_files_under(path: Path) -> list:
    if path.is_dir():
        return sorted(str(p) for p in path.rglob("test_*.py") if p.is_file())
    if path.is_file():
        return [str(path)]
    return []


def _nonempty_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _fast_covered_files(ctx: Ctx, paths: dict) -> list:
    covered: list[Path] = []
    for suite in FAST_SUITES:
        for rel in paths.get(suite) or []:
            target = (ctx.repo / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
            # 防越界：只统计仓库内路径
            try:
                target.relative_to(ctx.repo.resolve())
            except ValueError:
                continue
            for f in _test_files_under(target):
                covered.append(Path(f))
    # 去重（portal 根文件 / 数组内重复）
    seen, uniq = set(), []
    for p in covered:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


GATE_LOG_SCAN_LIMIT = 60
SUITE_LOG_RE = re.compile(r"_\d{8}_\d{6}\.log$")


def _default_gate_log_dirs() -> list:
    """gate 汇总日志默认落 C:\\Temp（test_gate.ps1 约定）；套件日志落系统 TEMP。

    两个目录都纳入，避免只认一处导致「入 gate 但无日志证据」的假 unknown。
    """
    dirs = [Path(r"C:\Temp"), Path(tempfile.gettempdir())]
    out, seen = [], set()
    for d in dirs:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _gate_log_dirs(ctx: Ctx) -> list:
    return list(ctx.gate_log_dirs) or _default_gate_log_dirs()


def _gate_log_candidates(ctx: Ctx) -> list:
    cands: list[Path] = []
    for d in _gate_log_dirs(ctx):
        for pattern in GATE_LOG_PATTERNS:
            cands.extend(Path(p) for p in glob.glob(str(Path(d) / pattern)))
    uniq, seen = [], set()
    for p in cands:
        if not p.is_file():
            continue
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return sorted(uniq, key=lambda p: p.stat().st_mtime, reverse=True)


def _looks_like_summary_log(name: str) -> bool:
    """汇总日志命名不含 `_<8位日期>_<6位时间>` 后缀（套件日志形如 test_gate_<suite>_20261031_191323.log）。"""
    return bool(re.match(r"^(?:gate_after_|test_gate_|audit_gate_)", name)) \
        and not SUITE_LOG_RE.search(name)


def _find_gate_verdict(ctx: Ctx) -> Optional[dict]:
    """最近一次带 GATE PASS/FAIL 判定的日志（汇总日志优先，最多读 60 个文件）。"""
    cands = _gate_log_candidates(ctx)
    ordered = ([p for p in cands if _looks_like_summary_log(p.name)]
               + [p for p in cands if not _looks_like_summary_log(p.name)])
    scanned = 0
    for cand in ordered:
        if scanned >= GATE_LOG_SCAN_LIMIT:
            break
        scanned += 1
        try:
            body = _read_any_text(cand)
        except InputError:
            continue
        if "GATE PASS" in body or "GATE FAIL" in body:
            return {"path": str(cand),
                    "verdict": "pass" if "GATE PASS" in body else "fail",
                    "mtime": dt.datetime.fromtimestamp(cand.stat().st_mtime),
                    "scanned": scanned}
    return None


def _gate_dirs_text(ctx: Ctx) -> str:
    return " / ".join(str(d) for d in _gate_log_dirs(ctx))


def _run_e2e_artifact(ctx: Ctx) -> dict:
    gate_ps1 = ctx.repo / "tools" / "test_gate.ps1"
    text = _read_text(gate_ps1)
    paths = parse_gate_paths(text)
    covered = [str(p.resolve()).lower() for p in _fast_covered_files(ctx, paths)]
    test_file = (ctx.repo / E2E_ARTIFACT_TEST)
    in_gate = bool(covered) and any(
        p.endswith(E2E_ARTIFACT_TEST.replace("/", "\\").lower()) or
        p.endswith(E2E_ARTIFACT_TEST.lower()) for p in covered)
    test_exists = test_file.is_file()
    log = _find_gate_verdict(ctx)
    version = None
    if log:
        age = (ctx.now.replace(tzinfo=None) - log["mtime"]).total_seconds() / 86400.0
        version = {"verdict": log["verdict"], "path": log["path"],
                   "age_days": round(age, 1), "fresh": age <= GATE_LOG_MAX_AGE_DAYS,
                   "mtime": log["mtime"].isoformat(timespec="seconds")}
    return {"in_gate": in_gate, "test_exists": test_exists,
            "paths": paths, "log": version,
            "test_file": str(test_file), "gate_ps1": str(gate_ps1)}


def collect_e2e_artifact(ctx: Ctx) -> dict:
    info = _run_e2e_artifact(ctx)
    in_gate = info["in_gate"]
    test_exists = info["test_exists"]
    log = info["log"]
    ev = ("%s；test_ide_release_artifact.py 存在=%s 入 fast gate=%s"
          % (Path(info["gate_ps1"]).name, test_exists, in_gate))
    if log:
        ev += "；最近 gate 日志 %s（%s，%.1f 天前）" % (
            Path(log["path"]).name, log["verdict"].upper(), log["age_days"])
    else:
        ev += "；%s 内未找到含 GATE PASS/FAIL 的 gate 汇总日志" % _gate_dirs_text(ctx)
    if in_gate and test_exists and log and log["verdict"] == "pass" and log["fresh"]:
        return _metric("e2e_artifact", "E2E artifact（入 gate / 最近 pass）",
                       "in_gate", "入 gate + 最近 gate PASS", "入 fast gate",
                       STATUS_PASS, evidence=ev, detail=info["log"])
    if in_gate and test_exists and log and log["verdict"] == "fail":
        return _metric("e2e_artifact", "E2E artifact（入 gate / 最近 pass）",
                       "gate_fail", "入 gate，但最近 gate FAIL", "入 fast gate",
                       STATUS_FAIL, evidence=ev,
                       blocker="最近 fast gate FAIL；需按 gate 日志修复后再看板",
                       detail=info["log"])
    if in_gate and test_exists and log and not log["fresh"]:
        return _metric("e2e_artifact", "E2E artifact（入 gate / 最近 pass）",
                       "in_gate", "入 gate（日志 %.1f 天前，已过期）" % log["age_days"],
                       "入 fast gate", STATUS_WARN, evidence=ev,
                       blocker="最近 gate 日志超过 %d 天，需重跑质量闸取证" % GATE_LOG_MAX_AGE_DAYS,
                       detail=info["log"])
    if not in_gate or not test_exists:
        return _metric("e2e_artifact", "E2E artifact（入 gate / 最近 pass）",
                       "not_in_gate", "未入 fast gate", "入 fast gate",
                       STATUS_FAIL, evidence=ev,
                       blocker="test_ide_release_artifact.py 未在 fast gate 清单/文件缺失",
                       detail=info)
    # 入 gate 但没有可判定的最近日志 → 未判定（不伪造 pass）
    return _metric("e2e_artifact", "E2E artifact（入 gate / 最近 pass）",
                   "in_gate", "入 gate（无最近日志证据）", "入 fast gate",
                   STATUS_UNKNOWN, evidence=ev,
                   blocker="需要最近一次 fast gate 日志作为 pass 证据（可用 test_gate.ps1 -Suite fast 生成；默认扫描 C:\\Temp 与系统 TEMP）",
                   detail=info["log"])


# ----------------------------------------------------------------------
# 8) D1 周单 doing
# ----------------------------------------------------------------------
D1_WEEK_PREFIX = "WO-INV-WEEK"


def collect_d1_weekly(ctx: Ctx) -> dict:
    tasks = _load_tasks(ctx)
    weekly = {str(tid): t for tid, t in tasks.items()
              if isinstance(t, dict) and str(tid).startswith(D1_WEEK_PREFIX)}
    doing = [tid for tid, t in sorted(weekly.items()) if t.get("status") == "doing"]
    status = STATUS_PASS if len(doing) <= TARGET_D1_WEEK_DOING else STATUS_FAIL
    by_status: dict[str, int] = {}
    for t in weekly.values():
        st = _as_text(t.get("status")) or "<空>"
        by_status[st] = by_status.get(st, 0) + 1
    return _metric(
        "d1_weekly", "D1 周单 doing", len(doing),
        "%d 张（周单共 %d）" % (len(doing), len(weekly)),
        "≤%d" % TARGET_D1_WEEK_DOING, status,
        evidence="口径：tasks.json 中 %s-* 工单；状态分布：%s%s"
        % (D1_WEEK_PREFIX, json.dumps(by_status, ensure_ascii=False),
           "" if not doing else "；doing：%s" % ", ".join(doing)),
        blocker="" if status == STATUS_PASS else "D1 周单 doing %d > 目标 %d" % (
            len(doing), TARGET_D1_WEEK_DOING),
        detail={"weekly_total": len(weekly), "doing": doing, "by_status": by_status})


# ----------------------------------------------------------------------
# 9) D1 outcome hit 覆盖
# ----------------------------------------------------------------------
OUTCOME_PATH = "agent_platform/data/shadow/decision_outcomes.jsonl"


def collect_outcome_hit(ctx: Ctx) -> dict:
    path = ctx.repo / OUTCOME_PATH
    if not path.is_file():
        raise InputError("outcome 账本不存在：%s" % path)
    text = _read_text(path)
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    hit_rows = [r for r in rows if isinstance(r, dict) and r.get("hit") is not None]
    contract_rows = [r for r in hit_rows if _as_text(r.get("decision_id"))]
    hit_true = sum(1 for r in hit_rows if r.get("hit") in (True, 1, "1", "true"))
    value = len(contract_rows)
    status = STATUS_PASS if value >= TARGET_OUTCOME_HIT else STATUS_FAIL
    ev = ("账本 %s：总行 %d（坏行 %d）；hit 非空 %d（hit=true %d）；"
          "带 decision_id 且 hit 非空 %d（W3 契约口径）"
          % (OUTCOME_PATH, len(rows), bad, len(hit_rows), hit_true, value))
    if len(rows) and value == 0:
        ev += "；存量 %d 行 legacy 无 decision_id（不计入契约口径）" % len(rows)
    return _metric(
        "outcome_hit", "D1 outcome hit 覆盖", value, "%d 条（契约口径）" % value,
        "≥%d" % TARGET_OUTCOME_HIT, status, evidence=ev,
        blocker="" if status == STATUS_PASS else
        "11-02 outcome 回写尚未开始（contract 口径 0 条）；W3 报告：outcome 待 11-02，不伪造",
        detail={"rows": len(rows), "hit_nonnull": len(hit_rows),
                "hit_true": hit_true, "contract_rows": value,
                "legacy_no_decision_id": len(rows) - len(contract_rows)})


# ----------------------------------------------------------------------
# 10) ACAD1 候选
# ----------------------------------------------------------------------
CANDIDATE_ID_RE = re.compile(r"-v\d+$")


def _candidate_id(filename: str) -> str:
    stem = Path(filename).stem
    return CANDIDATE_ID_RE.sub("", stem)


def collect_acad1_candidates(ctx: Ctx) -> dict:
    roots = [ctx.repo / "agent_platform" / "data" / "academy" / "artifacts",
             ctx.repo / "deliverables"]
    found: dict[str, dict] = {}
    scanned = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("RA-*.json")):
            scanned += 1
            cid = _candidate_id(path.name)
            version = path.stem[len(cid):].lstrip("-") or "?"
            entry = found.setdefault(cid, {"id": cid, "versions": [],
                                           "files": []})
            entry["versions"].append(version)
            entry["files"].append(str(path.relative_to(ctx.repo))
                                  if str(path).startswith(str(ctx.repo)) else str(path))
    candidates = list(found.values())
    value = len(candidates)
    status = STATUS_PASS if value >= TARGET_ACAD_CANDIDATES else STATUS_FAIL
    listing = "; ".join("%s（%s）" % (c["id"], ",".join(sorted(c["versions"])))
                        for c in candidates[:6]) or "（无）"
    return _metric(
        "acad1_candidates", "ACAD1 候选（证据包计数）", value, "%d 个候选" % value,
        "%d" % TARGET_ACAD_CANDIDATES, status,
        evidence="扫描 %d 个 RA-*.json → %d 个去重候选 id：%s；口径=候选证据包计数，"
                 "≠可交易/冻结结论（No-Go 由 W3 报告裁决）" % (scanned, value, listing),
        blocker="" if status == STATUS_PASS else
        "候选证据包 %d < 目标 %d；ACAD1 当前 No-Go（W3 11:19），不得伪造候选" % (
            value, TARGET_ACAD_CANDIDATES),
        detail={"candidates": [{k: v for k, v in c.items()} for c in candidates]})


# ----------------------------------------------------------------------
# 11) fast gate 覆盖（W5/计划同口径：测试资产非空行占比）
# ----------------------------------------------------------------------
def _universe_files(ctx: Ctx) -> list:
    files: list[Path] = []
    for rel in COVERAGE_SUITE_DIRS:
        d = ctx.repo / rel
        if not d.is_dir():
            continue
        files.extend(sorted(p for p in d.rglob("test_*.py") if p.is_file()))
    return files


def _coverage_stats(ctx: Ctx) -> dict:
    gate_ps1 = ctx.repo / "tools" / "test_gate.ps1"
    paths = parse_gate_paths(_read_text(gate_ps1))
    universe = _universe_files(ctx)
    if not universe:
        raise InputError("测试套件目录为空：%s" % (COVERAGE_SUITE_DIRS,))
    universe_set = {str(p.resolve()).lower() for p in universe}
    covered = [p for p in _fast_covered_files(ctx, paths)
               if str(p.resolve()).lower() in universe_set]
    covered_lines = sum(_nonempty_lines(p) for p in covered)
    total_lines = sum(_nonempty_lines(p) for p in universe)
    return {"covered_files": len(covered), "total_files": len(universe),
            "files_pct": _pct(len(covered), len(universe)),
            "covered_lines": covered_lines, "total_lines": total_lines,
            "lines_pct": _pct(covered_lines, total_lines),
            "gate_ps1": str(gate_ps1)}


def collect_fast_gate_coverage(ctx: Ctx) -> dict:
    stats = _coverage_stats(ctx)
    pct = stats["lines_pct"]
    status = STATUS_PASS if pct >= TARGET_FAST_GATE_COVERAGE_PCT else STATUS_FAIL
    ev = ("口径=测试资产非空行占比（W5 报告 §1.2 / 计划 v2 §5 同口径，非 coverage.py）："
          "非空行 %d/%d = %.1f%%；文件 %d/%d = %.1f%%；基线 W5 实测 23.0%%（10-31）；来源 %s"
          % (stats["covered_lines"], stats["total_lines"], pct,
             stats["covered_files"], stats["total_files"], stats["files_pct"],
             Path(stats["gate_ps1"]).name))
    return _metric(
        "fast_gate_coverage", "fast gate 覆盖（行占比口径）", pct,
        "%.1f%%（行口径）" % pct, "≥%d%%" % int(TARGET_FAST_GATE_COVERAGE_PCT),
        status, evidence=ev,
        blocker="" if status == STATUS_PASS else
        "覆盖 %.1f%% < 11-07 目标 %d%%；按 W5 B1/B2 批次继续接测试（不一次全接）"
        % (pct, int(TARGET_FAST_GATE_COVERAGE_PCT)),
        detail=stats)


# ----------------------------------------------------------------------
# 12) bundle 提交（目标 0 增长）
# ----------------------------------------------------------------------
BUNDLE_COMMIT_GREP = "auto rebuild agent_ide_bundle"


def _git_bundle_window(ctx: Ctx, since: dt.datetime) -> list:
    since_text = since.strftime("%Y-%m-%d %H:%M:%S")
    rc, out = _git(ctx.repo, "log", "--since=%s" % since_text,
                   "--pretty=format:%h|%ci|%s", "--grep=%s" % BUNDLE_COMMIT_GREP)
    if rc != 0:
        raise InputError("git log 不可用（rc=%d）" % rc)
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return rows


def collect_bundle_commits(ctx: Ctx) -> dict:
    window_start = ctx.now.replace(tzinfo=None) - dt.timedelta(days=8)
    window_rows = _git_bundle_window(ctx, window_start)
    because_v2_rows = _git_bundle_window(ctx, PLAN_V2_PUBLISHED)
    value = len(window_rows)
    growth = len(because_v2_rows)
    # 目标「0 增长」：以计划 v2 发布（09:30）为界的新增 auto-rebuild 提交为 0 才 pass
    status = STATUS_PASS if growth <= TARGET_BUNDLE_GROWTH else STATUS_FAIL
    recent = "; ".join("%s %s" % (r["commit"], r["subject"][:60]) for r in window_rows[:3])
    ev = ("窗口 近8天（自 %s）auto-rebuild 提交 %d 条（计划 v2 基线 826/8d）；"
          "自计划 v2 发布 %s 起新增 %d 条%s"
          % (window_start.strftime("%Y-%m-%d"), value,
             PLAN_V2_PUBLISHED.strftime("%Y-%m-%d %H:%M"), growth,
             "" if not recent else "；近例：" + recent))
    return _metric(
        "bundle_commits", "bundle 提交（auto rebuild）", value,
        "%d 条/8d；自计划 v2 新增 %d" % (value, growth), "0 增长", status,
        evidence=ev,
        blocker="" if status == STATUS_PASS else
        "计划 v2 发布后仍新增 %d 条 auto-rebuild bundle 提交；需按计划「0 增长」收口"
        % growth,
        detail={"window_start": window_start.isoformat(timespec="seconds"),
                "window_count": value, "since_plan_v2_count": growth,
                "samples": window_rows[:5], "plan_v2_baseline_per_8d": 826})


# ----------------------------------------------------------------------
# 聚合
# ----------------------------------------------------------------------
COLLECTORS: list[tuple[str, str, Callable[[Ctx], dict]]] = [
    ("devtask_open", "devtask open（doing+review）", collect_devtask_open),
    ("unknown_agent", "unknown-agent / missing_owner（信息项）", collect_unknown_agent),
    ("missing_worktree", "missing_worktree（在制无工作树）", collect_missing_worktree),
    ("taskstore_doing", "TaskStore doing", collect_taskstore_doing),
    ("plan_status", "计划 STATUS 覆盖", collect_plan_status),
    ("version_all_match", "version all_match", collect_version_all_match),
    ("e2e_artifact", "E2E artifact（入 gate / 最近 pass）", collect_e2e_artifact),
    ("d1_weekly", "D1 周单 doing", collect_d1_weekly),
    ("outcome_hit", "D1 outcome hit 覆盖", collect_outcome_hit),
    ("acad1_candidates", "ACAD1 候选（证据包计数）", collect_acad1_candidates),
    ("fast_gate_coverage", "fast gate 覆盖（行占比口径）", collect_fast_gate_coverage),
    ("bundle_commits", "bundle 提交（auto rebuild）", collect_bundle_commits),
]


def default_repo() -> Path:
    """默认主仓：linked worktree 内回落到主仓（同 tools/_repo_paths.repo_root 口径）。"""
    tools_dir = Path(__file__).resolve().parent
    try:
        key = "__wkb_repo_paths__"
        mod = sys.modules.get(key)
        if mod is None:
            inserted = str(tools_dir) not in sys.path
            if inserted:
                sys.path.insert(0, str(tools_dir))
            try:
                import importlib
                mod = importlib.import_module("_repo_paths")
            finally:
                if inserted:
                    try:
                        sys.path.remove(str(tools_dir))
                    except ValueError:
                        pass
            sys.modules[key] = mod
        return Path(mod.repo_root(start=__file__))
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[1]


def build_board(repo: Optional[Path] = None, date: str = DEFAULT_DATE, *,
                now: Optional[dt.datetime] = None,
                release_root: Optional[Path] = None,
                temp_dir: Optional[Path] = None,
                milestone: str = DEFAULT_MILESTONE) -> dict:
    """聚合 12 项指标；单指标取数异常 → unknown（保留错误原文，不伪造）。"""
    repo = Path(repo) if repo else default_repo()
    now = now or _now_local()
    gate_dirs = [Path(temp_dir)] if temp_dir else _default_gate_log_dirs()
    ctx = Ctx(repo=repo, date=date, now=now,
              release_root=Path(release_root) if release_root else DEFAULT_RELEASE_ROOT,
              gate_log_dirs=gate_dirs,
              milestone=milestone)
    metrics = []
    for key, title, fn in COLLECTORS:
        try:
            metrics.append(fn(ctx))
        except InputError as exc:
            metrics.append(_metric(key, title, None, "unknown", "-", STATUS_UNKNOWN,
                                   evidence="输入缺失/不可读：%s" % exc,
                                   blocker="取数失败：%s" % exc))
        except Exception as exc:  # noqa: BLE001 —— 单指标异常不拖垮看板
            metrics.append(_metric(key, title, None, "unknown", "-", STATUS_UNKNOWN,
                                   evidence="取数异常：%s: %s" % (exc.__class__.__name__, exc),
                                   blocker="单指标异常，需排查：%s" % exc))
    summary = {st: sum(1 for m in metrics if m["status"] == st)
               for st in (STATUS_PASS, STATUS_WARN, STATUS_FAIL, STATUS_UNKNOWN)}
    exit_code = 1 if summary[STATUS_FAIL] else (2 if summary[STATUS_UNKNOWN] else 0)
    verdict = ("FAIL" if exit_code == 1 else "UNKNOWN" if exit_code == 2 else "PASS")
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "generated_at": _now_iso(now),
        "board_date": date,
        "milestone": milestone,
        "repo": str(repo),
        "verdict": verdict,
        "exit_code": exit_code,
        "summary": summary,
        "metrics": metrics,
        "inputs": {
            "tasks": str(ctx.tasks_path),
            "plans_dir": str(ctx.plans_dir),
            "release_root": str(ctx.release_root),
            "gate_log_dirs": [str(d) for d in _gate_log_dirs(ctx)],
        },
    }


# ----------------------------------------------------------------------
# 渲染 / 落盘
# ----------------------------------------------------------------------
def _md_cell(text) -> str:
    return _as_text(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _clip(text: str, limit: int = 320) -> str:
    text = _as_text(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def render_markdown(board: dict) -> str:
    s = board["summary"]
    lines = [
        "# %s 周日 KPI 看板 v1（研发计划 v2 §5）" % board["board_date"],
        "",
        "> 生成时间：%s ｜ 里程碑：**%s** ｜ 仓库：`%s`"
        % (board["generated_at"], board["milestone"], board["repo"]),
        "> 口径：只读聚合（不写任务板/计划/生产，不重启服务）；复用 worktree_invariant_check /"
        " release_artifact_guard / test_gate 清单 / 计划全量台账。失败与未知一律如实标注，不伪造。",
        "> 判定：%s ｜ 退出码 **%d**（0=全 pass；1=有 fail；2=无 fail 但存在 unknown）"
        % (board["verdict"], board["exit_code"]),
        "",
        "## 0. 总览",
        "",
        "| PASS | WARN | FAIL | UNKNOWN | 判定 |",
        "|---:|---:|---:|---:|---|",
        "| %d | %d | %d | %d | **%s** |"
        % (s[STATUS_PASS], s[STATUS_WARN], s[STATUS_FAIL], s[STATUS_UNKNOWN], board["verdict"]),
        "",
        "## 1. 指标（当前值 / 目标 / 状态 / 证据）",
        "",
        "| # | 指标 | 当前 | 目标（%s） | 状态 | 证据 / blocker |" % board["milestone"],
        "|---:|---|---|---|---|---|",
    ]
    for idx, m in enumerate(board["metrics"], 1):
        cell = _md_cell(_clip(m["evidence"]))
        if m["blocker"]:
            cell += "<br>**blocker**：" + _md_cell(_clip(m["blocker"], 220))
        lines.append("| %d | %s | %s | %s | %s %s | %s |" % (
            idx, _md_cell(m["title"]), _md_cell(m["display"]), _md_cell(m["target"]),
            STATUS_ICON.get(m["status"], ""), STATUS_LABEL.get(m["status"], m["status"]), cell))
    fails = [m for m in board["metrics"] if m["status"] == STATUS_FAIL]
    unknowns = [m for m in board["metrics"] if m["status"] == STATUS_UNKNOWN]
    lines += ["", "## 2. FAIL 项（需 owner 处置）", ""]
    if fails:
        for m in fails:
            lines.append("- **%s**：%s（目标 %s）→ %s" % (
                m["title"], m["display"], m["target"],
                _as_text(m["blocker"]) or "见上表证据"))
    else:
        lines.append("- 无")
    lines += ["", "## 3. UNKNOWN 项（口径/输入待补，未伪造为 pass）", ""]
    if unknowns:
        for m in unknowns:
            lines.append("- **%s**：%s" % (m["title"], _as_text(m["blocker"]) or "见上表证据"))
    else:
        lines.append("- 无")
    lines += [
        "", "## 4. 复跑与口径说明", "",
        "```powershell",
        "python -X utf8 tools/weekly_kpi_board.py --date %s --json"
        % board["board_date"],
        "python -X utf8 tools/weekly_kpi_board.py --date %s --out C:\\项目\\deliverables"
        % board["board_date"],
        "```",
        "",
        "- fast gate 覆盖 = **测试资产非空行占比**（W5 报告 §1.2 / 计划同口径），**不是** "
        "coverage.py 真实代码行覆盖；若要真实核心行覆盖需另立工单。",
        "- TaskStore = append-only 快照 `agent_platform/data/org/tasks/tasks_*.jsonl`"
        "（每个 task_id 取最后状态）；只读解析，不调写路径。",
        "- ACAD1 候选数 = 候选证据包去重计数，**不代表可交易/已冻结**；No-Go 结论由 W3 报告裁决。",
        "- bundle 提交 = `git log` 中 `%s` 提交；「0 增长」以计划 v2 发布（%s）为界。"
        % (BUNDLE_COMMIT_GREP, PLAN_V2_PUBLISHED.strftime("%Y-%m-%d %H:%M")),
        "- 本看板不含 live 网络探针（8103/8787）；live 版本/健康探测属 `tools/trust_chain_kpi.py`。",
        "",
    ]
    return "\n".join(lines) + "\n"


def output_paths(repo: Path, date: str, out_dir: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    if not out.is_absolute():
        out = Path(repo) / out
    return (out / ("%s_周日KPI看板_v1.md" % date),
            out / ("%s_周日KPI看板_v1.json" % date))


def write_outputs(board: dict, repo: Path, out_dir: str = DEFAULT_OUT_DIR) -> tuple[Path, Path]:
    md_path, json_path = output_paths(repo, board["board_date"], out_dir)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(board), encoding="utf-8")
    json_path.write_text(json.dumps(board, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    return md_path, json_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="计划 v2 §5 周日 KPI 看板聚合器（只读）")
    ap.add_argument("--json", action="store_true", help="打印 JSON（默认打印 Markdown）")
    ap.add_argument("--markdown", action="store_true", help="即使 --json 也按 Markdown 打印")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR,
                    help="输出目录（默认 deliverables/，相对主仓）")
    ap.add_argument("--no-write", action="store_true", help="不落盘，只打印")
    ap.add_argument("--date", default=DEFAULT_DATE, help="看板日期（默认 %s）" % DEFAULT_DATE)
    ap.add_argument("--repo", default="", help="主仓路径（默认自动解析）")
    ap.add_argument("--release-root", default="",
                    help="release 根（默认 %s）" % DEFAULT_RELEASE_ROOT)
    ap.add_argument("--temp-dir", default="",
                    help="gate 日志目录（默认系统 TEMP）")
    ap.add_argument("--milestone", default=DEFAULT_MILESTONE,
                    help="目标里程碑（默认 %s）" % DEFAULT_MILESTONE)
    args = ap.parse_args(argv)
    try:
        board = build_board(
            repo=Path(args.repo) if args.repo else None,
            date=args.date,
            release_root=Path(args.release_root) if args.release_root else None,
            temp_dir=Path(args.temp_dir) if args.temp_dir else None,
            milestone=args.milestone,
        )
    except Exception as exc:  # noqa: BLE001 —— 参数/环境错误按 2（unknown）退出
        print("[ERR] 看板聚合失败：%s: %s" % (exc.__class__.__name__, exc), file=sys.stderr)
        return 2
    if not args.no_write:
        md_path, json_path = write_outputs(board, Path(board["repo"]), args.out)
        print("[ok] 已落盘：%s" % md_path, file=sys.stderr)
        print("[ok] 已落盘：%s" % json_path, file=sys.stderr)
    if args.json and not args.markdown:
        print(json.dumps(board, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(board))
    return int(board["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
