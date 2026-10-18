# -*- coding: utf-8 -*-
"""进化账本只读投影（驾驶舱·复盘舱数据面二期）。

聚合三个数据面，供 GET /api/evolution 投影（主会话负责挂载）：

1. 告警闭环 automation_runs/execution_watchdog/<任务名>.json
   （{"last_alert_ts","error_code","ticket_id"}，last_run.json 是巡检器
   自身记录，跳过）；
2. 工单账本 agent_platform/data/org/tasks/tasks_YYYYMM.jsonl
   （TaskStore 按月账本，告警 ticket_id ↔ 工单 task_id 对齐，统计闭环率；
   闭环 = status ∈ {done, closed, cancelled}，以勘察到的真实枚举为准）；
3. 影子账本 agent_platform/data/shadow/shadow_ledger.jsonl
   （勘察实况：无 verdict 字段，落账行为 status ∈ {done,...}，run_id/date/
   ts——status=done 计 pass，pending 计 pending，fail/error 计 fail，
   其余计 other）。

红线（沿用 api_tickets 判例）：
- **纯只读 + fail-visible**：目录缺失/坏行不抛错，errors 里说明，HTTP 恒 200；
- 不 import 引擎链（engine/tasks.py 等），直接读文件面；
- errors="replace" 逐行读，坏行跳过计数，宁少勿假；
- 不提供任何写路径。

Windows 编码判例：jsonl 均为 UTF-8。
"""

import json
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 告警目录（execution_watchdog 每任务一 JSON）
WATCHDOG_DIR = _REPO_ROOT / "automation_runs" / "execution_watchdog"

#: TaskStore 按月账本目录
TASKS_DIR = _REPO_ROOT / "agent_platform" / "data" / "org" / "tasks"

#: 影子账本（勘察实况在 data/shadow/ 下；兼容旧路径 data/shadow_ledger.jsonl）
_SHADOW_CANDIDATES = (
    _REPO_ROOT / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl",
    _REPO_ROOT / "agent_platform" / "data" / "shadow_ledger.jsonl",
)

#: 巡检器自身运行记录（非告警），读告警时跳过
_RUNNER_FILE = "last_run.json"

#: 闭环工单状态枚举（真仓勘察：open/doing/done/cancelled）
_CLOSED_STATUSES = frozenset({"done", "closed", "cancelled"})

#: 影子账本 status → verdict 归并（真仓勘察现值只有 done）
_SHADOW_VERDICT_MAP = {
    "done": "pass", "pass": "pass", "ok": "pass",
    "pending": "pending",
    "fail": "fail", "failed": "fail", "error": "fail",
}

#: recent 卡面条数上限
RECENT_LIMIT = 10


def _read_jsonl(path):
    """读一个 jsonl 文件，返回 (recs, skipped_bad)。

    坏行（非 JSON / 非 dict）跳过计数，不抛错（api_capability 判例）。
    """
    recs = []
    skipped = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        raise
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(rec, dict):
            recs.append(rec)
        else:
            skipped += 1
    return recs, skipped


def _load_alerts(watchdog_dir, errors):
    """读告警目录，返回告警行列表（新在前）。

    每任务一 JSON：task=文件名、date/rc/ticket_id 取自文件内容。
    目录缺失 → 记入 errors（fail-visible）。
    """
    d = Path(watchdog_dir)
    if not d.is_dir():
        errors.append("告警目录不存在: %s" % d)
        return []
    alerts = []
    for f in sorted(d.glob("*.json")):
        if f.name == _RUNNER_FILE:
            continue  # 巡检器自身记录，非告警
        try:
            rec = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append("告警文件读取失败 %s: %s" % (f.name, e))
            continue
        if not isinstance(rec, dict):
            errors.append("告警文件非对象 %s" % f.name)
            continue
        ts = str(rec.get("last_alert_ts") or "")
        alerts.append({
            "date": ts[:10] if ts else "",
            "ts": ts,
            "task": f.stem,
            "rc": rec.get("error_code"),
            "ticket_id": rec.get("ticket_id"),
        })
    alerts.sort(key=lambda a: a["ts"], reverse=True)  # 新在前
    return alerts


