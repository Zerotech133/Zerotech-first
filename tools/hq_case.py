#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-Bound Case 流程 v1：田丰汇总 → 五角色意见 → 会议 → 方案 → 人类审批 → 下发。

硬门：
- 未 APPROVED 不得 dispatch；
- 五角色意见不齐不得进入 MEETING（可重试）；
- 决议/方案只记录，不自动执行；dispatch 才创建任务申请。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
sys.path.insert(0, str(AP))
sys.path.insert(0, str(ROOT / "tools"))
CASES = ROOT / "deliverables" / "cases"
MEETINGS = AP / "data" / "meetings" / "hq"
BASE = "http://127.0.0.1:8796"
CASE_RE = re.compile(r"^CASE-[A-Za-z0-9._-]{3,80}$")

ROLES = [
    ("TIANFENG", "A0", "田丰", "目标拆解、任务DAG、派单、汇总、推进"),
    ("ZHUGE", "AI1", "诸葛亮", "反向质询、pre-mortem、找反例、挑战前提"),
    ("ZHAOYUN", "ZHAOYUN", "赵云", "独立证据复核、审计、经验归档"),
    ("HUANGZHONG", "HUANGZHONG", "黄忠", "验收标准、抽样、质量终检、放行/拒收"),
    ("GUANYU", "GUANYU", "关羽", "代码审计、技术债、门禁、整改"),
]
STAGES = ("DRAFT", "AGGREGATED", "REVIEWED", "MEETING", "PROPOSED", "APPROVED", "DISPATCHED")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(cid: str) -> str:
    cid = str(cid or "").strip()
    if not CASE_RE.fullmatch(cid):
        raise ValueError("bad_case_id")
    return cid


def _path(case_id: str, base: Path | None = None) -> Path:
    return (Path(base) if base else CASES) / _safe_id(case_id) / "case.json"


def load(case_id: str, base: Path | None = None) -> dict:
    p = _path(case_id, base)
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(d, dict) or d.get("case_id") != case_id:
        raise ValueError("case_invalid")
    return d


