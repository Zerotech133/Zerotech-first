# -*- coding: utf-8 -*-
r"""TdxSync 零行/锁跳过看门狗（只读，fail-visible）。

背景（2026-11-02 task-43 核验 + TdxSync 四批 L2 调整）：
1. **零行假绿**：`tdx_sync.py` 增量模式无零行闸——`LastTaskResult=0` 不代表有产出；
2. **共锁静默跳过**：A/B/C/D 四批共用 `%TEMP%\\tdx_sync_core.lock`，拿不到锁打印
   `[lock] SKIP` 并 `exit 0` → 任务显示成功但整批没跑（三批任务级无法发现）；
3. **任务级无告警**：四批只有任务状态，没有数据级证据（part mtime / manifest rows 增量）。

本工具只读取证四件事（不写数据仓、不触发任务、不删锁、不重启服务）：
a) `\\ZeroBound\\TdxSync_{A,B,C,D}` 四任务的 State / LastRunTime / LastTaskResult /
   NextRunTime（PowerShell `Get-ScheduledTask(Info)`，JSON 回传，locale 无关）；
b) `C:\\Temp\\tdx_sync_core.lock`（兼容 `%TEMP%` 同名锁）是否存在与残留时长/pid；
c) 抽样关键表 `data/tdx/<table>` 最新 part mtime + `tdx/_manifest.json` 的 rows 增量
   （对照上次运行基线 `C:\\Temp\\tdx_sync_probe_state.json`）；
d) 汇总 JSON + 退出码（0=OK / 1=异常，fail-visible），每次运行追加 JSONL 到
   `C:\\Temp\\tdx_sync_probe.log`；另写 `C:\\Temp\\tdx_sync_probe_task.log` 由 L2 任务重定向。

判定口径（对齐 `deliverables/2026-11-02_L2调整_TdxSync四批与GlobalFutSync.md` §四
「首夜验收清单」）：
- **18:30（READY_AFTER）之后**：四任务必须**今日已跑**且 `LastTaskResult ∈ {0, 267009}`；
  交易日（`--trading-day auto` = 周一~周五；法定节假日需显式 `--trading-day no`）要求
  关键表至少一表 rows 增量 >0、且至少一表最新 part mtime 晚于当日 17:30（WINDOW_START）；
- **18:30 之前**：只记录、不对「今日未跑」判失败（首夜 17:35 起跑）；
- 锁残留 **>2h**（与 `tdx_sync.py` 自身陈锁阈值一致）→ 失败；<2h 视为可能在跑 → WARN；
- 基线 = **当日窗口前（17:30 前）**的 manifest rows 快照（C:\Temp\tdx_sync_probe_state.json）；
  每小时运行都对比**同一份当日基线**（收盘后不因「与上一小时零增量」误报），跨日/首跑自动重捕；
- 基线缺失时首次运行只建立基线（不判零行），第二次运行起判增量；
- 计划任务查询失败 / manifest 不可读 / 状态文件损坏等 → 计入异常并退出 1（不假绿）。

用法：
  python tools/datawarehouse/tdx_sync_probe.py             # 人类可读
  python tools/datawarehouse/tdx_sync_probe.py --json      # JSON（L2 任务用）
  python tools/datawarehouse/tdx_sync_probe.py --now 2026-11-02T19:05:00+08:00  # 复核用
退出码：0=OK / 1=异常（fail-visible）。

测试：`tools/datawarehouse/tests/test_tdx_sync_probe.py`（全 mock/临时目录，不碰真任务/真数据）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

# ---- 路径与常量（全部只读默认值）------------------------------------------------
try:  # 与数据仓工具族同口径取主仓/数据根（worktree 内运行也会解析回主仓）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import common as _common  # noqa: E402

    REPO = _common.REPO
    DEFAULT_DATA_ROOT = _common.DATA
except Exception:  # noqa: BLE001 —— 独立运行时兜底
    REPO = Path(__file__).resolve().parents[2]
    DEFAULT_DATA_ROOT = REPO / "quant_backtest" / "data"

TASK_PATH = "\\ZeroBound\\"
TASK_NAMES = ("TdxSync_A_高频", "TdxSync_B_财务", "TdxSync_C_低频", "TdxSync_D_缺口")
# 关键表：A 批（高频）每日增量表——交易日必须有 rows 增量；B/C/D 批存在合法零行表，
# 只做任务级检查（口径见模块 docstring）。
KEY_TABLES = (
    "ts_daily",
    "ts_daily_basic",
    "ts_index_daily",
    "ts_moneyflow",
    "ts_margin_detail",
    "ts_hk_daily",
    "ts_fx_daily",
)
LOG_PATH = Path(r"C:\Temp\tdx_sync_probe.log")
STATE_PATH = Path(r"C:\Temp\tdx_sync_probe_state.json")
READY_AFTER = dtime(18, 30)  # 四批 17:35/17:45/17:55/18:05 + 余量
WINDOW_START = dtime(17, 30)  # A 批首跑时间
LOCK_MAX_AGE_H = 2.0  # 与 tdx_sync.py _CORE_LOCK_MAX_AGE_S 一致
ALLOWED_LAST_RESULTS = {0, 267009}  # 0=成功；267009=0x41301 正在运行
NEVER_RUN_RESULT = 267011  # 0x41303 任务尚未运行
EXIT_OK, EXIT_FAIL = 0, 1
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_TASK_QUERY_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out = @()
foreach ($n in @('TdxSync_A_高频','TdxSync_B_财务','TdxSync_C_低频','TdxSync_D_缺口')) {
  $t = Get-ScheduledTask -TaskName $n -TaskPath '\ZeroBound\' -ErrorAction SilentlyContinue
  if (-not $t) { $out += [pscustomobject]@{ name=$n; found=$false }; continue }
  $i = Get-ScheduledTaskInfo -TaskName $n -TaskPath '\ZeroBound\' -ErrorAction SilentlyContinue
  $lr = ''; $nr = ''
  if ($i -and $i.LastRunTime) { $lr = $i.LastRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz') }
  if ($i -and $i.NextRunTime) { $nr = $i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz') }
  $out += [pscustomobject]@{
    name=$n; found=$true; state=[string]$t.State;
    last_run=$lr; next_run=$nr;
    last_result=$(if ($i) { [long]$i.LastTaskResult } else { $null })
  }
}
$out | ConvertTo-Json -Compress -Depth 4
"""


