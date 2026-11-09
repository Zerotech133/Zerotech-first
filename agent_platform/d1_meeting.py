# -*- coding: utf-8 -*-
"""D1 投研会运行时（P0，2026-11-01 WO-D1-MEETING-20261101）。

规则真源：agent_platform/registry/d1_meeting.json
  chair=D1（鲁肃）；voters=T1/T2/N1/F1；observer=ST1/QS1；quorum=5；
  多数票；D1 主席最终表决/否决。

边界：只落会议纪要 + D1 final decision 候选；不自动执行、不下单、不接实盘。
会议最终化不自动写 d1_decisions.jsonl；由 D1 再确认（API 可显式 write_decision）。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from agent_platform import d1_decision as D

SCHEMA = "d1_meeting/v1"
CHAIR = "D1"
CHAIR_NAME = "鲁肃"
VOTERS = ("T1", "T2", "N1", "F1")
QUORUM = 5
STATUS_OPEN = "open"
STATUS_FINAL = "finalized"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "agent_platform" / "data" / "meetings" / "d1"
MEETING_ID_RE = __import__("re").compile(r"^d1m-\d{14}-[0-9a-f]{6}$")


class D1MeetingError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_meeting_id(now=None):
    dt = now or datetime.now()
    return "d1m-" + dt.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _clip(v, n):
    return str(v or "").strip()[:n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _path(meeting_id, dirpath=None):
    mid = str(meeting_id or "").strip()
    if not MEETING_ID_RE.match(mid):
        raise D1MeetingError("bad_meeting_id", "meeting_id 非法")
    return Path(dirpath or DEFAULT_DIR) / (mid + ".json")


def _write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)
    return path


def _validate_tier(tier):
    t = _clip(tier, 24).upper()
    if t not in D.TIERS:
        raise D1MeetingError("bad_tier", "tier 必须是 %s 之一" % "/".join(D.TIERS))
    return t


def _validate_confidence(v):
    c = _f(v)
    if c is None or not (0.0 <= c <= 1.0):
        raise D1MeetingError("bad_confidence", "confidence 必须是 0~1")
    return round(c, 4)


def _evidence(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise D1MeetingError("bad_evidence", "evidence 必须是列表")
    return [_clip(x, 300) for x in v if _clip(x, 300)]


def create_meeting(code, topic="", asof="", agenda=None, created_by=CHAIR,
                   dirpath=None, meeting_id=None):
    code = _clip(code, 32)
    if not code:
        raise D1MeetingError("missing_code", "code 必填")
    if _clip(created_by, 16).upper() not in (CHAIR,):
        raise D1MeetingError("bad_creator", "仅 D1 可发起 D1 投研会")
    mid = _clip(meeting_id, 40) or new_meeting_id()
    if not MEETING_ID_RE.match(mid):
        raise D1MeetingError("bad_meeting_id", "meeting_id 格式应为 d1m-YYYYMMDDHHMMSS-xxxxxx")
    path = _path(mid, dirpath)
    if path.exists():
        raise D1MeetingError("duplicate_meeting", "meeting_id 已存在")
    doc = {
        "schema": SCHEMA,
        "meeting_id": mid,
        "code": code,
        "topic": _clip(topic, 200) or ("%s D1 投研会" % code),
        "asof": _clip(asof, 16),
        "chair": CHAIR,
        "chair_name": CHAIR_NAME,
        "voters": list(VOTERS),
        "quorum": QUORUM,
        "status": STATUS_OPEN,
        "created_at": _now(),
        "created_by": CHAIR,
        "agenda": [_clip(x, 200) for x in (agenda or [])][:10],
        "votes": {},
        "final_decision": None,
        "paper_only": True,
        "not_a_trade": True,
    }
    _write(path, doc)
    return doc


def get_meeting(meeting_id, dirpath=None):
    path = _path(meeting_id, dirpath)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise D1MeetingError("meeting_not_found", "会议不存在：%s" % meeting_id)
    except ValueError:
        raise D1MeetingError("meeting_corrupt", "会议文件损坏：%s" % meeting_id)
    if not isinstance(doc, dict):
        raise D1MeetingError("meeting_corrupt", "会议文件非 object")
    return doc


def submit_vote(meeting_id, seat, tier, confidence, rationale, evidence=None,
                dirpath=None):
    seat = _clip(seat, 8).upper()
    if seat not in VOTERS:
        raise D1MeetingError("bad_seat", "投票席必须是 T1/T2/N1/F1")
    doc = get_meeting(meeting_id, dirpath)
    if doc.get("status") != STATUS_OPEN:
        raise D1MeetingError("meeting_closed", "会议已结束，不能再投票")
    if seat in (doc.get("votes") or {}):
        raise D1MeetingError("duplicate_vote", "%s 已投过票" % seat)
    tier = _validate_tier(tier)
    confidence = _validate_confidence(confidence)
    rationale = _clip(rationale, 1000)
    if len(rationale) < 5:
        raise D1MeetingError("bad_rationale", "投票理由至少 5 字")
    doc.setdefault("votes", {})[seat] = {
        "seat": seat,
        "tier": tier,
        "tier_label": D.TIER_LABELS[tier],
        "confidence": confidence,
        "rationale": rationale,
        "evidence": _evidence(evidence),
        "voted_at": _now(),
    }
    _write(_path(meeting_id, dirpath), doc)
    return doc


def can_finalize(doc):
    votes = doc.get("votes") or {}
    missing = [s for s in VOTERS if s not in votes]
    return {"ok": not missing, "missing": missing,
            "vote_count": len(votes), "quorum": QUORUM}


def finalize(meeting_id, tier, confidence, rationale, evidence=None, dissent=None,
             decided_by=CHAIR, dirpath=None):
    doc = get_meeting(meeting_id, dirpath)
    if doc.get("status") != STATUS_OPEN:
        raise D1MeetingError("meeting_closed", "会议已结束")
    if _clip(decided_by, 16).upper() != CHAIR:
        raise D1MeetingError("bad_decider", "最终表决只能 D1 主席本人")
    check = can_finalize(doc)
    if not check["ok"]:
        raise D1MeetingError("quorum_not_reached",
                             "未到 quorum=5：缺 %s" % ",".join(check["missing"]))
    tier = _validate_tier(tier)
    confidence = _validate_confidence(confidence)
    rationale = _clip(rationale, 2000)
    if len(rationale) < 10:
        raise D1MeetingError("bad_rationale", "最终裁决理由至少 10 字")
    candidate = D.validate_decision({
        "code": doc.get("code"),
        "tier": tier,
        "confidence": confidence,
        "rationale": rationale,
        "evidence": _evidence(evidence),
        "dissent": _evidence(dissent),
        "decided_by": CHAIR,
        "source": "d1_meeting",
        "paper_only": True,
    })
    doc["status"] = STATUS_FINAL
    doc["finalized_at"] = _now()
    doc["final_decision"] = {
        "tier": candidate["tier"],
        "tier_label": candidate["tier_label"],
        "confidence": candidate["confidence"],
        "rationale": candidate["rationale"],
        "evidence": candidate["evidence"],
        "dissent": candidate["dissent"],
        "decided_by": candidate["decided_by"],
        "decided_by_name": candidate["decided_by_name"],
        "finalized_at": doc["finalized_at"],
        "vote_count": len(doc.get("votes") or {}),
        "quorum": QUORUM,
    }
    doc["decision_candidate"] = candidate
    doc["paper_only"] = True
    _write(_path(meeting_id, dirpath), doc)
    return doc


def list_meetings(dirpath=None, limit=50):
    d = Path(dirpath or DEFAULT_DIR)
    if not d.exists():
        return []
    rows = []
    for p in sorted(d.glob("d1m-*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            rows.append(doc)
        if len(rows) >= limit:
            break
    return rows


def summary(dirpath=None):
    rows = list_meetings(dirpath)
    return {
        "schema": "d1_meeting_summary/v1",
        "total": len(rows),
        "open": sum(1 for r in rows if r.get("status") == STATUS_OPEN),
        "finalized": sum(1 for r in rows if r.get("status") == STATUS_FINAL),
        "latest": rows[0] if rows else None,
        "dir": str(Path(dirpath or DEFAULT_DIR)),
        "paper_only": True,
    }
