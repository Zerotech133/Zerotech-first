#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ Directive 只读模型（C3，2026-11-03）。

把三类**已有真源**聚合为 Directive 只读对象（不新增真源、不派单、不写盘、不签署）：
  1. 会议纪要 v2：``agent_platform/data/meetings/hq/hq-*.json`` 的 decisions / orders；
  2. 会前议程：``portal/data/council_new_topics.json`` 的议题（只读；不写 portal）；
  3. 五角色职责/派单参与：``agent_platform/command_hq/roles.py``（RoleAdapter 真身份）；
     会议 transcript 中五席（A0/AI1/ZHAOYUN/HUANGZHONG/GUANYU）的发言**原样**作为 opinions，
     缺席/失败（如「模型调用失败」）如实保留，**绝不伪造意见**。

Directive v1 字段：id / topic / source_role / legion / owner / task_id / status / due /
evidence / opinions（另有 kind/created_at/meeting_id/roles_involved/task_ref/read_only）。
状态机见 ``docs/specs/2026-11-03_HQ_directive_report_contract_v1.md``。

无真源数据 → fail-visible（ok=false, error=no_source_data, rc=2），不以空对象冒充成功。

用法：
  python -X utf8 tools/hq_directives.py                 # 人读摘要
  python -X utf8 tools/hq_directives.py --json          # 机器可读（stdout 纯 JSON）
  python -X utf8 tools/hq_directives.py --json --kind decision --status open --limit 20
  python -X utf8 tools/hq_directives.py --json --meeting hq-20261028-233044

退出码：0 成功（含空列表但真源存在）；2 fail-visible（无真源/参数/环境错误）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
MEETINGS = AP / "data" / "meetings" / "hq"
COUNCIL = ROOT / "portal" / "data"
TASKS = ROOT / ".worktrees" / "tasks.json"

SCHEMA = "hq/directives/v1"
READ_ONLY = True
ROLE_ORDER = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
SEAT_TO_ROLE = {
    "A0": "TIANFENG", "AI1": "ZHUGE", "ZHAOYUN": "ZHAOYUN",
    "HUANGZHONG": "HUANGZHONG", "GUANYU": "GUANYU",
}
# owner/seat → 军团（组合）归属；未列入者如实归 unmapped（不猜）
OWNER_LEGION = {
    "ACAD1": "acad1-legion",
    "DATA1": "data-warehouse",
    "D1": "lusu-d1-flow", "LUSU1": "lusu-d1-flow",
    "STRAT1": "quant-strategy-line", "ST1": "quant-strategy-line",
    "STRAT2": "quant-strategy-line", "BACK1": "quant-strategy-line",
    "HUANGZHONG": "huangzhong-acceptance",
    "A0": "hq-command", "AI1": "hq-command", "ARCH1": "hq-command",
    "DSH": "hq-command", "DSH-Lead": "hq-command", "LAOTIAN": "hq-command",
    "ZHAOYUN": "hq-governance", "GUANYU": "hq-audit",
}
STATUSES = ("draft", "open", "pending", "dispatched", "in_progress", "closed")
KINDS = ("decision", "order", "topic")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
MAX_TEXT = 2000


# --------------------------------------------------------------------------- #
# 基础读取（全部只读；坏文件跳过并记 warning，不静默假绿）
# --------------------------------------------------------------------------- #
def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _text(v, limit: int = MAX_TEXT) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _created_key(v) -> str:
    """created_at → 可排序 ISO 前缀（无法解析时原样，排后）。"""
    s = _text(v, 40)
    return s


def _load_roles() -> tuple[dict, list]:
    """复用 command_hq.roles 真身份（五角色 seat/name/duty）；不可用时降级并 warning。"""
    warnings = []
    try:
        if str(AP) not in sys.path:
            sys.path.insert(0, str(AP))
        from command_hq import roles  # noqa: E402

        meta = {}
        for role in ROLE_ORDER:
            spec = getattr(roles, "ROLE_SPECS", {}).get(role) or {}
            meta[role] = {
                "role": role,
                "seat": spec.get("seat_id") or SEAT_TO_ROLE.get(
                    next((s for s, r in SEAT_TO_ROLE.items() if r == role), ""), ""),
                "name": spec.get("cn_name") or role,
                "duty": spec.get("duty") or "",
                "never": list(spec.get("never") or []),
            }
        return meta, warnings
    except Exception as exc:  # noqa: BLE001 —— 降级：无职责富化，但 Directive 仍可用
        warnings.append("roles_unavailable:%s" % type(exc).__name__)
        return {}, warnings


