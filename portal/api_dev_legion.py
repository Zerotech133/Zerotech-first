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

from dev_legion_aggregate import (build_governance, build_metrics,
                                  build_redlines, build_wip)
from dev_legion_ai_card import build_ai_cards
from dev_legion_quant_card import build_quant_cards
from dev_legion_risk_card import build_risk_cards
from dev_legion_quant_real import build_quant_real
from dev_legion_risk_real import build_risk_real

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
    if not isinstance(org, dict):
        return {}, []
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



DECISIONS = ROOT / "agent_platform" / "config" / "dev_legion_decisions.json"
EVENTS = ROOT / "portal" / "data" / "dev_events.jsonl"


def _load_optional(root, rel):
    try:
        return json.loads((Path(root) / rel).read_text(encoding="utf-8-sig")), None
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (Path(rel).name, type(e).__name__)


def _workorder_bus(tasks):
    """跨席在制/待复核（不限于 dev seats），最多 30 条；只读。"""
    if not isinstance(tasks, dict):
        return []
    rows = []
    for tid, t in tasks.items():
        if not isinstance(t, dict) or str(t.get("status") or "") not in ("doing", "review"):
            continue
        rows.append({
            "id": str(tid),
            "owner": str(t.get("owner") or ""),
            "status": str(t.get("status") or ""),
            "desc": str(t.get("desc") or ""),
            "created": str(t.get("created") or ""),
        })
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows[:30]


def _decisions(root):
    doc, err = _load_optional(root, "agent_platform/config/dev_legion_decisions.json")
    if err:
        return [], err
    items = (doc or {}).get("items") if isinstance(doc, dict) else []
    return (items if isinstance(items, list) else []), None


def _events(root):
    try:
        lines = (Path(root) / "portal/data/dev_events.jsonl").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return [], "dev_events.jsonl: %s" % type(e).__name__
    rows = []
    for line in lines[-12:]:
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        rows.append({
            "ts": str(r.get("ts") or r.get("at") or ""),
            "kind": str(r.get("kind") or ""),
            "agent": str(r.get("agent") or r.get("owner") or ""),
            "task": str(r.get("task") or r.get("tid") or ""),
        })
    return rows[-10:], None


def build_payload(root=ROOT):
    root = Path(root)
    errors = []
    _org_ex, org = None, None
    try:
        org = json.loads((root / "agent_platform/registry/org.json").read_text(
            encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        errors.append("org_unavailable: %s" % type(e).__name__)
    if org is not None and not isinstance(org, dict):
        errors.append("org_unavailable: bad_shape")
        org = None
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
    # v1.1 可选真实聚合（无真源/无席位时不出现，v1 客户端无感）
    ai_cards = build_ai_cards(org)
    quant_cards = build_quant_cards(org)
    risk_cards = build_risk_cards(org)
    extensions = []
    qreal = build_quant_real(root)
    if qreal and quant_cards:
        for _c in quant_cards:
            _c["real"] = qreal
        extensions.append("v1.1_quant_real")
    bus = _workorder_bus(tasks if isinstance(tasks, dict) else {})
    decisions, e_dec = _decisions(root)
    if e_dec:
        errors.append(e_dec)
    if bus:
        decisions = list(decisions) + [{
            "title": "跨席 %d 单未回流" % len(bus),
            "due": "今日",
            "owner": "ARCH1",
            "high": True,
            "desc": "研发部直属在制与跨席工单均在任务总线；由 ARCH1 决定回流规则。",
            "action": "决定派工规则",
            "source": "workorder_bus",
        }]
    events, e_ev = _events(root)
    if e_ev:
        errors.append(e_ev)
    if bus:
        extensions.append("v2_workorder_bus")
    if decisions:
        extensions.append("v2_decisions")
    if events:
        extensions.append("v2_events")
    rreal = build_risk_real(root)
    if rreal and risk_cards:
        for _c in risk_cards:
            _c["real"] = rreal
        extensions.append("v1.1_risk_real")
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
        "ai_cards": ai_cards,
        "quant_cards": quant_cards,
        "risk_cards": risk_cards,
        "work_in_progress": wip,
        "governance": build_governance(tasks if isinstance(tasks, dict) else {}),
        "metrics": metrics,
        "redlines": redlines,
        "workorder_bus": bus,
        "decisions": decisions,
        "events": events,
        "v2": {"readonly": True, "extras": ["workorder_bus", "decisions", "events"]},
        "sources": SOURCES,
        "extensions": extensions,
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
