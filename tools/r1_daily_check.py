# -*- coding: utf-8 -*-
"""R1 恢复只读检查器（计划任务 ``HK_R1_Daily`` 18:30 节点）。

背景：R1 每日跑批由 Windows 计划任务 ``HK_R1_Daily`` 在 18:30 触发，Action =
``C:\\项目\\py312\\python.exe -X utf8 "C:\\aios\\tools\\hk_r1_daily.py" --append --json``。
18:30 节点需要**只读**确认：任务是否存在/启用、LastRunTime/LastTaskResult/NextRunTime、
R1 日志 rc、台账对账与产物新鲜度。本工具只查询/读取，**不启动 R1、不改任务、不重启服务**。

用法：::

  python -X utf8 tools/r1_daily_check.py                     # Markdown 摘要（默认，人类可读）
  python -X utf8 tools/r1_daily_check.py --json              # JSON（机器可读；CI/门禁）
  python -X utf8 tools/r1_daily_check.py --markdown --no-write
  python -X utf8 tools/r1_daily_check.py --out C:\\Temp\\r1 --max-age-hours 24
  python -X utf8 tools/r1_daily_check.py --task-csv r1_task.csv --ledger <台账> --logs-dir <日志目录>
  # ↑ --task-csv：复用 ``schtasks /Query /TN HK_R1_Daily /V /FO CSV`` 的只读快照（离线/mock）

状态与退出码：``pass`` → 0；``fail`` → 1；``unknown``（查询不可用/文件缺失/无法解析）→ 2。
安全边界（代码强制）：
  - 只用 ``schtasks /Query``（或读取显式快照）读取任务信息，绝不 ``/Run`` ``/Change`` ``/Delete``；
  - 只读台账/日志文件，不写、不追加、不触发 R1；
  - 不重启 8103/8787/任何服务；
  - 输入读不到/解析不了 → 对应判据 ``unknown``（附原文/路径），不伪造 pass/fail。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "r1-daily-check/v1"
REPO = Path(__file__).resolve().parents[1]
TASK_NAME = "HK_R1_Daily"
DEFAULT_EXPECT_TIME = "18:30"
DEFAULT_MAX_AGE_HOURS = 24.0
SCHTASKS_TIMEOUT = 20.0
LEDGER_REL = Path("_reviews_20261026") / "hk_r1_observation_v1.md"
LOGS_REL = Path("_reviews_20261026") / "hk_r1_logs"
LOG_NAME_RE = re.compile(r"^hk_r1_(\d{8})_(\d{6})\.log$")
DATE_ROW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}

# 计划任务 Last Result 常见代码（0x413xx 系列）→ 人话；未知代码原样给十六进制
_TASK_RESULT_NOTES = {
    0: "成功",
    267008: "已就绪（尚未运行过）",
    267009: "正在运行",
    267010: "任务被禁用",
    267011: "尚未运行过",
    267012: "无后续运行计划",
    267013: "任务无后续计划（未运行）",
    267014: "被用户终止",
    2147942401: "0x80070001 函数不正确",
    2147942402: "0x80070002 系统找不到指定文件",
}

NOTES = (
    "只读：仅 schtasks /Query + 读取台账/日志，绝不启动 R1、不改计划任务、不重启服务",
    "判定口径：必需判据有 fail → fail；无 fail 但有 unknown → unknown；全 pass → pass",
    "18:30 节点建议顺序：先看 LastTaskResult（今日跑批结果）；18:30 前查看时它反映的是上一轮结果",
    "台账/日志默认路径从 tools/hk_r1_daily.py 的 LEDGER/LOGS 常量定位（worktree 内自动回落主仓）",
    "输入缺失/不可解析一律 unknown 并附原文，不伪造 pass/fail",
)

_HEADER_ALIASES = {
    "hostname": "hostname", "主机名": "hostname",
    "taskname": "task_name", "task name": "task_name", "任务名": "task_name", "任务名称": "task_name",
    "next run time": "next_run_time", "下次运行时间": "next_run_time",
    "status": "status", "状态": "status",
    "logon mode": "logon_mode", "登录模式": "logon_mode",
    "last run time": "last_run_time", "上次运行时间": "last_run_time",
    "last result": "last_result", "上次结果": "last_result",
    "author": "author", "作者": "author",
    "task to run": "task_to_run", "要运行的任务": "task_to_run",
    "start in": "start_in", "起始于": "start_in",
    "comment": "comment", "注释": "comment",
    "scheduled task state": "scheduled_state", "计划任务状态": "scheduled_state",
    "run as user": "run_as_user", "运行方式用户": "run_as_user",
    "schedule type": "schedule_type", "计划类型": "schedule_type",
    "start time": "start_time", "开始时间": "start_time",
    "start date": "start_date", "开始日期": "start_date",
    "days": "days", "天数": "days",
}

_DT_FORMATS = (
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y/%m/%d", "%Y-%m-%d",
)
_EMPTY_DT = {"", "N/A", "NA", "-", "NONE", "无", "没有", "从未运行", "尚未运行"}


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


def _decode_console(raw: bytes) -> str:
    """schtasks 控制台输出解码：优先 UTF-8，再系统 ANSI（mbcs），再 UTF-16，最后容错。"""
    for enc in ("utf-8-sig", "mbcs", "utf-16", "utf-8"):
        if enc == "mbcs" and os.name != "nt":
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _read_text_any(path: Path) -> str:
    """多编码容错读取（台账/日志可能 UTF-8 BOM 或不一致编码）。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "mbcs", "utf-16", "utf-8"):
        if enc == "mbcs" and os.name != "nt":
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def resolve_main_repo(repo=None) -> Path:
    """把（可能的）linked worktree 解析回主仓根；失败则原样返回。"""
    repo = Path(repo or REPO)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return repo
    if proc.returncode != 0:
        return repo
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            candidate = Path(line[len("worktree "):].strip())
            if candidate.is_dir():
                return candidate
    return repo