def _load_meetings(dirpath: Path) -> tuple[list, list]:
    """→ [(path, doc)]（按 meeting_id 升序，坏文件计入 warnings）。"""
    warnings = []
    rows = []
    d = Path(dirpath)
    if not d.is_dir():
        return rows, ["meetings_dir_missing:%s" % _rel(d)]
    for p in sorted(d.glob("hq-*.json")):
        doc = _read_json(p, None)
        if not isinstance(doc, dict):
            warnings.append("meeting_unreadable:%s" % _rel(p))
            continue
        rows.append((p, doc))
    return rows, warnings


def _load_council(dirpath: Path) -> tuple[dict, list]:
    warnings = []
    d = Path(dirpath)
    topics = _read_json(d / "council_new_topics.json", [])
    if not isinstance(topics, list):
        warnings.append("council_topics_not_list")
        topics = []
    scheduled = []
    sp = d / "council_scheduled.jsonl"
    if sp.is_file():
        try:
            for line in sp.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    scheduled.append(row)
        except OSError:
            warnings.append("council_scheduled_unreadable")
    return {"topics": topics, "scheduled": scheduled}, warnings


def _load_tasks(path: Path) -> tuple[dict | None, str]:
    """任务板：dict[task_id]=record；缺失/坏 JSON → (None, error)（供 task_id 显式链接）。"""
    p = Path(path)
    if not p.is_file():
        return None, "tasks_file_missing:%s" % _rel(p)
    data = _read_json(p, None)
    if not isinstance(data, dict):
        return None, "tasks_file_invalid"
    return data, ""


# --------------------------------------------------------------------------- #
# 映射/富化
# --------------------------------------------------------------------------- #
def _role_of_seat(seat: str) -> str:
    return SEAT_TO_ROLE.get(str(seat or "").strip().upper(), "")


def _legion_of(owner) -> str:
    o = str(owner or "").strip()
    return OWNER_LEGION.get(o, "unmapped")


def _roles_involved(owner, opinions) -> list:
    roles = []
    r = _role_of_seat(owner)
    if r:
        roles.append(r)
    for op in opinions:
        if op.get("role") and op["role"] not in roles:
            roles.append(op["role"])
    return [r for r in ROLE_ORDER if r in roles]


def _opinions_from_meeting(doc: dict) -> list:
    """只收 transcript 中五席真发言（原样文本）；不补、不改写、不伪造。"""
    out = []
    for m in (doc.get("transcript") or [])[:200]:
        if not isinstance(m, dict):
            continue
        by = _text(m.get("by"), 24).upper()
        role = _role_of_seat(by)
        text = _text(m.get("text"))
        if not role or not text:
            continue
        out.append({
            "role": role,
            "seat": by,
            "text": text,
            "src": _text(m.get("src"), 12),
            "evidence": [_text(x, 200) for x in (m.get("evidence") or [])[:10]],
            "model_call_failed": "模型调用失败" in text,
        })
        if len(out) >= 40:
            break
    return out


def _roles_meta_for(role_map: dict, roles: list) -> list:
    rows = []
    for r in roles:
        meta = role_map.get(r) or {"role": r, "seat": "", "name": r, "duty": "", "never": []}
        rows.append({"role": r, "seat": meta.get("seat") or "",
                     "name": meta.get("name") or r, "duty": meta.get("duty") or ""})
    return rows


def _task_ref_from(obj: dict) -> dict:
    """显式任务链接（不猜）：obj.task_id / dispatch.data.task_id|ticket_id。"""
    tid = _text(obj.get("task_id"), 64)
    if tid:
        return {"source": "field:task_id", "task_id": tid}
    dispatch = obj.get("dispatch")
    if isinstance(dispatch, dict):
        data = dispatch.get("data") if isinstance(dispatch.get("data"), dict) else {}
        for key in ("task_id", "ticket_id"):
            val = _text(data.get(key) or dispatch.get(key), 64)
            if val:
                return {"source": "dispatch:%s" % key, "task_id": val}
    return {"source": "none", "task_id": ""}


