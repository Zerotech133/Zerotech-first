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
TYPES = ("morning", "operation", "acceptance", "dept_morning")
# 2026-11-03 新组织架构：指挥席 + 部门负责人池
COMMAND_SEATS = {"A0", "AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG", "LAOTIAN"}
DEPT_LEADS = {"D1", "ARCH1", "DATA1", "PM1", "QA1", "DEV3",
              "MKT1", "LEG1", "A0A", "ACAD1", "INTEL0", "LUSU1", "R3"}
# UI 花名册中的观察/专业席：可上桌，不等于常任指挥席
OBSERVER_SEATS = {"R2", "OPT1", "QS1", "ST1", "R1", "INTEL1", "JEV",
                  "HERMES_UI", "DEV1", "DEV2"}
# 司马徽研究院团队（2026-11-04 入职）：可参会/可被派单，但不是部门负责人池
ACADEMY_TEAM = {"STRAT1", "BACK1", "COMM1", "PROD1", "STRAT2"}
SEATS = COMMAND_SEATS | DEPT_LEADS | OBSERVER_SEATS | ACADEMY_TEAM
MAX_ATTENDEES = 6
KIND_MAP = {"work": "task_request", "trade": "task_request", "study": "question"}
MID_RE = re.compile(r"hq-\d{8}-\d{6}")
# 编码损坏特征：连续问号。控制台/命令行代码页在把非 ASCII 交给 UTF-8 写入端之前
# 已把字符替换成 0x3F，落盘后不可逆（2026-10-29 / 11-03 两档即此症状）。
# 入口拒收是从根上止血——写入端编码本来就是对的，坏在入参。
GARBLED_RE = re.compile(r"\?{2,}")


_REPO = Path(__file__).resolve().parents[2]


def _load_events_module():
    """按文件路径加载 tools/events.py（避免环境里同名 tools 包劫持）。"""
    import importlib.util
    path = _REPO / "tools" / "events.py"
    spec = importlib.util.spec_from_file_location("aios_dev_events", path)
    if spec is None or spec.loader is None:
        raise ImportError("events module unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emit_event(kind: str, task: str, detail: str) -> None:
    """会议事件回流 dev_events（失败静默，不挡会议）。"""
    try:
        _load_events_module().emit(kind, task=task, agent="A0", detail=detail)
    except Exception:  # noqa: BLE001
        pass


def _trace(name: str, attrs: dict, start: float) -> None:
    """会议 span → 本地 SQLite + Phoenix（OTLP 已接；失败静默）。"""
    try:
        from observability import trace
        trace.record(trace.trace_id(), name, kind="meeting",
                     start=start, end=time.time(), status="ok", attrs=attrs)
    except Exception:  # noqa: BLE001
        pass


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


def looks_garbled(text) -> bool:
    """连续两个以上问号视为编码损坏（正常中文文本不会出现）。"""
    return bool(GARBLED_RE.search(str(text or "")))


def validate(req) -> tuple[dict | None, str | None]:
    if not isinstance(req, dict):
        return None, "bad_request"
    mtype = _r(req.get("type") or "morning", 16)
    if mtype not in TYPES:
        return None, "bad_type"
    # 编码拒收（R12 D6）：先于一切字段裁剪，避免截断后再判导致漏检
    for a in (req.get("agenda") or [])[:20]:
        if isinstance(a, dict) and looks_garbled(a.get("item")):
            return None, "bad_encoding"
    for d in (req.get("decisions") or [])[:50]:
        if isinstance(d, dict) and looks_garbled(d.get("decision")):
            return None, "bad_encoding"
    for o in (req.get("orders") or [])[:50]:
        if isinstance(o, dict) and looks_garbled(o.get("title")):
            return None, "bad_encoding"
    for m in (req.get("transcript") or [])[:200]:
        if isinstance(m, dict) and looks_garbled(m.get("text")):
            return None, "bad_encoding"
    attendees = [_r(x, 24) for x in (req.get("attendees") or [])][:MAX_ATTENDEES]
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
    acceptance = []
    for a in (req.get("acceptance") or [])[:5]:
        if not isinstance(a, dict):
            continue
        acceptance.append({
            "verdict": _r(a.get("verdict"), 12),
            "ticket": _r(a.get("ticket"), 64),
            "commit": _r(a.get("commit"), 64),
            "evidence": _r(a.get("evidence"), 500),
            "report": _r(a.get("report"), 200),
        })
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
        "acceptance": acceptance,
        "status": "decided" if decisions else "draft",
        "source_verification": "server_recorded_client_text",
        "note": "指挥部会议纪要 v2：服务端落盘；发言文本仍为客户端声明，未经真实模型回执验证；"
                "决议仅记录，不自动执行、不代批 L2。",
    }, None


def save_meeting(dirpath, req) -> tuple[int, dict]:
    doc, err = validate(req)
    if err:
        return 400, {"ok": False, "error": err}
    start = time.time()
    target = _write(dirpath, doc["meeting_id"], doc)
    _emit_event("meeting_saved", doc["meeting_id"],
                "type=%s status=%s decisions=%d attendees=%d"
                % (doc["type"], doc["status"], len(doc["decisions"]), len(doc["attendees"])))
    _trace("hq.meeting.save", {"meeting_id": doc["meeting_id"], "type": doc["type"],
                               "status": doc["status"], "decisions": len(doc["decisions"])}, start)
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
        "owner": owner,
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
    start = time.time()
    _write(dirpath, mid, doc)
    _emit_event("meeting_dispatched", mid, "order=%s owner=%s" % (title[:80], owner))
    _trace("hq.meeting.dispatch", {"meeting_id": mid, "owner": owner, "order_type": ot}, start)
    return 200, {"ok": True, "meeting_id": mid, "dispatch": env, "order": order}
