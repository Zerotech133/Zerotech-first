# -*- coding: utf-8 -*-
"""研发军团作战屏 · 聚合层（LS-04，2026-10-28）。

从 tasks.json / daily_metrics.json 提炼契约字段：
- build_wip(tasks, seat_ids)   → 在制任务（dev 席，doing/review）
- build_metrics(daily)         → C6 指标（5 项 + 前置时间）
- build_redlines(metrics)      → 红线状态（可判定项；不可判定返回 None）

只读纯函数；供 portal/api_dev_legion.py 使用；缺数据 → 空值/None，不抛错。
"""
from __future__ import annotations

import re


_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s；;，,）)]+")
_SECRET_WORD_RE = re.compile(r"(?i)(token|secret|password|api[_-]?key)")


def _sanitize_desc(text):
    """脱敏：绝对路径→[path]，敏感词→***（WIP desc 只用于内部展示）。"""
    s = _PATH_RE.sub("[path]", str(text or ""))
    s = _SECRET_WORD_RE.sub("***", s)
    return s


def build_wip(tasks, seat_ids):
    """按席位聚合在制任务（doing/review），按工单号排序。"""
    out = []
    for sid in seat_ids:
        rows = []
        for tid, t in (tasks or {}).items():
            if not isinstance(t, dict) or t.get("owner") != sid:
                continue
            if t.get("status") not in ("doing", "review"):
                continue
            rows.append({"id": tid, "status": t.get("status", ""),
                         "desc": _sanitize_desc((t.get("desc") or "")[:120])})
        if rows:
            out.append({"owner": sid, "tasks": sorted(rows, key=lambda r: r["id"])})
    return out


def _e3(trigger):
    if not trigger:
        return "", 0, 0
    try:
        total = len(trigger or {})
        ok = sum(1 for v in (trigger or {}).values()
                 if isinstance(v, dict) and v.get("ok"))
        return "%d/%d" % (ok, total), ok, total
    except Exception:  # noqa: BLE001
        return "", 0, 0


def build_metrics(daily):
    """daily_metrics.json → 契约 metrics（窗口/采集/合并/归属/验证/E3/前置时间）。"""
    if not isinstance(daily, dict):
        daily = {}
    dm = daily.get("metrics") or {}
    if not isinstance(dm, dict):
        dm = {}
    tagged = dm.get("dev_dept_tagged") or 0
    covered = dm.get("dev_dept_with_acceptance") or 0
    e3_str, _ok, _total = _e3(dm.get("e3_trigger"))
    return {
        "window_days": (daily or {}).get("window_days", 7),
        "dev_tasks_recent": dm.get("dev_tasks_recent"),
        "dev_merged_recent": dm.get("dev_merged_recent"),
        "dev_capture_share_recent": dm.get("dev_capture_share_recent"),
        "owner_legal_rate_recent": dm.get("owner_legal_rate_recent"),
        "verification_rate": (round(covered / tagged, 4) if tagged else None),
        "e3_trigger": e3_str,
        "dev_leadtime_p50_min": dm.get("dev_leadtime_p50_min"),
    }


def build_redlines(metrics):
    """metrics → 红线（可判定项 ok bool；不可判定 None）。"""
    metrics = metrics if isinstance(metrics, dict) else {}
    rate = metrics.get("owner_legal_rate_recent")
    e3 = str(metrics.get("e3_trigger") or "")
    e3_ok = None
    try:
        a, b = e3.split("/", 1)
        e3_ok = int(a) == int(b)
    except Exception:  # noqa: BLE001
        pass
    return [
        {"id": "owner_legal_rate", "value": rate,
         "ok": (None if rate is None else rate >= 0.85)},
        {"id": "e3_trigger", "value": e3, "ok": e3_ok},
        {"id": "merge_approval_coverage", "value": None, "ok": None},
        {"id": "self_review", "value": None, "ok": None},
    ]

# 与 tools/authority_audit.py 同口径：block 生效（2026-10-27 16:00）后新单，
# 历史 done 不追改（charter 明确）。
HISTORICAL_CUTOFF = "2026-10-27T16:00"


def build_governance(tasks, cutoff=HISTORICAL_CUTOFF):
    """dev 单署名覆盖（新单口径）+ 历史缺署名计数，供作战屏看治理红。"""
    rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict) or (t.get("dept") or "").lower() != "dev":
            continue
        if t.get("status") != "done" or not t.get("merged"):
            continue
        stamp = str(t.get("created") or t.get("merged") or "").replace(" ", "T")[:16]
        rows.append({"id": tid, "stamp": stamp,
                     "approver": (t.get("approver") or "").strip()})
    new = [r for r in rows if r["stamp"] >= cutoff]
    new_missing = [r for r in new if not r["approver"]]
    hist = [r for r in rows if r["stamp"] < cutoff]
    hist_missing = [r for r in hist if not r["approver"]]
    return {
        "merge_approval_coverage": (round((len(new) - len(new_missing)) / len(new), 4)
                                    if new else None),
        "new_done": len(new),
        "new_missing": len(new_missing),
        "historical_done": len(hist),
        "historical_missing": len(hist_missing),
        "approval_ok": (None if not new else not new_missing),
        "cutoff": cutoff,
    }
