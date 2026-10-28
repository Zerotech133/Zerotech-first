# -*- coding: utf-8 -*-
"""研发军团作战屏 · 聚合层（LS-04，2026-10-28）。

从 tasks.json / daily_metrics.json 提炼契约字段：
- build_wip(tasks, seat_ids)   → 在制任务（dev 席，doing/review）
- build_metrics(daily)         → C6 指标（5 项 + 前置时间）
- build_redlines(metrics)      → 红线状态（可判定项；不可判定返回 None）

只读纯函数；供 portal/api_dev_legion.py 使用；缺数据 → 空值/None，不抛错。
"""
from __future__ import annotations


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
                         "desc": (t.get("desc") or "")[:120]})
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
    dm = (daily or {}).get("metrics") or {}
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
    metrics = metrics or {}
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
