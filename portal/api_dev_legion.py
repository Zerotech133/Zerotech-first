# -*- coding: utf-8 -*-
"""研发军团作战屏 v1 · 只读 API（LS-03，2026-10-28）。

GET /api/dev/legion
    → 只读聚合 org.json + .worktrees/tasks.json + daily_metrics.json
    契约：docs/dev/legion_screen_contract.json（frozen v1.0）
    ADR：docs/adr/ADR-009-dev-legion-screen-contract.md

只读纪律：不写文件/库/事件；数据源缺失 → 空态 + errors 标注，HTTP 200，
不 500（契约 §errors）。
挂载：portal_api.do_GET 早分支 → api_dev_legion.handle_get(self, path, qs)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from dev_legion_aggregate import (build_metrics, build_redlines,
                                  build_wip)
from dev_legion_ai_card import build_ai_cards
from dev_legion_quant_card import build_quant_cards

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
TASKS = ROOT / ".worktrees" / "tasks.json"
DAILY = ROOT / "agent_platform" / "data" / "dev" / "daily_metrics.json"
HR = ROOT / "agent_platform" / "hr"
SOURCES = ["agent_platform/registry/org.json", ".worktrees/tasks.json",
           "agent_platform/data/dev/daily_metrics.json"]


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig")), None
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (Path(path).name, type(e).__name__)


def _hr_name(seat):
    try:
        for line in (HR / seat / "resume.yaml").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cn_name:"):
                return line.split(":", 1)[1].strip().strip('"')
    except Exception:  # noqa: BLE001
        pass
    return ""


def _dev_profiles(org):
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return d, [p for p in d.get("agent_profiles") or []
                       if isinstance(p, dict) and p.get("agent_id")]
    return {}, []


def _seat(p):
    sid = p.get("agent_id", "")
    return {
        "id": sid,
        "name": p.get("cn_name") or _hr_name(sid),
        "role_title": p.get("role_title", ""),
        "discipline": p.get("discipline", ""),
        "group": p.get("group", ""),
        "grade": p.get("grade", ""),
        "model": p.get("llm_binding", ""),
        "org_role": p.get("org_role", ""),
        "report_to": p.get("report_to", ""),
    }


def _wip(tasks, seat_ids):
    """委托 LS-04 聚合层（保留旧函数名兼容）。"""
    return build_wip(tasks, seat_ids)


def _e3(trigger):
    try:
        total = len(trigger or {})
        ok = sum(1 for v in (trigger or {}).values() if isinstance(v, dict) and v.get("ok"))
        return "%d/%d" % (ok, total), ok, total
    except Exception:  # noqa: BLE001
        return "", 0, 0


def build_payload(root=ROOT):
    root = Path(root)
    errors = []
    org_ex, org = None, None
    try:
        org = json.loads((root / "agent_platform/registry/org.json").read_text(
            encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        errors.append("org_unavailable: %s" % type(e).__name__)
    dev, profiles = _dev_profiles(org)
    seats = [_seat(p) for p in profiles]
    groups = []
    for g in dev.get("groups") or []:
        groups.append({"id": g.get("id", ""), "name": g.get("name", ""),
                       "lead": g.get("lead", ""),
                       "members": list(g.get("members") or [])})
    tasks = None
    try:
        tasks = json.loads((root / ".worktrees/tasks.json").read_text(
            encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        errors.append("tasks_unavailable: %s" % type(e).__name__)
    wip = _wip(tasks if isinstance(tasks, dict) else {}, [s["id"] for s in seats])
    daily = None
    try:
        daily = json.loads((root / "agent_platform/data/dev/daily_metrics.json")
                           .read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        errors.append("daily_metrics_unavailable: %s" % type(e).__name__)
    metrics = build_metrics(daily)
    redlines = build_redlines(metrics)
    lead = dev.get("lead", "")
    lead_name = ""
    for _p in profiles:
        if _p.get("agent_id") == lead:
            lead_name = _seat(_p)["name"]
            break
    return {
        "schema": "legion_screen_contract/v1",
        "readonly": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "department": {
            "id": dev.get("dept", "dev"),
            "name": dev.get("name", "研发部"),
            "lead": lead,
            "lead_name": lead_name,
            "deputy": dev.get("deputy", ""),
            "groups": groups,
        },
        "seats": seats,
        "ai_cards": build_ai_cards(org),
        "quant_cards": build_quant_cards(org),
        "work_in_progress": wip,
        "metrics": metrics,
        "redlines": redlines,
        "sources": SOURCES,
        "errors": errors,
    }


def _json(handler, obj, status=200):
    try:
        handler._json(obj, status)
        return
    except Exception:  # noqa: BLE001
        pass
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_get(handler, path, qs=""):
    if path != "/api/dev/legion":
        return _json(handler, {"ok": False, "error": "not_found", "path": path}, 404)
    payload = build_payload()
    if payload["errors"]:
        payload["ok"] = False
    else:
        payload["ok"] = True
    return _json(handler, payload)