def _norm_status(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("pass", "ok", "true"):
        return "pass"
    if text in ("fail", "failed", "false"):
        return "fail"
    return "unknown"


def _crit(cid: str, title: str, status: str, detail: str, *,
          required: bool = True, evidence: dict | None = None) -> dict:
    return {"id": cid, "title": title, "status": _norm_status(status),
            "detail": str(detail or ""), "required": bool(required),
            "evidence": evidence or {}}


# ---------------------------------------------------------------------------
# 定位 R1 台账/日志路径（复用 hk_r1_daily.py 的 LEDGER/LOGS 常量，只读）
# ---------------------------------------------------------------------------
def locate_r1_paths(repo=None) -> dict:
    """定位 hk_r1_daily.py 的输出路径：优先读工具常量，失败回退约定路径。"""
    repo = Path(repo or REPO)
    main = resolve_main_repo(repo)
    fallback_ledger = main.parent / LEDGER_REL
    fallback_logs = main.parent / LOGS_REL
    script = main / "tools" / "hk_r1_daily.py"
    meta = {"repo": str(repo), "main_repo": str(main), "script": str(script),
            "ledger": str(fallback_ledger), "logs": str(fallback_logs),
            "source": "fallback", "error": ""}
    if not script.is_file():
        meta["error"] = "hk_r1_daily.py 不存在（回退约定路径）：%s" % script
        return meta
    try:
        spec = importlib.util.spec_from_file_location("r1_check_hk_r1_daily", script)
        if spec is None or spec.loader is None:
            raise ImportError("无法加载 %s" % script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ledger = Path(getattr(mod, "LEDGER", fallback_ledger))
        logs = Path(getattr(mod, "LOGS", fallback_logs))
    except Exception as exc:  # noqa: BLE001 - 常量读取失败回退，不阻塞
        meta["error"] = "hk_r1_daily.py 常量读取失败（回退约定路径）：%s: %s" % (
            type(exc).__name__, str(exc)[:160])
        return meta
    meta.update({"ledger": str(ledger), "logs": str(logs), "source": "hk_r1_daily.py"})
    return meta


# ---------------------------------------------------------------------------
# 计划任务查询（只读）
# ---------------------------------------------------------------------------
def _norm_header(text) -> str:
    return " ".join(str(text or "").strip().lstrip("\ufeff").lower().split())


def _split_schtasks_csv_line(line: str) -> list[str]:
    """按 schtasks /FO CSV 的引号规则切一行。

    schtasks 的 CSV 对「字段内部的引号」不做转义（例如 Task To Run 里的
    ``"C:\\aios\\tools\\hk_r1_daily.py"``），标准 csv 模块会解析错位。这里用
    「引号只切换引号态、其余字符原样保留」的容错扫描：内层引号被剥离，字段内容
    仍完整（不依赖 schtasks 的转义正确性）。
    """
    fields: list[str] = []
    current: list[str] = []
    in_quotes = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if in_quotes and index + 1 < len(line) and line[index + 1] == '"':
                current.append('"')
                index += 2
                continue
            in_quotes = not in_quotes
            index += 1
            continue
        if char == "," and not in_quotes:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    fields.append("".join(current).strip())
    return fields


def parse_task_csv_text(text: str) -> tuple[dict, str]:
    """解析 ``schtasks /Query /V /FO CSV`` 文本 → (task dict, error)。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return {}, "schtasks 输出为空"
    header = _split_schtasks_csv_line(lines[0])
    data_line = lines[1] if len(lines) > 1 else ""
    data = _split_schtasks_csv_line(data_line)
    if not data_line:
        return {}, "schtasks 输出无数据行"
    mapping: dict[str, int] = {}
    for index, name in enumerate(header):
        key = _HEADER_ALIASES.get(_norm_header(name))
        if key:
            mapping[key] = index
    if not mapping:
        return {}, "schtasks CSV 未识别到关键列（表头可能本地化/格式变化）"
    task = {}
    for key, index in mapping.items():
        task[key] = data[index].strip() if index < len(data) else ""
    if not task.get("task_name"):
        task["task_name"] = ""
    return task, ""


def parse_task_query(rc: int, stdout: str, stderr: str) -> dict:
    """把一次 schtasks 查询结果（rc/stdout/stderr）归一为查询元信息。"""
    meta = {"ok": False, "found": False, "error": "", "task": {}, "rc": rc,
            "raw": (stdout or "")[:4000], "mode": "schtasks"}
    combined = ((stdout or "") + "\n" + (stderr or "")).strip()
    if rc == 0 and (stdout or "").strip():
        task, err = parse_task_csv_text(stdout)
        if err:
            meta["error"] = err
            return meta
        meta.update({"ok": True, "found": True, "task": task})
        return meta
    low = combined.lower()
    not_found_tokens = ("cannot find", "找不到", "does not exist", "不存在", "no such task",
                        "没有与指定条件相符")
    if any(token in low for token in not_found_tokens):
        meta.update({"ok": True, "found": False,
                     "error": "计划任务不存在：%s（只读查询未找到）" % TASK_NAME})
        return meta
    meta["error"] = "schtasks 查询失败（rc=%s）：%s" % (rc, combined[:240] or "-")
    return meta


def query_task(task_name: str = TASK_NAME, timeout: float = SCHTASKS_TIMEOUT,
               csv_path=None) -> dict:
    """只读查询计划任务：``--task-csv`` 快照优先，否则 schtasks /Query（绝不 /Run /Change）。"""
    if csv_path:
        path = Path(csv_path)
        meta = {"ok": False, "found": False, "error": "", "task": {}, "rc": None,
                "mode": "csv", "path": str(path), "raw": ""}
        if not path.is_file():
            meta["error"] = "任务快照 CSV 不可读（不存在）：%s" % path
            return meta
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            meta["error"] = "任务快照 CSV 不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
            return meta
        meta["raw"] = text[:4000]
        task, err = parse_task_csv_text(text)
        if err:
            meta["error"] = "任务快照 CSV 解析失败：%s" % err
            return meta
        meta.update({"ok": True, "found": True, "task": task})
        return meta
    if os.name != "nt":
        return {"ok": False, "found": False, "task": {}, "rc": None, "raw": "",
                "mode": "schtasks",
                "error": "非 Windows 环境：schtasks 不可用（可用 --task-csv 提供只读快照）"}
    cmd = ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "CSV"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "found": False, "task": {}, "rc": None, "raw": "",
                "mode": "schtasks", "cmd": " ".join(cmd),
                "error": "schtasks 查询超时（>%ss）" % timeout}
    except OSError as exc:
        return {"ok": False, "found": False, "task": {}, "rc": None, "raw": "",
                "mode": "schtasks", "cmd": " ".join(cmd),
                "error": "schtasks 无法执行：%s: %s" % (type(exc).__name__, str(exc)[:160])}
    stdout = _decode_console(proc.stdout or b"")
    stderr = _decode_console(proc.stderr or b"")
    meta = parse_task_query(proc.returncode, stdout, stderr)
    meta["cmd"] = " ".join(cmd)
    return meta


# ---------------------------------------------------------------------------
# 字段解析
# ---------------------------------------------------------------------------
def parse_dt(value) -> dt.datetime | None:
    """解析 schtasks 时间（本地时区）；未知格式返回 None（不猜）。"""
    text = str(value or "").strip()
    if text.upper() in _EMPTY_DT or text.lower() in {x.lower() for x in _EMPTY_DT}:
        return None
    for fmt in _DT_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).astimezone()
        except ValueError:
            continue
    return None


def parse_last_result(value) -> int | None:
    """解析 Last Result（支持十进制/0x 十六进制/N/A）；不可解析返回 None。"""
    text = str(value or "").strip()
    if not text or text.upper() in ("N/A", "NA", "-", "NONE"):
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def task_result_note(code: int | None) -> str:
    if code is None:
        return "不可解析"
    note = _TASK_RESULT_NOTES.get(code)
    if note:
        return "%s（%d / 0x%X）" % (note, code, code)
    if 0 < code < 256:
        return "应用退出码 %d（0x%X，非 0 即失败）" % (code, code)
    return "未知代码（%d / 0x%X）" % (code, code)


def _age_hours(now: dt.datetime, moment: dt.datetime) -> float:
    return round((now - moment).total_seconds() / 3600.0, 2)


# ---------------------------------------------------------------------------
# R1 日志 / 台账解析（只读）
# ---------------------------------------------------------------------------
def parse_log_text(text: str) -> dict:
    """解析 hk_r1_daily 落盘日志：每条命令 [rc=N]、超时标记、对账结论。"""
    rcs: list[int] = []
    timeouts = 0
    for line in (text or "").splitlines():
        match = re.match(r"^\s*\[rc=(-?\d+)\]", line)
        if match:
            rcs.append(int(match.group(1)))
        if "[TIMEOUT" in line:
            timeouts += 1
    reconcile = ""
    if "对账 FAIL" in text:
        reconcile = "FAIL"
    elif "对账 OK" in text:
        reconcile = "OK"
    return {"rcs": rcs, "rc_nonzero": [value for value in rcs if value != 0],
            "command_count": len(rcs), "timeouts": timeouts, "reconcile": reconcile}


def collect_logs(logs_dir, now: dt.datetime) -> dict:
    """取最新 ``hk_r1_*.log`` 并解析（只读；目录/日志缺失 → error 而非伪造）。"""
    directory = Path(logs_dir)
    meta = {"dir": str(directory), "exists": directory.is_dir(), "count": 0,
            "latest": None, "error": "", "candidates": []}
    if not meta["exists"]:
        meta["error"] = "日志目录不存在：%s" % directory
        return meta
    try:
        files = [path for path in directory.glob("hk_r1_*.log") if path.is_file()]
    except OSError as exc:
        meta["error"] = "日志目录不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["count"] = len(files)
    if not files:
        meta["error"] = "目录内无 hk_r1_*.log：%s" % directory
        return meta
    try:
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        latest = files[0]
        mtime = latest.stat().st_mtime
        text = _read_text_any(latest)
    except OSError as exc:
        meta["error"] = "最新日志不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    match = LOG_NAME_RE.match(latest.name)
    stamp = match.group(1) if match else ""
    parsed = parse_log_text(text)
    meta["latest"] = {
        "path": str(latest), "name": latest.name, "stamp": stamp,
        "mtime": dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds"),
        "age_hours": round(max(0.0, (now.timestamp() - mtime) / 3600.0), 2),
        "bytes": latest.stat().st_size,
        "tail": "\n".join(text.strip().splitlines()[-4:]),
    }
    meta["latest"].update(parsed)
    return meta


def parse_ledger(path) -> dict:
    """解析 R1 台账 markdown 表格行（日期/湖最新/成交/费用/现金/持仓/总资产/仓位/对账/备注）。"""
    target = Path(path)
    meta = {"path": str(target), "exists": target.is_file(), "rows": [], "count": 0,
            "latest": None, "error": "", "mtime": "", "age_hours": None}
    if not meta["exists"]:
        meta["error"] = "R1 台账不存在：%s" % target
        return meta
    try:
        text = _read_text_any(target)
        mtime = target.stat().st_mtime
    except OSError as exc:
        meta["error"] = "R1 台账不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["mtime"] = dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 10 or not DATE_ROW_RE.match(cells[0]):
            continue
        rows.append({
            "date": cells[0], "lake_latest": cells[1], "fills": cells[2], "fee": cells[3],
            "cash": cells[4], "market_value": cells[5], "total": cells[6],
            "pos_pct": cells[7], "reconcile": cells[8].upper(), "note": cells[9],
        })
    meta["rows"] = rows
    meta["count"] = len(rows)
    meta["latest"] = rows[-1] if rows else None
    return meta


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def _build_criteria(task_query: dict, now: dt.datetime, ledger: dict, log: dict,
                    max_age_hours: float, expect_time: str) -> list[dict]:
    criteria: list[dict] = []
    query_ok = bool(task_query.get("ok"))
    found = bool(task_query.get("found"))
    query_err = str(task_query.get("error") or "")
    task = dict(task_query.get("task") or {})

    # 1) 任务存在
    if not query_ok:
        criteria.append(_crit("task_exists", "计划任务存在（只读查询）", "unknown",
                              "查询不可用：%s" % (query_err or "未知错误"),
                              evidence=task_query))
    elif not found:
        criteria.append(_crit("task_exists", "计划任务存在（只读查询）", "fail",
                              query_err or "任务不存在：%s" % TASK_NAME))
    else:
        criteria.append(_crit("task_exists", "计划任务存在（只读查询）", "pass",
                              "任务存在：%s（查询模式 %s）" % (
                                  task.get("task_name") or TASK_NAME, task_query.get("mode")),
                              evidence={"query_mode": task_query.get("mode")}))

    if not (query_ok and found):
        # 任务未查到：其余任务类判据如实 unknown（不伪造）
        for cid, title in (
                ("task_enabled", "计划任务已启用"),
                ("action_matches", "任务动作符合 R1 口径（hk_r1_daily.py --append）"),
                ("last_result", "LastTaskResult=0（上一轮成功）"),
                ("last_run_freshness", "LastRunTime 新鲜（<= --max-age-hours）"),
                ("next_run", "NextRunTime 有效且在将来"),
                ("next_run_expect_time", "NextRunTime 时刻符合 18:30 口径")):
            criteria.append(_crit(cid, title, "unknown", "任务未查到，无法判定"))
        return criteria

    state = str(task.get("status") or "").strip()
    scheduled = str(task.get("scheduled_state") or "").strip()
    action = str(task.get("task_to_run") or "").strip()
    last_run = parse_dt(task.get("last_run_time"))
    next_run = parse_dt(task.get("next_run_time"))
    last_result = parse_last_result(task.get("last_result"))

    # 2) 任务启用
    if scheduled.lower() == "enabled" or state.lower() == "running":
        criteria.append(_crit("task_enabled", "计划任务已启用", "pass",
                              "Scheduled Task State=%s · Status=%s" % (scheduled or "-", state or "-")))
    elif scheduled.lower() == "disabled":
        criteria.append(_crit("task_enabled", "计划任务已启用", "fail",
                              "任务被禁用（Scheduled Task State=Disabled），18:30 不会触发"))
    else:
        criteria.append(_crit("task_enabled", "计划任务已启用", "unknown",
                              "启用状态不可判定：state=%r scheduled=%r" % (scheduled, state)))

    # 3) 动作口径
    if not action:
        criteria.append(_crit("action_matches", "任务动作符合 R1 口径（hk_r1_daily.py --append）",
                              "unknown", "schtasks 未返回 Task To Run 字段"))
    else:
        ok_action = "hk_r1_daily" in action and "--append" in action
        criteria.append(_crit("action_matches", "任务动作符合 R1 口径（hk_r1_daily.py --append）",
                              "pass" if ok_action else "fail",
                              "Task To Run=%s" % action[:240]))

    # 4) 上一轮结果
    if last_result is None:
        criteria.append(_crit("last_result", "LastTaskResult=0（上一轮成功）", "unknown",
                              "Last Result 不可解析：%r" % (task.get("last_result"),)))
    elif last_result == 0:
        criteria.append(_crit("last_result", "LastTaskResult=0（上一轮成功）", "pass",
                              "LastTaskResult=0（成功）；Last Run=%s" % (task.get("last_run_time") or "-")))
    elif last_result in (267008, 267011, 267010):
        criteria.append(_crit("last_result", "LastTaskResult=0（上一轮成功）",
                              "unknown" if last_result != 267010 else "fail",
                              "LastTaskResult=%s：%s" % (last_result, task_result_note(last_result))))
    elif last_result == 267009:
        criteria.append(_crit("last_result", "LastTaskResult=0（上一轮成功）", "pass",
                              "任务正在运行（0x41301），当前轮未结束"))
    else:
        criteria.append(_crit("last_result", "LastTaskResult=0（上一轮成功）", "fail",
                              "LastTaskResult=%s：%s" % (last_result, task_result_note(last_result))))

    # 5) LastRunTime 新鲜度
    if last_run is None:
        criteria.append(_crit("last_run_freshness", "LastRunTime 新鲜（<= --max-age-hours）",
                              "unknown", "Last Run Time 不可解析：%r" % (task.get("last_run_time"),)))
    else:
        age = _age_hours(now, last_run)
        if age < -1.0:
            criteria.append(_crit("last_run_freshness", "LastRunTime 新鲜（<= --max-age-hours）",
                                  "fail", "Last Run Time 在未来（%s），时钟/时区异常"
                                  % last_run.isoformat(timespec="seconds")))
        elif age > max_age_hours:
            criteria.append(_crit("last_run_freshness", "LastRunTime 新鲜（<= --max-age-hours）",
                                  "fail", "Last Run %s 距今 %.2fh > %.2fh（每日任务疑似断档）"
                                  % (last_run.isoformat(timespec="seconds"), age, max_age_hours)))
        else:
            criteria.append(_crit("last_run_freshness", "LastRunTime 新鲜（<= --max-age-hours）",
                                  "pass", "Last Run %s 距今 %.2fh <= %.2fh"
                                  % (last_run.isoformat(timespec="seconds"), age, max_age_hours)))

    # 6) NextRunTime
    if str(task.get("next_run_time") or "").strip().upper() in ("N/A", "NA", "-", "NONE"):
        criteria.append(_crit("next_run", "NextRunTime 有效且在将来", "fail",
                              "NextRunTime=%r（无后续运行计划，18:30 不会触发）"
                              % (task.get("next_run_time"),)))
    elif next_run is None:
        criteria.append(_crit("next_run", "NextRunTime 有效且在将来", "unknown",
                              "Next Run Time 不可解析：%r" % (task.get("next_run_time"),)))
    elif next_run <= now:
        criteria.append(_crit("next_run", "NextRunTime 有效且在将来", "fail",
                              "NextRunTime 已过期：%s <= now %s"
                              % (next_run.isoformat(timespec="seconds"),
                                 now.isoformat(timespec="seconds"))))
    else:
        criteria.append(_crit("next_run", "NextRunTime 有效且在将来", "pass",
                              "NextRunTime=%s（%.2fh 后）"
                              % (next_run.isoformat(timespec="seconds"),
                                 _age_hours(next_run, now))))

    # 7) 18:30 时刻口径
    if expect_time:
        if next_run is None:
            criteria.append(_crit("next_run_expect_time", "NextRunTime 时刻符合 %s 口径" % expect_time,
                                  "unknown", "NextRunTime 不可解析，无法比对时刻"))
        else:
            actual = next_run.strftime("%H:%M")
            criteria.append(_crit("next_run_expect_time", "NextRunTime 时刻符合 %s 口径" % expect_time,
                                  "pass" if actual == expect_time else "fail",
                                  "NextRunTime 时刻=%s（期望 %s）" % (actual, expect_time)))
    else:
        criteria.append(_crit("next_run_expect_time", "NextRunTime 时刻符合 18:30 口径",
                              "unknown", "--expect-time 为空，未做时刻比对（配置项）"))

    # ---- R1 日志 ----
    latest = log.get("latest") or {}
    # 8) 日志存在
    if not log.get("exists"):
        criteria.append(_crit("log_present", "R1 日志存在", "unknown",
                              log.get("error") or "日志目录缺失", evidence={"dir": log.get("dir")}))
    elif not latest:
        criteria.append(_crit("log_present", "R1 日志存在", "unknown",
                              log.get("error") or "无 hk_r1_*.log", evidence={"dir": log.get("dir")}))
    else:
        criteria.append(_crit("log_present", "R1 日志存在", "pass",
                              "最新日志 %s（%s，age=%.2fh）" % (
                                  latest.get("name"), latest.get("mtime"), latest.get("age_hours")),
                              evidence={"path": latest.get("path")}))
    # 9) 日志 rc
    if not latest:
        criteria.append(_crit("log_rc", "日志内全部命令 rc=0", "unknown", "无最新日志可解析"))
    elif latest.get("timeouts"):
        criteria.append(_crit("log_rc", "日志内全部命令 rc=0", "fail",
                              "日志含 %d 处 TIMEOUT 标记：%s" % (latest["timeouts"], latest.get("name"))))
    elif not latest.get("rcs"):
        criteria.append(_crit("log_rc", "日志内全部命令 rc=0", "unknown",
                              "日志无 [rc=N] 行（可能截断/未跑完）：%s" % latest.get("name")))
    elif latest.get("rc_nonzero"):
        criteria.append(_crit("log_rc", "日志内全部命令 rc=0", "fail",
                              "日志含非 0 rc：%s（%s）" % (latest["rc_nonzero"], latest.get("name"))))
    else:
        criteria.append(_crit("log_rc", "日志内全部命令 rc=0", "pass",
                              "全部 %d 条命令 rc=0（%s）" % (
                                  latest.get("command_count", 0), latest.get("name"))))
    # 10) 日志新鲜度
    if latest:
        age = latest.get("age_hours")
        criteria.append(_crit("log_freshness", "日志新鲜（<= --max-age-hours）",
                              "pass" if age is not None and age <= max_age_hours else "fail",
                              "最新日志 age=%.2fh（阈值 %.2fh）" % (age or 0.0, max_age_hours)))
    else:
        criteria.append(_crit("log_freshness", "日志新鲜（<= --max-age-hours）", "unknown",
                              "无最新日志可判定"))
    # 11) 日志日期 ⇄ LastRunTime 对齐
    if not latest or last_run is None:
        criteria.append(_crit("log_matches_last_run", "日志日期与 LastRunTime 对齐", "unknown",
                              "最新日志或 LastRunTime 缺失"))
    else:
        log_stamp = latest.get("stamp") or ""
        last_stamp = last_run.strftime("%Y%m%d")
        if log_stamp == last_stamp:
            criteria.append(_crit("log_matches_last_run", "日志日期与 LastRunTime 对齐", "pass",
                                  "日志 %s ⇄ LastRun %s" % (latest.get("name"), last_run.date())))
        elif last_result not in (None, 0):
            criteria.append(_crit("log_matches_last_run", "日志日期与 LastRunTime 对齐", "unknown",
                                  "上一轮失败（LastResult=%s），日志日期 %s ≠ LastRun %s（失败可能未落盘）"
                                  % (last_result, log_stamp or "-", last_run.date())))
        else:
            criteria.append(_crit("log_matches_last_run", "日志日期与 LastRunTime 对齐", "fail",
                                  "日志日期 %s ≠ LastRun %s（成功轮缺对应日志）"
                                  % (log_stamp or "-", last_run.date())))
    # 12) 日志对账结论
    reconcile_log = str(latest.get("reconcile") or "") if latest else ""
    if not latest:
        criteria.append(_crit("log_reconcile", "日志对账结论 OK", "unknown", "无最新日志"))
    elif reconcile_log == "OK":
        criteria.append(_crit("log_reconcile", "日志对账结论 OK", "pass", "日志内对账 OK"))
    elif reconcile_log == "FAIL":
        criteria.append(_crit("log_reconcile", "日志对账结论 OK", "fail",
                              "日志内出现「对账 FAIL」：%s" % latest.get("name")))
    else:
        criteria.append(_crit("log_reconcile", "日志对账结论 OK", "unknown",
                              "日志未见对账结论（可能未跑到 status 步）：%s" % latest.get("name")))

    # ---- R1 台账 ----
    ledger_latest = ledger.get("latest") or {}
    # 13) 台账存在
    criteria.append(_crit("ledger_present", "R1 台账存在", "pass" if ledger.get("exists") else "fail",
                          "台账 %s" % (ledger.get("path") if ledger.get("exists")
                                     else (ledger.get("error") or "台账缺失"))))
    # 14) 台账新鲜度
    ledger_age = ledger.get("age_hours")
    if not ledger.get("exists"):
        criteria.append(_crit("ledger_freshness", "台账新鲜（<= --max-age-hours）", "unknown",
                              "台账缺失，无法判定新鲜度"))
    else:
        criteria.append(_crit("ledger_freshness", "台账新鲜（<= --max-age-hours）",
                              "pass" if ledger_age is not None and ledger_age <= max_age_hours else "fail",
                              "台账 age=%.2fh（阈值 %.2fh）" % (ledger_age or 0.0, max_age_hours)))
    # 15) 台账最新行对账
    if not ledger_latest:
        criteria.append(_crit("ledger_reconcile", "台账最新行对账 OK", "unknown",
                              "台账无观察行（%s）" % (ledger.get("error") or "空")))
    else:
        rec = str(ledger_latest.get("reconcile") or "").upper()
        criteria.append(_crit("ledger_reconcile", "台账最新行对账 OK",
                              "pass" if rec == "OK" else ("fail" if rec == "FAIL" else "unknown"),
                              "最新行 %s 对账=%s（总资产=%s）"
                              % (ledger_latest.get("date"), rec or "-", ledger_latest.get("total"))))
    # 16) 台账是否覆盖 LastRunTime 当日
    if not ledger_latest or last_run is None:
        criteria.append(_crit("ledger_row_for_last_run", "台账覆盖 LastRunTime 当日", "unknown",
                              "台账行或 LastRunTime 缺失"))
    else:
        dates = {row["date"] for row in ledger.get("rows") or []}
        last_date = last_run.strftime("%Y-%m-%d")
        if last_date in dates:
            criteria.append(_crit("ledger_row_for_last_run", "台账覆盖 LastRunTime 当日", "pass",
                                  "台账含 %s 行" % last_date))
        elif last_result not in (None, 0):
            criteria.append(_crit("ledger_row_for_last_run", "台账覆盖 LastRunTime 当日", "unknown",
                                  "上一轮失败（LastResult=%s），台账缺 %s 行属可解释（需手工补跑）"
                                  % (last_result, last_date)))
        else:
            criteria.append(_crit("ledger_row_for_last_run", "台账覆盖 LastRunTime 当日", "fail",
                                  "台账缺 %s 行（LastResult=0 但无观察行）" % last_date))
    return criteria


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def check_r1(*, task_query=None, task_csv=None, task_name: str = TASK_NAME,
             now: dt.datetime | None = None, ledger=None, logs_dir=None, repo=None,
             max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
             expect_time: str = DEFAULT_EXPECT_TIME,
             timeout: float = SCHTASKS_TIMEOUT) -> dict:
    """只读检查 R1 恢复状态，返回结构化报告（不抛异常、不写文件）。"""
    started = time.perf_counter()
    now = now or dt.datetime.now().astimezone()
    repo = Path(repo or REPO)
    if ledger is not None and logs_dir is not None:
        locate = {"repo": str(repo), "main_repo": "", "source": "explicit",
                  "ledger": str(ledger), "logs": str(logs_dir), "error": ""}
    else:
        locate = locate_r1_paths(repo)
    ledger_path = Path(ledger) if ledger else Path(locate["ledger"])
    logs_path = Path(logs_dir) if logs_dir else Path(locate["logs"])
    if task_query is None:
        task_query = query_task(task_name, timeout=timeout, csv_path=task_csv)
    ledger_meta = parse_ledger(ledger_path)
    ledger_meta["age_hours"] = (round(max(0.0, (now.timestamp()
                                                - dt.datetime.fromisoformat(ledger_meta["mtime"]).timestamp()) / 3600.0), 2)
                                if ledger_meta.get("mtime") else None)
    log_meta = collect_logs(logs_path, now)
    criteria = _build_criteria(task_query, now, ledger_meta, log_meta,
                               float(max_age_hours), str(expect_time or ""))
    counts = {
        "pass": sum(1 for item in criteria if item["status"] == "pass"),
        "fail": sum(1 for item in criteria if item["status"] == "fail"),
        "unknown": sum(1 for item in criteria if item["status"] == "unknown"),
        "total": len(criteria),
    }
    fails = [item["id"] for item in criteria if item["required"] and item["status"] == "fail"]
    unknowns = [item["id"] for item in criteria if item["required"] and item["status"] == "unknown"]
    verdict = "fail" if fails else ("unknown" if unknowns else "pass")
    task = dict(task_query.get("task") or {})
    last_run = parse_dt(task.get("last_run_time"))
    next_run = parse_dt(task.get("next_run_time"))
    last_result = parse_last_result(task.get("last_result"))
    report = {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "now": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y%m%d"),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "verdict": verdict,
        "passed": verdict == "pass",
        "counts": counts,
        "gaps": [{"id": item["id"], "status": item["status"],
                  "required": item["required"], "detail": item["detail"]}
                 for item in criteria if item["status"] != "pass"],
        "task": {
            "name": task_name,
            "query_mode": task_query.get("mode"),
            "query_ok": bool(task_query.get("ok")),
            "found": bool(task_query.get("found")),
            "query_error": task_query.get("error") or "",
            "state": task.get("status"),
            "scheduled_state": task.get("scheduled_state"),
            "logon_mode": task.get("logon_mode"),
            "run_as_user": task.get("run_as_user"),
            "task_to_run": task.get("task_to_run"),
            "last_run_time": last_run.isoformat(timespec="seconds") if last_run else None,
            "last_run_raw": task.get("last_run_time"),
            "last_result": last_result,
            "last_result_note": task_result_note(last_result),
            "next_run_time": next_run.isoformat(timespec="seconds") if next_run else None,
            "next_run_raw": task.get("next_run_time"),
        },
        "ledger": ledger_meta,
        "log": log_meta,
        "locate": locate,
        "thresholds": {"max_age_hours": float(max_age_hours), "expect_time": str(expect_time or "")},
        "criteria": criteria,
        "notes": list(NOTES),
    }
    return report


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _status_label(status) -> str:
    return STATUS_LABEL.get(str(status), "UNKNOWN")


def render_markdown(report: dict) -> str:
    task = report.get("task") or {}
    counts = report.get("counts") or {}
    thresholds = report.get("thresholds") or {}
    ledger = report.get("ledger") or {}
    log = report.get("log") or {}
    latest_log = log.get("latest") or {}
    latest_ledger = ledger.get("latest") or {}
    lines = [
        "# R1 恢复只读检查（HK_R1_Daily 18:30 节点）",
        "",
        "- 检查时间：%s" % report.get("generated_at"),
        "- 计划任务：`%s`（查询模式 %s）" % (task.get("name"), task.get("query_mode")),
        "- 口径：LastRunTime 新鲜阈值 %.1fh · NextRunTime 时刻 %s · **只读，不启动/不改任务**"
        % (thresholds.get("max_age_hours") or 0.0, thresholds.get("expect_time") or "-"),
        "",
        "**判定：%s**（通过 %s / 失败 %s / 未知 %s，共 %s 项）" % (
            str(report.get("verdict") or "unknown").upper(),
            counts.get("pass"), counts.get("fail"), counts.get("unknown"), counts.get("total")),
        "",
        "## 计划任务状态（只读查询）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| Status / Scheduled State | %s / %s |" % (
            _md_cell(task.get("state")), _md_cell(task.get("scheduled_state"))),
        "| Last Run Time | %s（原始 %r）|" % (
            _md_cell(task.get("last_run_time")), task.get("last_run_raw")),
        "| Last Result | %s · %s |" % (
            _md_cell(task.get("last_result")), _md_cell(task.get("last_result_note"))),
        "| Next Run Time | %s（原始 %r）|" % (
            _md_cell(task.get("next_run_time")), task.get("next_run_raw")),
        "| Logon Mode / Run As | %s / %s |" % (
            _md_cell(task.get("logon_mode")), _md_cell(task.get("run_as_user"))),
        "| Task To Run | %s |" % _md_cell(task.get("task_to_run")),
        "",
        "## 判据表",
        "",
        "| # | 判据 | 必需 | 状态 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(report.get("criteria") or [], 1):
        lines.append("| %d | %s | %s | %s | %s |" % (
            index, _md_cell(item.get("title")), "是" if item.get("required") else "否",
            _status_label(item.get("status")), _md_cell(item.get("detail"))))
    lines += ["", "## R1 产物（只读）", ""]
    lines.append("- 台账：`%s`（%s，行数 %s，age=%sh）" % (
        ledger.get("path"), "存在" if ledger.get("exists") else "缺失",
        ledger.get("count"), ledger.get("age_hours")))
    if latest_ledger:
        lines.append("- 台账最新行：%s · 湖最新=%s · 总资产=%s · 对账=%s" % (
            latest_ledger.get("date"), latest_ledger.get("lake_latest"),
            latest_ledger.get("total"), latest_ledger.get("reconcile")))
    lines.append("- 日志目录：`%s`（%s，共 %s 份）" % (
        log.get("dir"), "存在" if log.get("exists") else "缺失", log.get("count")))
    if latest_log:
        lines.append("- 最新日志：%s · mtime=%s · age=%sh · rc=%s · 对账=%s" % (
            latest_log.get("name"), latest_log.get("mtime"), latest_log.get("age_hours"),
            latest_log.get("rcs"), latest_log.get("reconcile") or "-"))
        tail = str(latest_log.get("tail") or "").strip()
        if tail:
            lines.append("")
            lines.append("```")
            lines.extend(tail.splitlines()[-4:])
            lines.append("```")
    lines += ["", "## 缺口清单（%d）" % len(report.get("gaps") or []), ""]
    gaps = report.get("gaps") or []
    if gaps:
        lines += ["| # | 判据 | 必需 | 状态 | 说明 |", "|---|---|---|---|---|"]
        for index, gap in enumerate(gaps, 1):
            lines.append("| %d | %s | %s | %s | %s |" % (
                index, _md_cell(gap.get("id")), "是" if gap.get("required") else "否",
                _status_label(gap.get("status")), _md_cell(gap.get("detail"))))
    else:
        lines.append("无缺口（全部判据 PASS）。")
    lines += ["", "## 说明", ""]
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


def output_paths(out_dir, date=None) -> tuple[Path, Path]:
    """``<out>/r1_daily_check_<date>.md/.json``（date 缺省用今天 YYYYMMDD）。"""
    stamp = str(date).strip() if date else dt.date.today().strftime("%Y%m%d")
    directory = Path(out_dir)
    return (directory / ("r1_daily_check_%s.md" % stamp),
            directory / ("r1_daily_check_%s.json" % stamp))


def write_report(report: dict, out_dir) -> dict:
    """把报告写入 markdown + json（只写这两个文件）。"""
    md_path, json_path = output_paths(out_dir, report.get("date"))
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_now(text: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(str(text).strip())
    return moment.astimezone() if moment.tzinfo is None else moment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="R1 恢复只读检查（HK_R1_Daily 18:30 节点；只读，不启动/不改任务）")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="stdout 输出 JSON（机器可读）")
    output.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    parser.add_argument("--no-write", action="store_true", help="即使给了 --out 也不落盘")
    parser.add_argument("--out", default="", help="可选：报告落盘目录（r1_daily_check_<date>.md/.json）")
    parser.add_argument("--task-csv", default="",
                        help="复用 schtasks /Query /V /FO CSV 的只读快照（离线/mock；缺省实时查询）")
    parser.add_argument("--ledger", default="", help="R1 台账路径（缺省从 hk_r1_daily.py 常量定位）")
    parser.add_argument("--logs-dir", default="", help="R1 日志目录（缺省从 hk_r1_daily.py 常量定位）")
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS,
                        help="LastRun/日志/台账新鲜度阈值小时（默认 %.1f）" % DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--expect-time", default=DEFAULT_EXPECT_TIME,
                        help="NextRunTime 期望时刻 HH:MM（默认 %s；空串关闭比对）" % DEFAULT_EXPECT_TIME)
    parser.add_argument("--timeout", type=float, default=SCHTASKS_TIMEOUT,
                        help="schtasks 查询超时秒（默认 %s）" % SCHTASKS_TIMEOUT)
    parser.add_argument("--now", default="", help="可选：固定当前时间（ISO，例如 2026-10-31T18:35:00+08:00）")
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    try:
        now = _parse_now(args.now) if args.now else None
    except ValueError as exc:
        print("参数错误：--now 非 ISO 时间：%s（%s）" % (args.now, exc), file=sys.stderr)
        return 2
    try:
        report = check_r1(
            task_csv=(args.task_csv or None),
            now=now,
            ledger=(args.ledger or None),
            logs_dir=(args.logs_dir or None),
            max_age_hours=float(args.max_age_hours),
            expect_time=str(args.expect_time),
            timeout=float(args.timeout),
        )
    except Exception as exc:  # noqa: BLE001 - 环境异常也给出结构化 unknown 报告
        report = {
            "schema": SCHEMA,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date": dt.date.today().strftime("%Y%m%d"),
            "verdict": "unknown", "passed": False, "criteria": [], "gaps": [], "counts": {},
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
        }
    written = {}
    if args.out and not args.no_write and report.get("criteria"):
        try:
            written = write_report(report, args.out)
        except OSError as exc:
            print("[warn] 报告落盘失败（不影响判定）：%s" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(report) if report.get("criteria")
              else "R1 检查失败（unknown）：%s" % report.get("error"))
    if written:
        print("[r1-daily-check] 已写入 %s / %s" % (
            written.get("markdown"), written.get("json")), file=sys.stderr)
    verdict = str(report.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