# ---- 基础工具 -------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def _parse_dt(value) -> datetime | None:
    """解析 ISO 时间（容忍空值/1999 未运行占位/末尾 Z）。"""
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone(timedelta(hours=8))


def _safe_print(text: str) -> None:
    """GBK/CP936 控制台也能输出（失败按 UTF-8 字节兜底）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            print(text.encode("ascii", "replace").decode("ascii"))


def _issue(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail}


def default_lock_paths() -> list[Path]:
    """主锁 C:\\Temp\\tdx_sync_core.lock；TEMP 不同时补 %TEMP% 同名锁（tdx_sync 用 TEMP）。"""
    paths = [Path(r"C:\Temp\tdx_sync_core.lock")]
    temp = Path(tempfile.gettempdir()) / "tdx_sync_core.lock"
    if temp not in paths:
        paths.append(temp)
    return paths


# ---- a) 计划任务 -----------------------------------------------------------------
def query_tasks(timeout: int = 60) -> tuple[list[dict], str]:
    """→ (tasks, error)。仅调用 PowerShell 只读查询；失败返回 error（fail-visible）。"""
    if os.name != "nt":
        return [], "非 Windows 环境无法查询计划任务"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _TASK_QUERY_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        return [], "计划任务查询异常: %r" % (exc,)
    if proc.returncode != 0:
        return [], "计划任务查询 rc=%d: %s" % (
            proc.returncode, (proc.stderr or proc.stdout or "")[-300:])
    try:
        return parse_tasks_json(proc.stdout or ""), ""
    except ValueError as exc:
        return [], "计划任务 JSON 解析失败: %s" % (exc,)


def parse_tasks_json(text: str) -> list[dict]:
    """解析 PowerShell ConvertTo-Json 输出（单对象/数组/空），归一化字段。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("输出为空")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("非法 JSON（%s）" % (exc,)) from exc
    items = data if isinstance(data, list) else [data]
    tasks = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("条目不是对象: %r" % (item,))
        last_result = item.get("last_result")
        try:
            last_result = int(last_result) if last_result is not None else None
        except (TypeError, ValueError):
            last_result = None
        tasks.append({
            "name": str(item.get("name") or ""),
            "found": bool(item.get("found", True)),
            "state": str(item.get("state") or ""),
            "last_run": str(item.get("last_run") or ""),
            "next_run": str(item.get("next_run") or ""),
            "last_result": last_result,
        })
    return tasks


