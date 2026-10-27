# -*- coding: utf-8 -*-
"""zhuge_decision_record.py — P1-4 结构化决策记录 v0（追加式 JSONL）。

记录一次「建议 → 决策 → 落地 → 复盘」：
- 建议/备选/证据/风险/置信度（诸葛亮侧）
- 决策人/结论/备注（老田侧）
- 采纳结果/回滚/复盘日期（执行侧）
- 可选关联：质询卡 card_id、采纳账本 adoption_id

只读写 `work/p1/decisions/decisions.jsonl`（不入库）；不派单、不执行、无 L2。
状态机：proposed → approved | rejected | deferred
        approved → implemented | superseded
        deferred → proposed | approved | rejected
        implemented → superseded
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORE_REL = Path("work") / "p1" / "decisions" / "decisions.jsonl"
STATUSES = ("proposed", "approved", "rejected", "deferred", "implemented", "superseded")
TRANSITIONS = {
    "proposed": {"approved", "rejected", "deferred"},
    "deferred": {"proposed", "approved", "rejected"},
    "approved": {"implemented", "superseded"},
    "implemented": {"superseded"},
    "rejected": set(),
    "superseded": set(),
}
CONFIDENCES = ("high", "medium", "low", "unknown")
ID_RE = re.compile(r"^DR-\d{8}-\d{2,3}$")


def _root() -> Path:
    override = os.environ.get("ZHUGE_DECISION_REPO")
    return Path(override).resolve() if override else REPO_ROOT


def _store() -> Path:
    return _root() / STORE_REL


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _load_events() -> list[dict]:
    path = _store()
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("event") and ev.get("id"):
            events.append(ev)
    return events


def _fold(events: list[dict]) -> dict:
    records: dict[str, dict] = {}
    for ev in events:
        rid = ev.get("id")
        kind = ev.get("event")
        if kind == "created" and isinstance(ev.get("record"), dict):
            rec = dict(ev["record"])
            rec.setdefault("status", "proposed")
            rec["events"] = 1
            records[rid] = rec
        elif rid in records:
            records[rid]["events"] = records[rid].get("events", 1) + 1
            if kind == "decided":
                records[rid].update({"status": ev.get("status"),
                                     "decided_by": ev.get("by"),
                                     "decision_note": ev.get("note"),
                                     "decided_at": ev.get("at")})
            elif kind == "updated":
                for key in ("outcome", "adoption_id", "adoption_domains", "review_at",
                            "status", "implemented_at", "superseded_by", "confidence"):
                    if key in ev:
                        records[rid][key] = ev[key]
            elif kind == "rejected_event":
                pass
    return records


def _append(events: list[dict]) -> None:
    if not events:
        return
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _next_id(records: dict) -> str:
    prefix = "DR-%s-" % datetime.now().strftime("%Y%m%d")
    nums = [int(r.split("-")[-1]) for r in records
            if r.startswith(prefix) and r.split("-")[-1].isdigit()]
    return "%s%02d" % (prefix, (max(nums) + 1) if nums else 1)


def create(title: str, question: str, recommendation: str, alternatives=None,
           evidence=None, risks=None, confidence: str = "medium", tags=None,
           source_card: str = "", review_at: str = "",
           adoption_domains=None) -> dict:
    title, question, recommendation = [(x or "").strip() for x in
                                       (title, question, recommendation)]
    if not title or not question or not recommendation:
        return {"ok": False, "error": "title/question/recommendation 均为必填"}
    if confidence not in CONFIDENCES:
        return {"ok": False, "error": "confidence 仅支持 %s" % "/".join(CONFIDENCES)}
    records = _fold(_load_events())
    idem = hashlib.sha256((title + "\n" + question + "\n" + recommendation)
                          .encode("utf-8")).hexdigest()[:16]
    for rec in records.values():
        if rec.get("idem_key") == idem:
            return {"ok": True, "duplicate": True, "id": rec.get("id")}
    rid = _next_id(records)
    record = {
        "id": rid,
        "ts": _now(),
        "title": title[:300],
        "question": question[:1000],
        "recommendation": recommendation[:4000],
        "alternatives": [str(a)[:500] for a in (alternatives or []) if str(a).strip()][:8],
        "evidence": [str(e)[:500] for e in (evidence or []) if str(e).strip()][:20],
        "risks": [str(r)[:500] for r in (risks or []) if str(r).strip()][:12],
        "confidence": confidence,
        "status": "proposed",
        "source_card": (source_card or "")[:80],
        "review_at": (review_at or "")[:10],
        "adoption_domains": [str(d).strip()[:80] for d in (adoption_domains or [])
                             if str(d).strip()][:20],
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()][:12],
        "idem_key": idem,
    }
    _append([{"event": "created", "id": rid, "at": _now(), "record": record}])
    return {"ok": True, "duplicate": False, "id": rid, "record": record}


def decide(rid: str, status: str, by: str = "", note: str = "") -> dict:
    if status not in STATUSES or status == "proposed":
        return {"ok": False, "error": "decide 仅支持 approved/rejected/deferred/implemented/superseded"}
    records = _fold(_load_events())
    rec = records.get(rid)
    if not rec:
        return {"ok": False, "error": "记录不存在：%s" % rid}
    current = rec.get("status")
    if status not in TRANSITIONS.get(current, set()):
        return {"ok": False, "error": "非法迁移：%s → %s" % (current, status)}
    if status in ("approved", "implemented") and not (by or "").strip():
        return {"ok": False, "error": "approved/implemented 必须记录决策人 --by"}
    _append([{"event": "decided" if status in ("approved", "rejected", "deferred")
              else "updated", "id": rid, "at": _now(), "status": status,
              "by": (by or "").strip()[:80], "note": (note or "")[:1000]}])
    return {"ok": True, "id": rid, "status": status}


def update(rid: str, outcome: str = "", adoption_id: str = "", review_at: str = "",
           confidence: str = "", adoption_domains=None) -> dict:
    records = _fold(_load_events())
    rec = records.get(rid)
    if not rec:
        return {"ok": False, "error": "记录不存在：%s" % rid}
    if rec.get("status") not in ("approved", "implemented", "deferred"):
        return {"ok": False, "error": "仅 approved/implemented/deferred 可更新结果（当前 %s）" % rec.get("status")}
    ev = {"event": "updated", "id": rid, "at": _now()}
    if outcome:
        ev["outcome"] = outcome[:2000]
    if adoption_id:
        ev["adoption_id"] = adoption_id[:80]
    if adoption_domains:
        current = list(rec.get("adoption_domains") or [])
        merged = list(dict.fromkeys(current + [str(d).strip()[:80]
                                              for d in adoption_domains if str(d).strip()]))
        ev["adoption_domains"] = merged[:20]
    if review_at:
        ev["review_at"] = review_at[:10]
    if confidence:
        if confidence not in CONFIDENCES:
            return {"ok": False, "error": "confidence 非法"}
        ev["confidence"] = confidence
    if len(ev) == 3:
        return {"ok": True, "id": rid, "updated": False}
    _append([ev])
    return {"ok": True, "id": rid, "updated": True}


def list_records(status: str = "", limit: int = 20) -> dict:
    records = _fold(_load_events())
    rows = [r for r in records.values() if not status or r.get("status") == status]
    rows.sort(key=lambda r: str(r.get("id")), reverse=True)
    return {"ok": True, "total": len(rows),
            "rows": rows[:max(1, min(limit, 100))]}


def show(rid: str) -> dict:
    rec = _fold(_load_events()).get(rid)
    return {"ok": bool(rec), "record": rec} if rec else {"ok": False, "error": "记录不存在：%s" % rid}


def review_due(day: str = "") -> dict:
    today = (day or _today())[:10]
    rows = [r for r in _fold(_load_events()).values()
            if r.get("review_at") and r["review_at"] <= today
            and r.get("status") in ("approved", "implemented")]
    rows.sort(key=lambda r: str(r.get("review_at")))
    return {"ok": True, "day": today, "count": len(rows), "rows": rows}


def validate() -> dict:
    records = _fold(_load_events())
    issues = []
    for rid, rec in records.items():
        if not ID_RE.match(rid):
            issues.append({"id": rid, "issue": "id_format"})
        if rec.get("confidence") not in CONFIDENCES:
            issues.append({"id": rid, "issue": "confidence_invalid"})
        if rec.get("status") not in STATUSES:
            issues.append({"id": rid, "issue": "status_invalid"})
        if not rec.get("recommendation"):
            issues.append({"id": rid, "issue": "recommendation_empty"})
    return {"ok": not issues, "records": len(records), "issues": issues}


def _json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="zhuge decision record v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--question", required=True)
    c.add_argument("--recommendation", required=True)
    c.add_argument("--alternatives", default="")
    c.add_argument("--evidence", default="")
    c.add_argument("--risks", default="")
    c.add_argument("--confidence", default="medium")
    c.add_argument("--tags", default="")
    c.add_argument("--source-card", default="")
    c.add_argument("--review-at", default="")
    c.add_argument("--adoption-domains", default="")
    d = sub.add_parser("decide")
    d.add_argument("id")
    d.add_argument("--status", required=True)
    d.add_argument("--by", default="")
    d.add_argument("--note", default="")
    u = sub.add_parser("update")
    u.add_argument("id")
    u.add_argument("--outcome", default="")
    u.add_argument("--adoption-id", default="")
    u.add_argument("--review-at", default="")
    u.add_argument("--confidence", default="")
    u.add_argument("--adoption-domains", default="")
    l = sub.add_parser("list")
    l.add_argument("--status", default="")
    l.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("show")
    s.add_argument("id")
    r = sub.add_parser("review-due")
    r.add_argument("--day", default="")
    sub.add_parser("validate")
    args = ap.parse_args(argv)
    if args.cmd == "create":
        out = create(args.title, args.question, args.recommendation,
                     args.alternatives.split("|") if args.alternatives else [],
                     args.evidence.split("|") if args.evidence else [],
                     args.risks.split("|") if args.risks else [],
                     args.confidence,
                     args.tags.split(",") if args.tags else [],
                     args.source_card, args.review_at,
                     args.adoption_domains.split(",") if args.adoption_domains else [])
    elif args.cmd == "decide":
        out = decide(args.id, args.status, args.by, args.note)
    elif args.cmd == "update":
        out = update(args.id, args.outcome, args.adoption_id, args.review_at,
                     args.confidence,
                     args.adoption_domains.split(",") if args.adoption_domains else [])
    elif args.cmd == "list":
        out = list_records(args.status, args.limit)
    elif args.cmd == "show":
        out = show(args.id)
    elif args.cmd == "review-due":
        out = review_due(args.day)
    else:
        out = validate()
    _json(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
