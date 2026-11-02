# -*- coding: utf-8 -*-
"""HQ-06 只读 API：/api/hq/*（由 portal_api.do_GET 早分支调用）。

契约：GET 只读，返回 {"ok": true, "data": ...}；未知路由 404。
不写审批/任务/调度/DB，不新增端口；由 8734 同源反代到 8091。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import hq_approvals  # noqa: E402
import hq_budget  # noqa: E402
import hq_heartbeat  # noqa: E402
import hq_org  # noqa: E402
import hq_snapshot  # noqa: E402
import hq_tasks  # noqa: E402


def _days(qs: str) -> int | None:
    try:
        v = (parse_qs(qs).get("days") or [""])[0]
        return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def handle_get(h, path: str, qs: str = "") -> None:
    try:
        if path == "/api/hq/snapshot":
            h._json({"ok": True, "data": hq_snapshot.build_snapshot()})
            return
        if path == "/api/hq/org":
            h._json({"ok": True, "data": hq_org.build_org()})
            return
        if path == "/api/hq/tasks":
            h._json({"ok": True, "data": hq_tasks.build_board()})
            return
        if path == "/api/hq/tasks/board":
            b = hq_tasks.build_board()
            h._json({"ok": True, "data": {"summary": b["summary"], "board": b["board"]}})
            return
        if path == "/api/hq/approvals":
            h._json({"ok": True, "data": hq_approvals.build_approvals()})
            return
        if path == "/api/hq/approvals/summary":
            a = hq_approvals.build_approvals()
            h._json({"ok": True, "data": {"summary": a["summary"], "policy": a["policy"], "risks": a["risks"]}})
            return
        if path == "/api/hq/budget":
            h._json({"ok": True, "data": hq_budget.build_budget(days=_days(qs))})
            return
        if path == "/api/hq/budget/alerts":
            b = hq_budget.build_budget(days=_days(qs))
            h._json({"ok": True, "data": {"risks": b["risks"], "budget": b["budget"], "totals": b["totals"]}})
            return
        if path == "/api/hq/heartbeat/status":
            h._json({"ok": True, "data": hq_heartbeat.build_heartbeat()})
            return
        h._json({"ok": False, "code": "not_found", "error": f"unknown hq path: {path}"}, 404)
    except Exception as exc:  # noqa: BLE001
        h._json({"ok": False, "code": "hq_error", "error": str(exc)}, 500)