def eval_tasks(tasks: list[dict], now: datetime, after_ready: bool,
               window_start: datetime) -> tuple[list[dict], list[dict]]:
    """四任务状态判定 → (tasks 详情, issues)。"""
    issues: list[dict] = []
    by_name = {t.get("name"): t for t in tasks}
    rows: list[dict] = []
    for name in TASK_NAMES:
        task = by_name.get(name)
        if not task:
            issues.append(_issue("task_missing", "fail", "任务未注册: %s%s" % (TASK_PATH, name)))
            rows.append({"name": name, "found": False, "verdict": "missing"})
            continue
        last_run = _parse_dt(task.get("last_run"))
        result = task.get("last_result")
        verdict = "ok"
        if not task.get("found", True):
            verdict = "missing"
            issues.append(_issue("task_missing", "fail", "任务未注册: %s%s" % (TASK_PATH, name)))
        elif result == NEVER_RUN_RESULT:
            if after_ready:
                verdict = "never_run"
                issues.append(_issue("task_never_run", "fail",
                                     "%s 今日未运行（结果码 267011，疑似未到点/被禁用）" % name))
            else:
                verdict = "awaiting_first_run"
        elif result not in ALLOWED_LAST_RESULTS:
            verdict = "failed"
            issues.append(_issue("task_failed", "fail",
                                 "%s LastTaskResult=%s（非 0/运行中）" % (name, result)))
        elif after_ready and (last_run is None or last_run < window_start):
            verdict = "not_run_today"
            issues.append(_issue("task_not_run_today", "fail",
                                 "%s 今日 %s 后无运行记录（last_run=%s）" % (
                                     name, WINDOW_START.strftime("%H:%M"),
                                     _iso(last_run) or "无")))
        elif result == 267009:
            verdict = "running"
        rows.append({"name": name, "found": bool(task.get("found", True)),
                     "state": task.get("state", ""), "last_run": _iso(last_run),
                     "last_result": result, "verdict": verdict})
    return rows, issues


# ---- b) 核心锁 -------------------------------------------------------------------
def eval_locks(paths: list[Path], now: datetime, max_age_h: float) -> tuple[list[dict], list[dict]]:
    """锁残留检查：>max_age_h 判失败（否则 WARN）；不删除、不修改。"""
    issues: list[dict] = []
    rows: list[dict] = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        try:
            st = path.stat()
        except OSError as exc:
            issues.append(_issue("lock_unreadable", "fail", "锁无法 stat: %s (%r)" % (path, exc)))
            continue
        age_h = max(0.0, (now.timestamp() - st.st_mtime) / 3600.0)
        pid = 0
        try:
            pid = int(path.read_text(encoding="ascii", errors="ignore").split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        stale = age_h > max_age_h
        row = {"path": str(path), "exists": True, "age_hours": round(age_h, 2),
               "age_minutes": round(age_h * 60, 1), "pid": pid, "stale": stale}
        rows.append(row)
        if stale:
            issues.append(_issue("stale_lock_residue", "fail",
                                 "锁残留 %.1f 分钟超过阈值 %.1fh（%s，pid=%s）→ 可能整批 SKIP" % (
                                     age_h * 60, max_age_h, path, pid or "?")))
        else:
            issues.append(_issue("lock_recent", "warn",
                                 "锁存在且较新（%.1f 分钟，%s，pid=%s）→ 可能在跑；持续不下需人工看" % (
                                     age_h * 60, path, pid or "?")))
    return rows, issues


# ---- c) 数据级证据（manifest rows + part mtime）-----------------------------------
def read_manifest_rows(data_root: Path) -> tuple[dict, str]:
    """→ ({table: rows}, error)。只读 tdx/_manifest.json。"""
    manifest = Path(data_root) / "tdx" / "_manifest.json"
    if not manifest.is_file():
        return {}, "manifest 不存在: %s" % manifest
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, "manifest 解析失败: %r" % (exc,)
    if not isinstance(data, dict):
        return {}, "manifest 顶层不是对象"
    rows = {}
    for table, meta in data.items():
        if isinstance(meta, dict):
            value = meta.get("rows")
            if isinstance(value, (int, float)):
                rows[str(table)] = int(value)
    return rows, ""


def latest_part(data_root: Path, table: str) -> tuple[str, float]:
    """→ (part 路径, mtime epoch)；无分片返回 ("", 0.0)。"""
    folder = Path(data_root) / "tdx" / table
    if not folder.is_dir():
        return "", 0.0
    best = ("", 0.0)
    try:
        for part in folder.glob("part_*.parquet"):
            mtime = part.stat().st_mtime
            if mtime > best[1]:
                best = (str(part), mtime)
    except OSError:
        pass
    return best


def _load_state(path: Path | None) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path | None, payload: dict) -> str:
    """原子写基线状态（C:\\Temp 下，成功返回 ""；失败返回 error 文本）。"""
    if not path:
        return ""
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        return "状态文件写入失败: %r" % (exc,)


