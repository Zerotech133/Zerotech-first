# -*- coding: utf-8 -*-
"""指挥部会议 v2（P0）：五席晨会/作战会/验收会 纪要 + 订单派单。

落盘：agent_platform/data/meetings/hq/hq-YYYYMMDD-HHMMSS.json（schema 2.0）。
纪律：只落纪要 + 建任务申请；不自动执行、不代批 L2；发言文本为客户端声明
（服务端只做记录，source_verification=server_recorded_client_text）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

SCHEMA = "2.0"
TYPES = ("morning", "operation", "acceptance")
SEATS = {"A0", "AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG", "LAOTIAN"}
KIND_MAP = {"work": "task_request", "trade": "task_request", "study": "question"}
MID_RE = re.compile(r"hq-\d{8}-\d{6}")


def _r(v, n: int) -> str:
    return str(v or "")[:n]


def _now_id() -> str:
    return "hq-" + time.strftime("%Y%m%d-%H%M%S")


def _write(dirpath, mid: str, doc: dict) -> Path:
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    target = d / (mid + ".json")
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    return target


def _load(dirpath, mid: str):
    if not MID_RE.fullmatch(str(mid or "")):
        return None
    p = Path(dirpath) / (mid + ".json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def validate(req) -> tuple[dict | None, str | None]:
    if not isinstance(req, dict):
        return None, "bad_request"
    mtype = _r(req.get("type") or "morning", 16)
    if mtype not in TYPES:
        return None, "bad_type"
    attendees = [_r(x, 24) for x in (req.get("attendees") or [])][:6]
    if not attendees or any(x not in SEATS for x in attendees):
        return None, "bad_attendee"
    agenda = []
    for a in (req.get("agenda") or [])[:20]:
        if not isinstance(a, dict) or not _r(a.get("item"), 200):
            continue
        agenda.append({"item": _r(a.get("item"), 200),
                       "owner": _r(a.get("owner"), 24),
                       "minutes": int(a.get("minutes") or 0)})
    transcript = []
    for m in (req.get("transcript") or [])[:200]:
        if not isinstance(m, dict) or not _r(m.get("text"), 2000):
            continue
        transcript.append({
            "by": _r(m.get("by"), 24),
            "text": _r(m.get("text"), 2000),
            "src": _r(m.get("src"), 12),
            "evidence": [_r(x, 200) for x in (m.get("evidence") or [])][:10],
        })
    decisions = []
    for d in (req.get("decisions") or [])[:50]:
        if not isinstance(d, dict) or not _r(d.get("decision"), 500):
            return None, "bad_decision"
        owner = _r(d.get("owner"), 24)
        if owner and owner not in SEATS:
            return None, "bad_owner"
        decisions.append({
            "id": "D%d" % (len(decisions) + 1),
            "decision": _r(d.get("decision"), 500),
            "owner": owner,
            "due": _r(d.get("due"), 32),
            "evidence": [_r(x, 200) for x in (d.get("evidence") or [])][:10],
            "dissent": [_r(x, 24) for x in (d.get("dissent") or [])][:6],
        })
    orders = []
    for o in (req.get("orders") or [])[:50]:
        if not isinstance(o, dict) or not _r(o.get("title"), 120):
            continue
        ot = _r(o.get("type") or "work", 12)
        if ot not in KIND_MAP:
            return None, "bad_order_type"
        orders.append({"title": _r(o.get("title"), 120), "type": ot,
                       "owner": _r(o.get("owner"), 24), "state": "待派工"})
    return {
        "schema_version": SCHEMA,
        "meeting_id": _now_id(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "type": mtype,
        "agenda": agenda,
        "attendees": attendees,
        "transcript": transcript,
        "decisions": decisions,
        "orders": orders,
        "status": "decided" if decisions else "draft",
        "source_verification": "server_recorded_client_text",
        "note": "指挥部会议纪要 v2：服务端落盘；发言文本仍为客户端声明，未经真实模型回执验证；"
                "决议仅记录，不自动执行、不代批 L2。",
    }, None


def save_meeting(dirpath, req) -> tuple[int, dict]:
    doc, err = validate(req)
    if err:
        return 400, {"ok": False, "error": err}
    target = _write(dirpath, doc["meeting_id"], doc)
    return 200, {"ok": True, "meeting_id": doc["meeting_id"],
                 "saved": str(target), "status": doc["status"],
                 "count": len(doc["transcript"]),
                 "decisions": len(doc["decisions"])}


def get_meeting(dirpath, mid: str) -> dict:
    doc = _load(dirpath, mid)
    if doc is None:
        return {"ok": False, "error": "meeting_not_found"}
    return {"ok": True, "meeting": doc}


def list_meetings(dirpath) -> dict:
    d = Path(dirpath)
    rows = []
    if d.exists():
        for p in sorted(d.glob("hq-*.json"), reverse=True)[:100]:
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            rows.append({
                "meeting_id": doc.get("meeting_id", p.stem),
                "type": doc.get("type", ""),
                "status": doc.get("status", ""),
                "attendees": doc.get("attendees") or [],
                "created_at": doc.get("created_at", ""),
                "decisions": len(doc.get("decisions") or []),
                "orders": len(doc.get("orders") or []),
            })
    return {"ok": True, "meetings": rows}


def dispatch_meeting(dirpath, req, submit) -> tuple[int, dict]:
    if not isinstance(req, dict):
        return 400, {"ok": False, "error": "bad_request"}
    mid = _r(req.get("meeting_id"), 32)
    doc = _load(dirpath, mid)
    if doc is None:
        return 404, {"ok": False, "error": "meeting_not_found"}
    title = _r(req.get("title"), 120)
    if not title:
        return 400, {"ok": False, "error": "bad_title"}
    ot = _r(req.get("order_type") or "work", 12)
    if ot not in KIND_MAP:
        return 400, {"ok": False, "error": "bad_order_type"}
    owner = _r(req.get("owner"), 24)
    if owner and owner not in SEATS:
        return 400, {"ok": False, "error": "bad_owner"}
    payload = {
        "command": "legion_meeting_dispatch",
        "order_type": ot,
        "title": title,
        "attendees": doc.get("attendees") or [],
        "host": "laotian",
        "note": "指挥部会议 %s（仅建任务申请，未执行；L2 不代批）" % mid,
    }
    try:
        env = submit(KIND_MAP[ot], payload, source="hq-meeting")
    except Exception as e:  # noqa: BLE001
        return 502, {"ok": False, "error": "dispatch_failed",
                     "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}
    order = {"title": title, "type": ot, "owner": owner,
             "state": "已生成任务申请", "dispatch": env}
    doc.setdefault("orders", []).append(order)
    doc["status"] = "dispatched"
    _write(dirpath, mid, doc)
    return 200, {"ok": True, "meeting_id": mid, "dispatch": env, "order": order}