def _tid_exists(tasks: dict | None, tid: str) -> bool:
    return bool(tasks) and bool(tid) and tid in tasks


# --------------------------------------------------------------------------- #
# Directive 构建
# --------------------------------------------------------------------------- #
def _meeting_directives(path: Path, doc: dict, role_map: dict) -> list:
    out = []
    mid = _text(doc.get("meeting_id"), 64) or path.stem
    created = _text(doc.get("created_at"), 40)
    mstatus = _text(doc.get("status"), 24) or "draft"
    opinions = _opinions_from_meeting(doc)
    meeting_ev = "meeting:%s" % _rel(path)

    for i, d in enumerate(doc.get("decisions") or [], 1):
        if not isinstance(d, dict):
            continue
        owner = _text(d.get("owner"), 24)
        rid = _text(d.get("id"), 16) or "D%d" % i
        topic = _text(d.get("decision"))
        if not topic:
            continue
        task_ref = _task_ref_from(d)
        status = "draft" if mstatus == "draft" else "open"
        roles = _roles_involved(owner, opinions)
        out.append({
            "id": "DIR-MEET-%s-%s" % (mid, rid),
            "kind": "decision",
            "topic": topic,
            "source_role": _role_of_seat(owner) or owner or "TIANFENG",
            "legion": _legion_of(owner or "A0"),
            "owner": owner,
            "task_id": task_ref["task_id"],
            "status": status,
            "due": _text(d.get("due"), 40),
            "evidence": ([meeting_ev + "#decision:" + rid]
                         + ["evidence:%s" % _text(e, 200) for e in (d.get("evidence") or [])[:10]]
                         + (["task:%s" % task_ref["task_id"]] if task_ref["task_id"] else [])),
            "opinions": [dict(op) for op in opinions],
            "created_at": created,
            "meeting_id": mid,
            "meeting_type": _text(doc.get("type"), 16),
            "dissent": [_text(x, 24) for x in (d.get("dissent") or [])[:6]],
            "roles_involved": _roles_meta_for(role_map, roles),
            "roles_present": roles,
            "roles_missing": [r for r in ROLE_ORDER if r not in roles],
            "opinions_scope": "meeting",
            "task_ref": task_ref,
            "read_only": True,
        })

    for i, o in enumerate(doc.get("orders") or [], 1):
        if not isinstance(o, dict):
            continue
        owner = _text(o.get("owner"), 24) or _text(o.get("agent"), 24)
        topic = _text(o.get("title") or o.get("desc"))
        if not topic:
            continue
        task_ref = _task_ref_from(o)
        dispatch = o.get("dispatch")
        dispatched = isinstance(dispatch, dict) and bool(dispatch.get("ok", True))
        status = "dispatched" if dispatched else "pending"
        roles = _roles_involved(owner, opinions)
        evidence = [meeting_ev + "#order:%d" % i]
        if isinstance(dispatch, dict) and dispatch.get("data"):
            evidence.append("dispatch:inbox:%s" % _text(
                (dispatch.get("data") or {}).get("id"), 64))
        if task_ref["task_id"]:
            evidence.append("task:%s" % task_ref["task_id"])
        out.append({
            "id": "DIR-MEET-%s-O%d" % (mid, i),
            "kind": "order",
            "topic": topic,
            "source_role": _role_of_seat(owner) or owner or "TIANFENG",
            "legion": _legion_of(owner or "A0"),
            "owner": owner,
            "task_id": task_ref["task_id"],
            "status": status,
            "due": _text(o.get("due"), 40),
            "evidence": evidence,
            "opinions": [dict(op) for op in opinions],
            "created_at": created,
            "meeting_id": mid,
            "meeting_type": _text(doc.get("type"), 16),
            "order_type": _text(o.get("type"), 12),
            "order_state": _text(o.get("state"), 32),
            "roles_involved": _roles_meta_for(role_map, roles),
            "roles_present": roles,
            "roles_missing": [r for r in ROLE_ORDER if r not in roles],
            "opinions_scope": "meeting",
            "task_ref": task_ref,
            "read_only": True,
        })
    return out