def eval_tables(data_root: Path, manifest_rows: dict, manifest_error: str,
                baseline_rows: dict, baseline_ok: bool, after_ready: bool,
                trading_day: bool, window_start: datetime) -> tuple[list[dict], list[dict], str]:
    """→ (tables 详情, issues, manifest_error)。

    baseline_rows = 当日窗口前基线（{table: rows}）；baseline_ok=False 时本轮不判零行。
    """
    issues: list[dict] = []
    rows: list[dict] = []
    window_ts = window_start.timestamp()
    for table in KEY_TABLES:
        part_path, part_mtime = latest_part(data_root, table)
        rows_now = manifest_rows.get(table)
        base_rows = baseline_rows.get(table)
        base_rows = int(base_rows) if isinstance(base_rows, int) else None
        delta = (rows_now - base_rows) if (rows_now is not None and base_rows is not None) else None
        part_fresh = bool(part_mtime and part_mtime >= window_ts)
        row = {
            "table": table,
            "rows": rows_now,
            "rows_baseline": base_rows,
            "rows_delta": delta,
            "latest_part": Path(part_path).name if part_path else "",
            "part_mtime": _iso(datetime.fromtimestamp(part_mtime).astimezone()) if part_mtime else "",
            "part_fresh_after_window": part_fresh,
            "verdict": "awaiting_window",
        }
        if after_ready and trading_day:
            if delta is None:
                row["verdict"] = "no_baseline"
            else:
                row["verdict"] = "ok" if (delta > 0 and part_fresh) else "suspect"
        elif after_ready and not trading_day:
            row["verdict"] = "non_trading_day"
        rows.append(row)
    if manifest_error:
        issues.append(_issue("manifest_unreadable", "fail", manifest_error))
        return rows, issues, manifest_error
    if after_ready and trading_day and baseline_ok:
        zero = [r["table"] for r in rows if r["verdict"] == "suspect" and (r["rows_delta"] or 0) <= 0]
        if zero and len(zero) == len(KEY_TABLES):
            issues.append(_issue("zero_row_all_key_tables", "fail",
                                 "交易日内所有关键表 rows 无增量（零行假绿）：%s" % ",".join(zero)))
        elif zero:
            issues.append(_issue("zero_row_some_key_tables", "warn",
                                 "部分关键表 rows 无增量：%s" % ",".join(zero)))
        stale = [r["table"] for r in rows if not r["part_fresh_after_window"]]
        if stale and len(stale) == len(KEY_TABLES):
            issues.append(_issue("no_part_updated_since_window", "fail",
                                 "所有关键表最新 part mtime 均早于今日 %s" % WINDOW_START.strftime("%H:%M")))
        elif stale:
            issues.append(_issue("part_stale_some_key_tables", "warn",
                                 "部分关键表最新 part mtime 早于今日 %s：%s" % (
                                     WINDOW_START.strftime("%H:%M"), ",".join(stale))))
    return rows, issues, ""


# ---- 日志 -----------------------------------------------------------------------
def append_log(log_path: Path | None, record: dict) -> str:
    if not log_path:
        return ""
    try:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return ""
    except OSError as exc:
        return "日志写入失败: %r" % (exc,)