def save(case: dict, base: Path | None = None) -> None:
    p = _path(case["case_id"], base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def new_case(title: str, materials: list[str], case_id: str = "", base: Path | None = None) -> dict:
    cid = case_id or ("CASE-%s-%s" % (datetime.date.today().strftime("%Y%m%d"), os.urandom(3).hex()))
    cid = _safe_id(cid)
    mat = []
    for x in materials:
        p = Path(x)
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        mat.append({"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw), "title": p.name})
    c = {"schema": "hq/case/v1", "case_id": cid, "title": str(title)[:200],
         "created_at": _now(), "stage": "DRAFT", "materials": mat,
         "a0_summary": "", "opinions": [], "meeting_id": "", "proposal": "",
         "approval": {}, "dispatch": {}, "history": []}
    save(c, base)
    return c


def _doc_text(paths: list[str], limit: int = 5000) -> str:
    out = []
    for x in paths:
        try:
            t = Path(x).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append("# " + Path(x).name + "\n" + t[:limit])
    return "\n\n".join(out)[:12000]


def _chat(agent: str, system: str, user: str, base: str = BASE, timeout: int = 120) -> dict:
    body = json.dumps({"agent": agent, "system": system, "purpose": "case",
                       "user": user}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.loads(r.read().decode("utf-8"))
    if not env.get("ok"):
        raise RuntimeError(str(env.get("error") or "model_failed")[:200])
    txt = str((env.get("data") or {}).get("text") or "").strip()
    if not txt:
        raise RuntimeError("empty_model_text")
    return {"text": txt[:6000], "binding": env.get("binding") or "",
            "latency_ms": env.get("latency_ms"), "degraded": bool(env.get("degraded"))}


def aggregate(case: dict, base: str = BASE) -> dict:
    if case["stage"] != "DRAFT":
        raise ValueError("stage_must_be_DRAFT")
    text = _doc_text([m["path"] for m in case.get("materials") or []])
    system = ("你是田丰（A0），零界参谋长。汇总材料，输出：事实/证据缺口/关键矛盾/建议议程；"
              "不编造数字，不代替老田拍板。")
    r = _chat("A0", system, "议题：" + case["title"] + "\n材料：\n" + (text or "（无材料）"), base)
    case["a0_summary"] = r["text"]
    case["stage"] = "AGGREGATED"
    case["history"].append({"at": _now(), "action": "aggregate", "binding": r["binding"]})
    return case


def review(case: dict, base: str = BASE, retry_failed: bool = True) -> dict:
    if case["stage"] not in ("AGGREGATED", "REVIEWED"):
        raise ValueError("stage_must_be_AGGREGATED")
    text = _doc_text([m["path"] for m in case.get("materials") or []])
    out = []
    for _rk, aid, name, duty in ROLES:
        system = (f"你是{name}（{aid}），零界指挥部五角色之一，职责：{duty}。"
                  "只从职能面读材料给意见；不编造数字，缺证据标需查证；不代替老田拍板。"
                  "输出：结论/证据/风险/建议/是否升级。")
        user = ("议题：" + case["title"] + "\n田丰汇总：\n" + case.get("a0_summary", "")[:3000] +
                "\n材料：\n" + (text or "（无材料）"))
        try:
            r = _chat(aid, system, user, base)
            out.append({"role": _rk, "seat": aid, "name": name, "text": r["text"],
                        "binding": r["binding"], "degraded": r["degraded"]})
        except Exception as e:  # noqa: BLE001
            out.append({"role": _rk, "seat": aid, "name": name, "error": type(e).__name__ + ":" + str(e)[:160]})
    case["opinions"] = out
    ok = len([x for x in out if x.get("text")]) == 5
    case["stage"] = "REVIEWED" if ok else "AGGREGATED"
    case["history"].append({"at": _now(), "action": "review", "ok": ok})
    return case


def meeting(case: dict, meeting_dir: Path | None = None) -> dict:
    if case["stage"] != "REVIEWED":
        raise ValueError("stage_must_be_REVIEWED")
    from legion_command import hq_meeting
    payload = {
        "type": "operation", "attendees": ["A0", "AI1", "ZHAOYUN", "HUANGZHONG", "GUANYU"],
        "agenda": [{"item": "田丰汇总", "owner": "A0", "minutes": 5},
                    {"item": "五角色意见", "owner": "ALL", "minutes": 10},
                    {"item": "方案与决策", "owner": "A0", "minutes": 10}],
        "transcript": [{"by": o["seat"], "text": o.get("text") or o.get("error") or "",
                        "src": "live" if o.get("text") else "error", "evidence": []}
                       for o in case["opinions"]],
        "decisions": [], "acceptance": [],
    }
    code, env = hq_meeting.save_meeting(Path(meeting_dir) if meeting_dir else MEETINGS, payload)
    if code != 200 or not env.get("ok"):
        raise RuntimeError("meeting_save_failed: %s" % env.get("error"))
    case["meeting_id"] = env["meeting_id"]
    case["stage"] = "MEETING"
    case["history"].append({"at": _now(), "action": "meeting", "meeting_id": env["meeting_id"]})
    return case


def propose(case: dict, base: str = BASE) -> dict:
    if case["stage"] != "MEETING":
        raise ValueError("stage_must_be_MEETING")
    opinions = "\n".join((o.get("name", "") + "：" + (o.get("text") or "")[:1500]) for o in case["opinions"])
    system = ("你是田丰（A0）。基于五角色意见，给出可执行方案：推荐方案/备选/风险/回滚/验收/需老田批准点。"
              "只输出方案，不派单、不执行。")
    r = _chat("A0", system, "议题：" + case["title"] + "\n五角色意见：\n" + opinions, base)
    case["proposal"] = r["text"]
    case["stage"] = "PROPOSED"
    case["history"].append({"at": _now(), "action": "propose", "binding": r["binding"]})
    return case


def approve(case: dict, approver: str, note: str = "") -> dict:
    if case["stage"] != "PROPOSED":
        raise ValueError("stage_must_be_PROPOSED")
    if not str(approver or "").strip():
        raise ValueError("approver_required")
    case["approval"] = {"approved": True, "approver": str(approver)[:80],
                        "note": str(note)[:500], "approved_at": _now()}
    case["stage"] = "APPROVED"
    case["history"].append({"at": _now(), "action": "approve", "approver": approver})
    return case


def dispatch(case: dict, owner: str = "A0", meeting_dir: Path | None = None, submit=None) -> dict:
    if case["stage"] != "APPROVED":
        raise ValueError("human_approval_required")
    if not case.get("meeting_id"):
        raise ValueError("meeting_required")
    from legion_command import hq_meeting
    if submit is None:
        from legion_command import inbox_gateway as submit_mod
        submit = submit_mod.submit
    req = {"meeting_id": case["meeting_id"],
           "title": ("CASE " + case["case_id"] + "：" + case["title"])[:120],
           "order_type": "work", "owner": owner}
    code, env = hq_meeting.dispatch_meeting(Path(meeting_dir) if meeting_dir else MEETINGS, req, submit)
    if code != 200 or not env.get("ok"):
        raise RuntimeError("dispatch_failed: %s" % env.get("error"))
    case["dispatch"] = {"owner": owner, "at": _now(), "result": env.get("dispatch")}
    case["stage"] = "DISPATCHED"
    case["history"].append({"at": _now(), "action": "dispatch", "owner": owner})
    return case


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Zero-Bound Case 流程")
    ap.add_argument("action", choices=["new", "aggregate", "review", "meeting", "propose", "approve", "dispatch", "show"])
    ap.add_argument("--case", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--materials", nargs="*", default=[])
    ap.add_argument("--approver", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--owner", default="A0")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "new":
            c = new_case(a.title, a.materials)
        else:
            c = load(a.case)
            if a.action == "aggregate": c = aggregate(c)
            elif a.action == "review": c = review(c)
            elif a.action == "meeting": c = meeting(c)
            elif a.action == "propose": c = propose(c)
            elif a.action == "approve": c = approve(c, a.approver, a.note)
            elif a.action == "dispatch": c = dispatch(c, a.owner)
        save(c)
    except Exception as e:  # noqa: BLE001
        print("case_error: %s" % str(e)[:300], file=sys.stderr)
        return 2
    print(json.dumps(c, ensure_ascii=False, indent=2) if a.json else
          "%s %s -> %s" % (c["case_id"], c["title"], c["stage"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
