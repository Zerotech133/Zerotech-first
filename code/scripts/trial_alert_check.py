"""M8-2 任务/数据过期告警检查（只读，不发外部消息、不触发任务、不写 DB）。

只读输入：
  - C:\\Temp\\zhice_job_status.json（P4-1 任务运行态）
  - Windows 计划任务 ZhiceAcad1Pool（状态/上次/下次/上次结果，只查询不修改）
  - Zhice DB 只读 SELECT：最新池日 / 最新日报日 / 最新 ACAD1 池日

告警规则（逐条可审计，不伪造通过）：
  R1 task_present                计划任务存在
  R2 task_state_ready            计划任务状态 Ready
  R3 task_last_result_zero       上次运行结果 = 0
  R4 task_next_run_present       有下次运行时间
  R5 task_last_run_fresh         上次运行 ≤ 26h（未错过日调度）
  R6 job_status_present          运行态文件存在且可解析、status 合法
  R7 job_not_failed              任务未失败（failed → 告警，带 rc/message）
  R8 job_not_running_timeout     运行中未超时（默认 > 2h 视为未完成超时）
  R9 job_finished_recorded       success 必须有 finished_at
  R10 job_data_date_present      运行态记录数据日
  R11 job_data_date_lag          job data_date 未落后池日/日报日（默认容忍 0 天）
  R12 data_date_pool_mismatch    数据日与池日一致（不一致 → 告警）
  R13 no_success_after_17        17:00（+30min 宽限）后当日必须有 success 上报
  R14 job_stale_26h              运行态新鲜（完成时间 ≤ 26h）
  R15 db_readable                只读 SQL 可连接（否则无法核对数据日）
  R16 report_day_lag             最新日报日未落后池日 > 1 天
  R17 acad1_day_lag              最新 ACAD1 池日未落后池日 > 1 天

输出：
  C:\\Temp\\zhice_alerts.json（机器可读全量）
  C:\\Temp\\zhice_alerts.md（人工摘要）

用法（py312 环境）：
  C:\\项目\\py312\\python.exe scripts\\trial_alert_check.py
  C:\\项目\\py312\\python.exe scripts\\trial_alert_check.py --now 2026-11-02T18:00:00+08:00
  C:\\项目\\py312\\python.exe scripts\\trial_alert_check.py --job-status-file C:\\Temp\\x.json
  C:\\项目\\py312\\python.exe scripts\\trial_alert_check.py --task-name ZhiceAcad1Pool --strict

退出码：0 = 无告警（允许 info/skip）；1 = 有 warning/error/critical 告警；2 = 脚本自身错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import trial_preflight as preflight  # noqa: E402  （复用其只读探测/解析实现）

OUT_JSON = Path(r"C:\Temp\zhice_alerts.json")
OUT_MD = Path(r"C:\Temp\zhice_alerts.md")
CST = preflight.CST
SCHEDULED_TASK_DEFAULT = preflight.SCHEDULED_TASK
JOB_STATUS_DEFAULT = preflight.JOB_STATUS_FILE

JOB_STATUS_VALUES = ("running", "success", "failed")
SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}
SEVERITY_ICON = {"critical": "🛑", "error": "❌", "warning": "⚠️", "info": "ℹ️"}


def now_cst() -> datetime:
    return datetime.now(CST)


def parse_now(text: str | None) -> datetime:
    if not text:
        return now_cst()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"[FAIL] --now 非法：{text}（需 ISO8601，如 2026-11-02T18:00:00+08:00）") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST)


def fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def safe_days_between(later: date | None, earlier: date | None) -> int | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).days


# --------------------------------------------------------------------------- #
# 只读数据读取（复用 trial_preflight 的 job/task 读取；DB 锚点自读以支持 --db-url 测试）
# --------------------------------------------------------------------------- #
def read_db_anchors(db_url: str | None) -> dict:
    import sqlalchemy as sa

    result: dict = {"error": "", "latest_pool_day": None,
                    "latest_report_day": None, "latest_acad1_day": None,
                    "db_url_source": "args" if db_url else "settings"}
    try:
        if db_url:
            engine = sa.create_engine(db_url)
        else:
            from zhice_os_v2.config.settings import get_settings

            engine = sa.create_engine(get_settings().db_main_url)
        with engine.connect() as conn:
            latest_pool = conn.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
            latest_report = conn.execute(sa.text("SELECT max(report_date) FROM v2_selection_report")).scalar()
            latest_acad1 = conn.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        result.update({
            "latest_pool_day": str(latest_pool) if latest_pool else None,
            "latest_report_day": str(latest_report) if latest_report else None,
            "latest_acad1_day": str(latest_acad1) if latest_acad1 else None,
        })
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


# --------------------------------------------------------------------------- #
# 规则引擎
# --------------------------------------------------------------------------- #
class RuleBook:
    def __init__(self) -> None:
        self.results: list[dict] = []
        self.alerts: list[dict] = []

    def record(self, rule_id: str, title: str, status: str, detail: str) -> None:
        self.results.append({"id": rule_id, "title": title, "status": status, "detail": detail})

    def alert(self, rule_id: str, title: str, severity: str, detail: str,
              evidence: dict | None = None, suggestion: str = "") -> None:
        self.alerts.append({
            "id": rule_id, "title": title, "severity": severity,
            "detail": detail, "evidence": evidence or {}, "suggestion": suggestion,
        })
        self.record(rule_id, title, f"alert({severity})", detail)


def evaluate(book: RuleBook, *, now: datetime, job: dict, task: dict, anchors: dict,
             stale_hours: float, task_stale_hours: float, grace_minutes: int,
             data_lag_days: int, expect_hour: int) -> dict:
    today = now.date()
    primary = task.get("primary") or {}
    job_file = (job.get("file") or {}).get("fields") or {}
    job_status = str(job_file.get("status") or "").lower()
    job_data_date = preflight.parse_dt(job_file.get("data_date"))
    finished = preflight.parse_dt(job_file.get("finished_at"))
    started = preflight.parse_dt(job_file.get("started_at"))
    scheduled = preflight.parse_dt(job_file.get("scheduled_at"))
    run_date = (finished or started or scheduled)
    run_day = run_date.date() if run_date else None

    pool_day = date.fromisoformat(anchors["latest_pool_day"]) if anchors.get("latest_pool_day") else None
    report_day = date.fromisoformat(anchors["latest_report_day"]) if anchors.get("latest_report_day") else None
    acad1_day = date.fromisoformat(anchors["latest_acad1_day"]) if anchors.get("latest_acad1_day") else None

    # R1-R5 计划任务
    if not primary.get("found"):
        book.alert("task_present", f"计划任务 {SCHEDULED_TASK_DEFAULT} 存在", "error",
                   f"未找到计划任务（query_error={task.get('query_error') or '—'}）",
                   {"task": SCHEDULED_TASK_DEFAULT},
                   "检查任务是否被删除/改名：Get-ScheduledTask -TaskName ZhiceAcad1Pool")
    else:
        book.record("task_present", f"计划任务 {SCHEDULED_TASK_DEFAULT} 存在", "ok", str(primary.get("path")))
        state = str(primary.get("state"))
        if state == "Ready":
            book.record("task_state_ready", "计划任务状态 Ready", "ok", state)
        else:
            book.alert("task_state_ready", "计划任务状态 Ready", "error",
                       f"当前状态={state or '—'}（非 Ready）", {"state": state},
                       "确认任务未被 Disabled/运行中卡死")
        last_result = primary.get("last_result")
        if last_result in (0, "0"):
            book.record("task_last_result_zero", "上次运行结果 = 0", "ok", f"last_result={last_result}")
        else:
            book.alert("task_last_result_zero", "上次运行结果 = 0", "error",
                       f"last_result={last_result}（上次运行失败）",
                       {"last_run": primary.get("last_run"), "last_result": last_result},
                       "查看任务历史与 C:\\Temp\\zhice_job_status.json 的 rc/steps")
        next_run = primary.get("next_run") or ""
        if next_run:
            book.record("task_next_run_present", "有下次运行时间", "ok", next_run)
        else:
            book.alert("task_next_run_present", "有下次运行时间", "warning",
                       "next_run 为空（可能未设置触发器）", {}, "检查任务触发器")
        last_run_dt = preflight.parse_dt(primary.get("last_run"))
        if last_run_dt:
            age_h = round((now - last_run_dt).total_seconds() / 3600.0, 1)
            if age_h > task_stale_hours:
                book.alert("task_last_run_fresh", f"上次运行 ≤ {task_stale_hours:g}h", "warning",
                           f"上次运行 {fmt_dt(last_run_dt)}，距今 {age_h}h（可能错过调度）",
                           {"last_run": primary.get("last_run"), "age_hours": age_h},
                           "核对计划任务触发器与 8105/DB 可用性")
            else:
                book.record("task_last_run_fresh", f"上次运行 ≤ {task_stale_hours:g}h", "ok",
                            f"{fmt_dt(last_run_dt)}（{age_h}h 前）")
        else:
            book.record("task_last_run_fresh", f"上次运行 ≤ {task_stale_hours:g}h", "skip",
                        "无上次运行记录（任务可能从未运行）")

    # R6-R14 运行态
    file_info = job.get("file") or {}
    if not file_info.get("exists"):
        book.alert("job_status_present", "运行态文件存在且可解析", "error",
                   f"未找到 {file_info.get('path')}", {"path": file_info.get("path")},
                   "等待 17:00 任务运行；或检查 P4-1 是否写入")
    elif file_info.get("parse_error"):
        book.alert("job_status_present", "运行态文件存在且可解析", "error",
                   f"解析失败：{file_info.get('parse_error')}", {"path": file_info.get("path")},
                   "检查 C:\\Temp\\zhice_job_status.json 是否被写坏")
    elif job_status not in JOB_STATUS_VALUES:
        book.alert("job_status_present", "运行态文件存在且可解析", "error",
                   f"status={job_status or '—'}（合法值 running/success/failed）",
                   {"status": job_status}, "检查 P4-1 写入逻辑")
    else:
        book.record("job_status_present", "运行态文件存在且可解析", "ok",
                    f"status={job_status} run_id={job_file.get('run_id') or '—'}")

        if job_status == "failed":
            book.alert("job_not_failed", "任务未失败", "error",
                       f"status=failed rc={job_file.get('rc')} run_id={job_file.get('run_id') or '—'}",
                       {"rc": job_file.get("rc"), "steps": (job.get("file") or {}).get("steps")},
                       "查看 steps 中首个失败步骤；必要时手工重跑（需人工批准）")
        else:
            book.record("job_not_failed", "任务未失败", "ok", f"status={job_status}")

        if job_status == "running":
            if started:
                age_h = (now - started).total_seconds() / 3600.0
                if age_h > stale_hours:
                    book.alert("job_not_running_timeout", f"运行中未超时（≤ {stale_hours:g}h）", "error",
                               f"running 已持续 {age_h:.1f}h（started_at={fmt_dt(started)}），疑似未完成超时",
                               {"started_at": job_file.get("started_at"), "age_hours": round(age_h, 1)},
                               "检查任务进程/日志是否卡死；不要在本脚本触发任务")
                else:
                    book.record("job_not_running_timeout", f"运行中未超时（≤ {stale_hours:g}h）", "ok",
                                f"运行 {age_h:.1f}h（started_at={fmt_dt(started)}）")
            else:
                book.record("job_not_running_timeout", f"运行中未超时（≤ {stale_hours:g}h）", "skip",
                            "running 但无 started_at，无法判断时长")
        elif job_status == "success":
            if finished:
                book.record("job_not_running_timeout", f"运行中未超时（≤ {stale_hours:g}h）", "ok",
                            "非 running，不适用")
            else:
                book.record("job_not_running_timeout", f"运行中未超时（≤ {stale_hours:g}h）", "ok",
                            "非 running，不适用")

        if job_status == "success" and not finished:
            book.alert("job_finished_recorded", "success 必须有 finished_at", "warning",
                       "status=success 但 finished_at 缺失（无法判断完成时间）", {},
                       "检查 P4-1 收尾写入")
        else:
            book.record("job_finished_recorded", "success 必须有 finished_at", "ok",
                        f"finished_at={fmt_dt(finished)}")

        # R10-R12 数据日
        if job_data_date is None:
            book.alert("job_data_date_present", "运行态记录数据日", "error",
                       "job data_date 缺失", {}, "P4-1 应记录 data_date")
        else:
            book.record("job_data_date_present", "运行态记录数据日", "ok", str(job_data_date.date()))
            if pool_day is None:
                book.record("job_data_date_lag", "job data_date 未落后", "skip", "无池日可对比（DB 不可用）")
                book.record("data_date_pool_mismatch", "数据日与池日一致", "skip", "无池日可对比")
            else:
                lag = safe_days_between(pool_day, job_data_date.date())
                report_lag = safe_days_between(report_day, job_data_date.date()) if report_day else None
                if lag is not None and lag > data_lag_days:
                    book.alert("job_data_date_lag", "job data_date 未落后（数据过期）", "error",
                               f"job data_date={job_data_date.date()} 落后最新池日 {pool_day} {lag} 天"
                               f"（容忍 {data_lag_days} 天）；日报日={report_day or '—'}",
                               {"job_data_date": str(job_data_date.date()),
                                "latest_pool_day": str(pool_day),
                                "latest_report_day": str(report_day) if report_day else None,
                                "lag_days": lag},
                               "核对每日任务是否取到最新数据日（数据仓/交易日历）")
                else:
                    book.record("job_data_date_lag", "job data_date 未落后（数据过期）", "ok",
                                f"job={job_data_date.date()} pool={pool_day} lag={lag}天"
                                f"（容忍 {data_lag_days} 天）"
                                + (f" report_lag={report_lag}天" if report_lag is not None else ""))
                if job_data_date.date() != pool_day:
                    severity = "warning" if (lag is not None and lag < 0) else "error"
                    direction = "超前" if (lag is not None and lag < 0) else "落后"
                    book.alert("data_date_pool_mismatch", "数据日与池日一致", severity,
                               f"job data_date={job_data_date.date()} ≠ 最新池日 {pool_day}（{direction} {abs(lag or 0)} 天）",
                               {"job_data_date": str(job_data_date.date()),
                                "latest_pool_day": str(pool_day)},
                               "确认池日与任务数据日口径是否一致（数据仓 vs 选股池）")
                else:
                    book.record("data_date_pool_mismatch", "数据日与池日一致", "ok",
                                f"{job_data_date.date()} == {pool_day}")

        # R13 17:00 后无 success
        expect_dt = datetime.combine(today, datetime.min.time(), tzinfo=CST) + timedelta(hours=expect_hour)
        grace_dt = expect_dt + timedelta(minutes=grace_minutes)
        today_success = bool(job_status == "success" and run_day == today)
        if now < expect_dt:
            book.record("no_success_after_17", f"{expect_hour:02d}:00 后当日有 success", "skip",
                        f"当前 {now.strftime('%H:%M')} 未到 {expect_hour:02d}:00（不适用）")
        elif now < grace_dt and not today_success:
            book.record("no_success_after_17", f"{expect_hour:02d}:00 后当日有 success", "info",
                        f"已过 {expect_hour:02d}:00 未满宽限 {grace_minutes}min，等待任务完成（latest={job_status or '—'}）")
        elif not today_success:
            book.alert("no_success_after_17", f"{expect_hour:02d}:00 后当日有 success", "error",
                       f"当前 {now.strftime('%Y-%m-%d %H:%M')}，{expect_hour:02d}:00 后仍无今日 success 上报"
                       f"（latest status={job_status or '—'} run_day={run_day or '—'}）",
                       {"now": now.isoformat(), "latest_run_day": str(run_day) if run_day else None,
                        "job_status": job_status},
                       f"检查 {expect_hour:02d}:00 计划任务是否触发/失败；任务计划只读，不在此脚本触发")
        else:
            book.record("no_success_after_17", f"{expect_hour:02d}:00 后当日有 success", "ok",
                        f"今日 success：finished_at={fmt_dt(finished)}")

        # R14 运行态新鲜（> 26h 无新上报）
        if finished:
            age_h = (now - finished).total_seconds() / 3600.0
            if age_h > 26:
                book.alert("job_stale_26h", "运行态新鲜（≤ 26h）", "warning",
                           f"最近 success 完成于 {fmt_dt(finished)}，距今 {age_h:.1f}h（可能错过日调度）",
                           {"finished_at": job_file.get("finished_at"), "age_hours": round(age_h, 1)},
                           "检查计划任务调度与上次运行结果")
            else:
                book.record("job_stale_26h", "运行态新鲜（≤ 26h）", "ok", f"{age_h:.1f}h")
        else:
            book.record("job_stale_26h", "运行态新鲜（≤ 26h）", "skip", "无 finished_at")

    # R15-R17 DB 锚点
    if anchors.get("error"):
        book.alert("db_readable", "只读 SQL 可连接数据库", "error", anchors["error"],
                   {"db_url_source": anchors.get("db_url_source")},
                   "检查 5446 端口/DB 实例；本脚本仅 SELECT，不写库")
    else:
        book.record("db_readable", "只读 SQL 可连接数据库", "ok", "SELECT only")
        if pool_day and report_day:
            lag = safe_days_between(pool_day, report_day)
            if lag is not None and lag > 1:
                book.alert("report_day_lag", "最新日报日未落后池日 > 1 天", "warning",
                           f"池日={pool_day} 最新日报日={report_day}（落后 {lag} 天）",
                           {"pool_day": str(pool_day), "report_day": str(report_day), "lag_days": lag},
                           "检查日报生成/分发链路")
            else:
                book.record("report_day_lag", "最新日报日未落后池日 > 1 天", "ok",
                            f"pool={pool_day} report={report_day}")
        else:
            book.record("report_day_lag", "最新日报日未落后池日 > 1 天", "skip", "池日/日报日缺失")
        if pool_day and acad1_day:
            lag = safe_days_between(pool_day, acad1_day)
            if lag is not None and lag > 1:
                book.alert("acad1_day_lag", "最新 ACAD1 池日未落后池日 > 1 天", "warning",
                           f"池日={pool_day} 最新 ACAD1 池日={acad1_day}（落后 {lag} 天）",
                           {"pool_day": str(pool_day), "acad1_day": str(acad1_day), "lag_days": lag},
                           "检查 ACAD1 选池任务")
            else:
                book.record("acad1_day_lag", "最新 ACAD1 池日未落后池日 > 1 天", "ok",
                            f"pool={pool_day} acad1={acad1_day}")
        else:
            book.record("acad1_day_lag", "最新 ACAD1 池日未落后池日 > 1 天", "skip", "池日/ACAD1 池日缺失")

    counts = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    for alert in book.alerts:
        counts[alert["severity"]] = counts.get(alert["severity"], 0) + 1
    bad = counts["critical"] + counts["error"] + counts["warning"]
    verdict = "OK" if bad == 0 else "ALERT"
    return {
        "verdict": verdict,
        "counts": counts,
        "bad_count": bad,
        "rules_total": len(book.results),
        "rules_ok": sum(1 for r in book.results if r["status"] == "ok"),
        "rules_alert": sum(1 for r in book.results if r["status"].startswith("alert")),
    }


# --------------------------------------------------------------------------- #
# Markdown 摘要
# --------------------------------------------------------------------------- #
def render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    alerts = payload["alerts"]
    rules = payload["rule_results"]
    job = payload["inputs"]["job_status"]
    task = payload["inputs"]["scheduled_task"]
    anchors = payload["inputs"]["db_anchors"]
    lines: list[str] = []
    add = lines.append
    add("# Zhice 任务/数据过期告警检查（trial_alert_check）")
    add("")
    add(f"> 生成时间：{payload['generated_at']} ｜ 判定：**{summary['verdict']}**"
        f"（告警 {summary['bad_count']}：critical {summary['counts']['critical']} / "
        f"error {summary['counts']['error']} / warning {summary['counts']['warning']}；"
        f"info {summary['counts']['info']}）")
    add(f"> {payload['disclaimer']}")
    add("")
    add("## 1. 输入快照（只读）")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 检查时间 now | `{payload['now']}` |")
    add(f"| job status 文件 | `{job.get('path')}`（存在={job.get('exists')}，mtime={job.get('mtime') or '—'}） |")
    add(f"| job 字段 | status=`{job.get('fields', {}).get('status')}` / source=`{job.get('fields', {}).get('source')}`"
        f" / data_date=`{job.get('fields', {}).get('data_date')}` / rc=`{job.get('fields', {}).get('rc')}`"
        f" / finished=`{job.get('fields', {}).get('finished_at')}` |")
    add(f"| 计划任务 {payload['task_name']} | found={task.get('found')} state=`{task.get('state')}`"
        f" last=`{task.get('last_run')}` next=`{task.get('next_run')}` result=`{task.get('last_result')}` |")
    add(f"| DB 锚点 | pool_day=`{anchors.get('latest_pool_day')}` report_day=`{anchors.get('latest_report_day')}`"
        f" acad1_day=`{anchors.get('latest_acad1_day')}` error=`{anchors.get('error') or '—'}` |")
    add(f"| 阈值 | stale_hours={payload['thresholds']['stale_hours']} / grace_minutes={payload['thresholds']['grace_minutes']}"
        f" / data_lag_days={payload['thresholds']['data_lag_days']} / expect_hour={payload['thresholds']['expect_hour']}"
        f" / task_stale_hours={payload['thresholds']['task_stale_hours']} |")
    add("")
    add("## 2. 告警")
    add("")
    if not alerts:
        add("- ✅ 无告警（所有检查规则通过或跳过）")
    else:
        ordered = sorted(alerts, key=lambda a: (SEVERITY_ORDER.get(a["severity"], 9), a["id"]))
        for i, alert in enumerate(ordered, 1):
            add(f"### {i}. {SEVERITY_ICON.get(alert['severity'], '•')} [{alert['severity']}] {alert['title']}")
            add("")
            add(f"- 规则：`{alert['id']}`")
            add(f"- 详情：{alert['detail']}")
            if alert.get("evidence"):
                add(f"- 证据：`{json.dumps(alert['evidence'], ensure_ascii=False)}`")
            if alert.get("suggestion"):
                add(f"- 建议：{alert['suggestion']}")
            add("")
    add("## 3. 规则覆盖（逐条）")
    add("")
    add("| # | 规则 | 状态 | 说明 |")
    add("|---|---|---|---|")
    for i, rule in enumerate(rules, 1):
        add(f"| {i} | `{rule['id']}` {rule['title']} | {rule['status']} | {rule['detail']} |")
    add("")
    add("## 4. 只读声明与复现")
    add("")
    add("- " + payload["disclaimer"])
    add(f"- 不发外部消息（无飞书/邮件/webhook 调用）；退出码：0=无告警，1=有告警，2=脚本错误。本次 exit={payload['exit_code']}。")
    add("")
    add("```powershell")
    add("C:\\项目\\py312\\python.exe scripts\\trial_alert_check.py")
    add("```")
    add("")
    add(f"- 机器可读：`{payload['outputs']['json']}`；人工摘要：`{payload['outputs']['md']}`")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M8-2 任务/数据过期告警检查（只读）：job status / 计划任务 / 池日与日报日")
    parser.add_argument("--now", default=None, help="模拟当前时间 ISO8601（默认系统时间）")
    parser.add_argument("--job-status-file", default=str(JOB_STATUS_DEFAULT),
                        help="job status JSON 路径（默认 C:\\Temp\\zhice_job_status.json）")
    parser.add_argument("--task-name", default=SCHEDULED_TASK_DEFAULT,
                        help="Windows 计划任务名（默认 ZhiceAcad1Pool）")
    parser.add_argument("--db-url", default=None, help="只读 DB URL 覆盖（默认 settings.db_main_url）")
    parser.add_argument("--stale-hours", type=float, default=2.0, help="running 超时阈值（小时）")
    parser.add_argument("--task-stale-hours", type=float, default=26.0, help="计划任务上次运行过旧阈值（小时）")
    parser.add_argument("--grace-minutes", type=int, default=30, help="17:00 任务完成宽限（分钟）")
    parser.add_argument("--data-lag-days", type=int, default=0,
                        help="job data_date 允许落后池日天数（默认 0）")
    parser.add_argument("--expect-hour", type=int, default=17, help="每日任务期望完成小时（默认 17）")
    parser.add_argument("--json-out", default=str(OUT_JSON), help="JSON 输出路径")
    parser.add_argument("--md-out", default=str(OUT_MD), help="Markdown 输出路径")
    args = parser.parse_args(argv)

    started = now_cst()
    try:
        now = parse_now(args.now)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # 只读读取 job status / 计划任务（复用 trial_preflight 实现；支持路径/任务名覆盖）
    preflight.JOB_STATUS_FILE = Path(args.job_status_file)
    preflight.SCHEDULED_TASK = args.task_name
    preflight.SUPPLEMENTARY_TASKS = []
    print(f"[trial_alert_check] now={now.isoformat(timespec='seconds')} "
          f"job={args.job_status_file} task={args.task_name}", flush=True)
    job = preflight.job_status_facts()
    task = preflight.scheduled_task_facts()
    primary = task.get("primary") or {}
    anchors = read_db_anchors(args.db_url)
    print(f"[trial_alert_check] job.exists={job['file']['exists']} "
          f"task.found={primary.get('found')} db_error={bool(anchors.get('error'))}", flush=True)

    book = RuleBook()
    summary = evaluate(
        book, now=now, job=job, task=task, anchors=anchors,
        stale_hours=args.stale_hours, task_stale_hours=args.task_stale_hours,
        grace_minutes=args.grace_minutes, data_lag_days=args.data_lag_days,
        expect_hour=args.expect_hour,
    )
    exit_code = 0 if summary["verdict"] == "OK" else 1
    payload = {
        "schema": "zhice_trial_alerts/v1",
        "generated_at": preflight.now_iso(),
        "now": now.isoformat(timespec="seconds"),
        "mode": "read_only",
        "disclaimer": (
            "只读告警检查：仅读 C:\\Temp\\zhice_job_status.json、只查询计划任务状态、只读 SQL(SELECT)；"
            "不触发任务、不写 DB、不改任务计划、不发外部消息；告警如实列出，不伪造。"
        ),
        "read_only": {
            "db_writes": False, "tasks_triggered": False,
            "scheduled_tasks_modified": False, "external_messages_sent": False,
            "config_changed": False, "sql_only_select": True,
        },
        "task_name": args.task_name,
        "thresholds": {
            "stale_hours": args.stale_hours,
            "task_stale_hours": args.task_stale_hours,
            "grace_minutes": args.grace_minutes,
            "data_lag_days": args.data_lag_days,
            "expect_hour": args.expect_hour,
        },
        "inputs": {
            "job_status": {
                "path": job["file"]["path"],
                "exists": job["file"]["exists"],
                "mtime": job["file"].get("mtime"),
                "parse_error": job["file"].get("parse_error"),
                "fields": job["file"].get("fields"),
                "steps": job["file"].get("steps"),
            },
            "scheduled_task": primary,
            "db_anchors": anchors,
        },
        "alerts": book.alerts,
        "rule_results": book.results,
        "summary": summary,
        "outputs": {"json": args.json_out, "md": args.md_out},
        "exit_code": exit_code,
        "elapsed_s": round((now_cst() - started).total_seconds(), 1),
    }

    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(render_markdown(payload), encoding="utf-8")

    print(json.dumps({
        "verdict": summary["verdict"],
        "alerts": [{"id": a["id"], "severity": a["severity"], "detail": a["detail"]}
                   for a in book.alerts],
        "counts": summary["counts"],
        "rules": f"{summary['rules_ok']}/{summary['rules_total']} ok（{summary['rules_alert']} 告警）",
        "json": str(json_out),
        "md": str(md_out),
        "exit_code": exit_code,
    }, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