# ---- 汇总 -----------------------------------------------------------------------
def collect(*, now: datetime | None = None, tasks: list[dict] | None = None,
            data_root: Path | None = None, lock_paths: list[Path] | None = None,
            state_path: Path | None = STATE_PATH, log_path: Path | None = LOG_PATH,
            ready_after: dtime = READY_AFTER, window_start: dtime = WINDOW_START,
            lock_max_age_h: float = LOCK_MAX_AGE_H, trading_day: str = "auto",
            write_state: bool = True, write_log: bool = True) -> dict:
    """执行一次看门狗检查（只读；仅写 C:\\Temp 状态/日志）。返回报告 dict（code=0/1）。"""
    now = now or _now()
    data_root = Path(data_root) if data_root is not None else Path(DEFAULT_DATA_ROOT)
    lock_paths = [Path(p) for p in (lock_paths if lock_paths is not None else default_lock_paths())]
    after_ready = now >= datetime.combine(now.date(), ready_after, tzinfo=now.tzinfo)
    window_start_dt = datetime.combine(now.date(), window_start, tzinfo=now.tzinfo)
    if trading_day == "auto":
        is_trading_day = now.weekday() < 5
    else:
        is_trading_day = trading_day == "yes"

    issues: list[dict] = []
    task_rows: list[dict] = []
    task_error = ""
    if tasks is None:
        tasks, task_error = query_tasks()
    if task_error:
        issues.append(_issue("task_query_failed", "fail", task_error))
    else:
        task_rows, task_issues = eval_tasks(tasks, now, after_ready, window_start_dt)
        issues.extend(task_issues)

    lock_rows, lock_issues = eval_locks(lock_paths, now, lock_max_age_h)
    issues.extend(lock_issues)

    # 当日窗口前基线（每小时都用同一份，防收盘后「零增量」误报）
    current_rows, manifest_error = read_manifest_rows(data_root)
    today = now.date().isoformat()
    prev_state = _load_state(state_path)
    prev_day_baseline = prev_state.get("day_baseline")
    if not isinstance(prev_day_baseline, dict):
        prev_day_baseline = {}
    prev_rows = prev_day_baseline.get("rows")
    prev_rows = prev_rows if isinstance(prev_rows, dict) else {}
    baseline_established = bool(prev_day_baseline.get("day") == today and prev_rows)
    if baseline_established:
        day_baseline = {t: int(v) for t, v in prev_rows.items() if isinstance(v, int)}
        baseline_meta = {"day": today, "captured_at": str(prev_day_baseline.get("captured_at") or "")}
    else:
        # 首次/跨日/缺失：立即捕获当日基线；若已过窗口，本轮只记录（late capture），下一轮起判定
        day_baseline = {t: current_rows[t] for t in KEY_TABLES if isinstance(current_rows.get(t), int)}
        baseline_meta = {"day": today, "captured_at": _iso(now)}
        if after_ready:
            issues.append(_issue("baseline_late_capture", "warn",
                                 "当日首次运行已过 %s，基线为补捕（本轮不判零行，下一轮起判定）"
                                 % ready_after.strftime("%H:%M")))
    baseline_ok = bool(baseline_established and day_baseline)
    table_rows, table_issues, manifest_error = eval_tables(
        data_root, current_rows, manifest_error, day_baseline, baseline_ok,
        after_ready, is_trading_day, window_start_dt)
    issues.extend(table_issues)

    state_error = ""
    if not manifest_error and current_rows:
        state_payload = {
            "updated_at": _iso(now),
            "window": {"ready_after": ready_after.strftime("%H:%M"),
                       "window_start": window_start.strftime("%H:%M")},
            "day_baseline": {"day": baseline_meta["day"],
                             "captured_at": baseline_meta["captured_at"],
                             "rows": day_baseline},
            "tables": {r["table"]: {"rows": r["rows"], "latest_part": r["latest_part"],
                                    "part_mtime": r["part_mtime"]}
                       for r in table_rows},
        }
        if write_state:
            state_error = _save_state(state_path, state_payload)
            if state_error:
                issues.append(_issue("state_write_failed", "fail", state_error))

    fails = [i for i in issues if i["severity"] == "fail"]
    warns = [i for i in issues if i["severity"] == "warn"]
    code = EXIT_FAIL if fails else EXIT_OK
    diagnostics = []
    if state_error:
        diagnostics.append(state_error)
    if manifest_error:
        diagnostics.append(manifest_error)
    report = {
        "tool": "tdx_sync_probe",
        "generated_at": _iso(now),
        "code": code,
        "window": {
            "ready_after": ready_after.strftime("%H:%M"),
            "window_start": window_start.strftime("%H:%M"),
            "after_ready": after_ready,
            "trading_day": is_trading_day,
            "trading_day_rule": trading_day,
        },
        "tasks": task_rows,
        "lock": lock_rows,
        "baseline": {
            "path": str(state_path) if state_path else "",
            "established_before_run": baseline_established,
            "day": baseline_meta["day"],
            "captured_at": baseline_meta["captured_at"],
            "updated": bool(write_state and not manifest_error and not state_error and current_rows),
            "error": state_error,
        },
        "tables": table_rows,
        "issues": issues,
        "summary": {
            "fail": len(fails),
            "warn": len(warns),
            "tasks_checked": len(task_rows),
            "tables_sampled": len(table_rows),
            "lock_hits": len(lock_rows),
            "rows_delta_total": sum(r["rows_delta"] or 0 for r in table_rows),
            "fail_codes": [i["code"] for i in fails],
            "warn_codes": [i["code"] for i in warns],
        },
        "diagnostics": diagnostics,
    }
    if write_log:
        log_error = append_log(log_path, {
            "ts": report["generated_at"],
            "code": code,
            "fail": len(fails),
            "warn": len(warns),
            "fail_codes": report["summary"]["fail_codes"],
            "rows_delta_total": report["summary"]["rows_delta_total"],
            "tasks": {r["name"]: r.get("verdict") for r in task_rows},
        })
        if log_error:
            report["issues"].append(_issue("log_write_failed", "fail", log_error))
            report["diagnostics"].append(log_error)
            report["summary"]["fail"] += 1
            report["summary"]["fail_codes"].append("log_write_failed")
            report["code"] = code = EXIT_FAIL
    return report