def _council_directives(council: dict, council_dir=None) -> list:
    out = []
    topics = council.get("topics") or []
    for i, t in enumerate(topics[-200:]):
        if not isinstance(t, dict):
            continue
        text = _text(t.get("topic") or t.get("title") or t.get("text"))
        if not text:
            continue
        created = _text(t.get("created_at") or t.get("ts"), 40)
        origin = _text(t.get("originator") or t.get("owner") or t.get("by"), 24)
        digest = hashlib.sha1(("%s|%s" % (text, created)).encode("utf-8")).hexdigest()[:8]
        out.append({
            "id": "DIR-COUNCIL-%s" % digest,
            "kind": "topic",
            "topic": text,
            "source_role": _role_of_seat(origin) or origin or "LAOTIAN",
            "legion": _legion_of(origin or "LAOTIAN"),
            "owner": origin,
            "task_id": "",
            "status": "open",
            "due": _text(t.get("due"), 40),
            "evidence": ["council:%s#topic:%d" % (
                _rel(Path(council_dir or COUNCIL) / "council_new_topics.json"), i)],
            "opinions": [],
            "created_at": created,
            "meeting_id": "",
            "tags": [_text(x, 24) for x in (t.get("tags") or [])[:8]],
            "stage": _text(t.get("stage"), 16),
            "roles_involved": [],
            "roles_present": [],
            "roles_missing": list(ROLE_ORDER),
            "opinions_scope": "none",
            "task_ref": {"source": "none", "task_id": ""},
            "read_only": True,
        })
    return out


def _fail(error: str, searched: list, warnings: list) -> dict:
    return {"schema": SCHEMA, "ok": False, "error": error,
            "searched": searched, "warnings": warnings,
            "read_only": True, "generated_at": _now(), "directives": []}


def build_directives(meetings_dir=None, council_dir=None, tasks_file=None) -> dict:
    """聚合只读 Directive 列表（纯函数；不写盘）。"""
    meetings_dir = Path(meetings_dir or MEETINGS)
    council_dir = Path(council_dir or COUNCIL)
    tasks_path = Path(tasks_file or TASKS)
    role_map, warnings = _load_roles()
    meetings, m_warn = _load_meetings(meetings_dir)
    warnings += m_warn
    council, c_warn = _load_council(council_dir)
    warnings += c_warn
    tasks, t_warn = _load_tasks(tasks_path)
    if t_warn:
        warnings.append(t_warn)  # task_id 链接不可用，但 Directive 仍可产出

    searched = [_rel(meetings_dir), _rel(council_dir / "council_new_topics.json"),
                _rel(tasks_path)]
    directives = []
    for p, doc in meetings:
        directives += _meeting_directives(p, doc, role_map)
    directives += _council_directives(council, council_dir)
    if not directives:
        return _fail("no_source_data", searched, warnings)

    # 显式 task 链接存在性校验（不猜：不存在则清空并记 warning）
    for d in directives:
        tid = d.get("task_id") or ""
        if tid and not _tid_exists(tasks, tid):
            warnings.append("task_link_unresolved:%s:%s" % (d["id"], tid))
            d["task_id"] = ""
            d["task_ref"] = {"source": "unresolved", "task_id": tid}
        elif tid:
            d["task_ref"] = {"source": d["task_ref"].get("source", "field:task_id"),
                             "task_id": tid, "task_exists": True}

    directives.sort(key=lambda x: (_created_key(x.get("created_at")), x.get("id")),
                    reverse=True)
    by_kind, by_status, by_legion = {}, {}, {}
    for d in directives:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        by_legion[d["legion"]] = by_legion.get(d["legion"], 0) + 1
    return {
        "schema": SCHEMA,
        "ok": True,
        "read_only": READ_ONLY,
        "generated_at": _now(),
        "sources": {
            "meetings_dir": _rel(meetings_dir),
            "meetings": len(meetings),
            "council_topics": len(council.get("topics") or []),
            "council_scheduled": len(council.get("scheduled") or []),
            "tasks_file": _rel(tasks_path),
            "tasks_available": bool(tasks),
            "roles_source": "command_hq.roles" if role_map else "unavailable",
        },
        "warnings": warnings,
        "directives": directives,
        "summary": {
            "total": len(directives),
            "by_kind": by_kind,
            "by_status": by_status,
            "by_legion": by_legion,
            "with_opinions": sum(1 for d in directives if d["opinions"]),
            "with_task_link": sum(1 for d in directives if d["task_id"]),
        },
    }