def _load_ticket_status(tasks_dir, errors):
    """读全部月度账本，返回 {task_id: status} 映射。"""
    d = Path(tasks_dir)
    if not d.is_dir():
        errors.append("工单目录不存在: %s" % d)
        return {}
    status_by_id = {}
    for f in sorted(d.glob("tasks_*.jsonl"), reverse=True):
        try:
            recs, _ = _read_jsonl(f)
        except OSError as e:
            errors.append("工单账本读取失败 %s: %s" % (f.name, e))
            continue
        for rec in recs:
            tid = rec.get("task_id")
            if tid:
                status_by_id[str(tid)] = rec.get("status")
    return status_by_id


def _load_shadow(shadow_path, errors):
    """读影子账本，返回 (rows 数, by_verdict, latest_date, latest_run_id)。"""
    path = Path(shadow_path) if shadow_path else None
    if path is None:
        for cand in _SHADOW_CANDIDATES:
            if cand.is_file():
                path = cand
                break
    if path is None or not Path(path).is_file():
        errors.append("影子账本不存在（%s）" %
                      " 或 ".join(str(c) for c in _SHADOW_CANDIDATES))
        return 0, {"pass": 0, "fail": 0, "pending": 0, "other": 0}, None, None
    try:
        recs, _ = _read_jsonl(path)
    except OSError as e:
        errors.append("影子账本读取失败: %s" % e)
        return 0, {"pass": 0, "fail": 0, "pending": 0, "other": 0}, None, None
    by_verdict = {"pass": 0, "fail": 0, "pending": 0, "other": 0}
    latest_date = None
    latest_ts = None
    latest_run_id = None
    for rec in recs:
        verdict = _SHADOW_VERDICT_MAP.get(
            str(rec.get("status") or "").lower(), "other")
        by_verdict[verdict] += 1
        ts = rec.get("ts")
        date = rec.get("date")
        if isinstance(ts, (int, float)) and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
            latest_date = str(date) if date else latest_date
            latest_run_id = rec.get("run_id")
        elif latest_ts is None and date and (latest_date is None
                                            or str(date) > latest_date):
            latest_date = str(date)
            latest_run_id = rec.get("run_id")
    return len(recs), by_verdict, latest_date, latest_run_id


def load_evolution(watchdog_dir=None, tasks_dir=None, shadow_path=None):
    """聚合三数据面，返回进化账本投影 dict（只读 + fail-visible）。

    契约字段（前端依赖，名不可改）：
    ok / available / generated_at / errors /
    alert_closure{total, closed, open, rate_pct, recent[]} /
    shadow{rows, by_verdict, latest_date, latest_run_id} /
    eval{available, reason}
    """
    errors = []
    try:
        alerts = _load_alerts(watchdog_dir or WATCHDOG_DIR, errors)
        status_by_id = _load_ticket_status(tasks_dir or TASKS_DIR, errors)
        rows, by_verdict, latest_date, latest_run_id = _load_shadow(
            shadow_path, errors)

        total = len(alerts)
        closed = 0
        for a in alerts:
            st = status_by_id.get(str(a["ticket_id"])) if a["ticket_id"] else None
            a["status"] = st
            if st in _CLOSED_STATUSES:
                closed += 1
        rate_pct = round(closed / total * 100, 1) if total else 0.0

        recent = [{"date": a["date"], "task": a["task"], "rc": a["rc"],
                   "ticket_id": a["ticket_id"], "status": a["status"]}
                  for a in alerts[:RECENT_LIMIT]]

        has_data = total > 0 or rows > 0
        return {
            "ok": not errors,
            "available": has_data,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "errors": errors,
            "alert_closure": {
                "total": total,
                "closed": closed,
                "open": total - closed,
                "rate_pct": rate_pct,
                "recent": recent,
            },
            "shadow": {
                "rows": rows,
                "by_verdict": by_verdict,
                "latest_date": latest_date,
                "latest_run_id": latest_run_id,
            },
            "eval": {"available": False,
                     "reason": "周五 17:45 首评后自动点亮"},
        }
    except Exception as e:  # 顶层兜底：loader 内部异常 → ok:false（fail-visible）
        return {
            "ok": False,
            "available": False,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "errors": ["loader 异常: %s" % e],
            "alert_closure": {"total": 0, "closed": 0, "open": 0,
                              "rate_pct": 0.0, "recent": []},
            "shadow": {"rows": 0,
                       "by_verdict": {"pass": 0, "fail": 0,
                                      "pending": 0, "other": 0},
                       "latest_date": None, "latest_run_id": None},
            "eval": {"available": False, "reason": "周五 17:45 首评后自动点亮"},
        }