# ---- CLI ------------------------------------------------------------------------
def _parse_hhmm(text: str) -> dtime:
    hour, _, minute = (text or "").partition(":")
    return dtime(int(hour), int(minute))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TdxSync 零行/锁跳过看门狗（只读，fail-visible）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--now", default="", help="覆盖当前时间（ISO，复核/演练用）")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--lock", action="append", default=None, help="锁路径（可多次；默认 C:\\Temp + %%TEMP%%）")
    ap.add_argument("--state", default=str(STATE_PATH))
    ap.add_argument("--log", default=str(LOG_PATH))
    ap.add_argument("--ready-after", default=READY_AFTER.strftime("%H:%M"))
    ap.add_argument("--window-start", default=WINDOW_START.strftime("%H:%M"))
    ap.add_argument("--lock-max-age-hours", type=float, default=LOCK_MAX_AGE_H)
    ap.add_argument("--trading-day", choices=("auto", "yes", "no"), default="auto")
    ap.add_argument("--tasks-json", default="",
                    help="从文件读四任务 JSON（复核/演练用，跳过真查询；格式=query_tasks 输出）")
    ap.add_argument("--no-state", action="store_true", help="不写基线状态（纯只读）")
    ap.add_argument("--no-log", action="store_true", help="不写日志")
    args = ap.parse_args(argv)
    now = _parse_dt(args.now) if args.now else None
    lock_paths = [Path(p) for p in args.lock] if args.lock else None
    tasks = None
    if args.tasks_json:
        try:
            tasks = parse_tasks_json(Path(args.tasks_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _safe_print("任务 JSON 读取/解析失败: %r（fail-visible）" % (exc,))
            return EXIT_FAIL
    report = collect(
        now=now,
        tasks=tasks,
        data_root=Path(args.data_root),
        lock_paths=lock_paths,
        state_path=Path(args.state) if args.state else None,
        log_path=Path(args.log) if args.log else None,
        ready_after=_parse_hhmm(args.ready_after),
        window_start=_parse_hhmm(args.window_start),
        lock_max_age_h=args.lock_max_age_hours,
        trading_day=args.trading_day,
        write_state=not args.no_state,
        write_log=not args.no_log,
    )
    if args.json:
        _safe_print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _safe_print("== tdx_sync_probe == now=%s code=%d" % (report["generated_at"], report["code"]))
        _safe_print("window: after_ready=%s trading_day=%s" % (
            report["window"]["after_ready"], report["window"]["trading_day"]))
        for task in report["tasks"]:
            _safe_print("  task %-18s %-14s last_run=%s result=%s" % (
                task.get("name"), task.get("verdict", ""),
                task.get("last_run", ""), task.get("last_result")))
        for lock in report["lock"]:
            _safe_print("  lock %s age=%.1fmin stale=%s pid=%s" % (
                lock["path"], lock["age_minutes"], lock["stale"], lock["pid"]))
        _safe_print("  tables: rows_delta_total=%s baseline_updated=%s" % (
            report["summary"]["rows_delta_total"], report["baseline"]["updated"]))
        for issue in report["issues"]:
            _safe_print("  [%s] %s: %s" % (issue["severity"], issue["code"], issue["detail"]))
        _safe_print("result: %s" % ("OK" if report["code"] == EXIT_OK else "FAIL"))
    return int(report["code"])


if __name__ == "__main__":
    sys.exit(main())