def filter_directives(directives: list, meeting=None, kind=None, status=None,
                      legion=None, limit=None) -> list:
    rows = list(directives)
    if meeting:
        rows = [d for d in rows if d.get("meeting_id") == meeting]
    if kind:
        wanted = {k.strip() for k in str(kind).split(",") if k.strip()}
        rows = [d for d in rows if d.get("kind") in wanted]
    if status:
        wanted = {s.strip() for s in str(status).split(",") if s.strip()}
        rows = [d for d in rows if d.get("status") in wanted]
    if legion:
        wanted = {x.strip() for x in str(legion).split(",") if x.strip()}
        rows = [d for d in rows if d.get("legion") in wanted]
    if limit and int(limit) > 0:
        rows = rows[: int(limit)]
    return rows


def _print_text(payload: dict, rows: list) -> None:
    if not payload.get("ok"):
        print("[FAIL] %s | searched=%s" % (payload.get("error"),
                                           payload.get("searched")), file=sys.stderr)
        for w in payload.get("warnings") or []:
            print("  WARN", w, file=sys.stderr)
        return
    src = payload["sources"]
    print("== HQ Directive（只读，C3）==")
    print("  来源：meetings=%d council_topics=%d tasks=%s roles=%s"
          % (src["meetings"], src["council_topics"],
             "on" if src["tasks_available"] else "off", src["roles_source"]))
    print("  汇总：total=%d by_kind=%s by_status=%s"
          % (payload["summary"]["total"], json.dumps(payload["summary"]["by_kind"], ensure_ascii=False),
             json.dumps(payload["summary"]["by_status"], ensure_ascii=False)))
    for d in rows[:30]:
        print("  [%s/%s] %s | role=%s legion=%s owner=%s task=%s due=%s opinions=%d"
              % (d["kind"], d["status"], d["topic"][:60].replace("\n", " "),
                 d["source_role"], d["legion"], d["owner"] or "-",
                 d["task_id"] or "-", d["due"] or "-", len(d["opinions"])))
    if len(rows) > 30:
        print("  … 其余 %d 条（--limit 控制）" % (len(rows) - 30))
    for w in (payload.get("warnings") or [])[:5]:
        print("  WARN", w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ Directive 只读模型（C3）")
    ap.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    ap.add_argument("--meetings-dir", default=str(MEETINGS))
    ap.add_argument("--council-dir", default=str(COUNCIL))
    ap.add_argument("--tasks-file", default=str(TASKS))
    ap.add_argument("--meeting", default="", help="过滤 meeting_id")
    ap.add_argument("--kind", default="", help="过滤 kind：decision,order,topic")
    ap.add_argument("--status", default="", help="过滤 status（逗号分隔）")
    ap.add_argument("--legion", default="", help="过滤 legion（逗号分隔）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    try:
        payload = build_directives(args.meetings_dir, args.council_dir, args.tasks_file)
    except Exception as exc:  # noqa: BLE001 —— fail-visible，不吞异常
        payload = _fail("internal_error:%s" % type(exc).__name__,
                        [args.meetings_dir, args.council_dir, args.tasks_file], [str(exc)[:200]])
    if not payload.get("ok"):
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        _print_text(payload, [])
        return 2
    rows = filter_directives(payload["directives"], args.meeting, args.kind,
                             args.status, args.legion, args.limit or None)
    payload["filtered"] = {"meeting": args.meeting, "kind": args.kind,
                           "status": args.status, "legion": args.legion,
                           "count": len(rows)}
    payload["directives"] = rows
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print_text(payload, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
