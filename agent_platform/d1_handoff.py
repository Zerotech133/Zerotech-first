# -*- coding: utf-8 -*-
"""D1 交棒回执（P1，2026-11-01 WO-D1-HANDOFF-20261101）。

D1 研究完成后把结论交 WA（盯盘）或 TR（模拟执行）；本模块只落**交棒回执**，
不生成订单、不执行、不接实盘。目标方确认（ack）仅表示“收到/拒收”，不表示已交易。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "d1_handoff/v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "agent_platform" / "data" / "d1_handoffs.jsonl"
TARGETS = ("WA1", "WA2", "WA3", "TR1", "TR2", "TR3", "WATCH_TOWER")
ACTIONS = ("watch", "trade", "no_trade")
ACK_STATUS = ("received", "rejected", "handled")
REQUIRED = ("code", "target", "action", "rationale")


class D1HandoffError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_receipt_id(now=None):
    dt = now or datetime.now()
    return "d1h-" + dt.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _clip(v, n):
    return str(v or "").strip()[:n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_handoff(payload):
    if not isinstance(payload, dict):
        raise D1HandoffError("bad_body", "body 必须是 object")
    for k in REQUIRED:
        if not _clip(payload.get(k), 200):
            raise D1HandoffError("missing_%s" % k, "%s 必填" % k)
    target = _clip(payload.get("target"), 16).upper()
    if target not in TARGETS:
        raise D1HandoffError("bad_target", "target 必须是 %s 之一" % "/".join(TARGETS))
    action = _clip(payload.get("action"), 16).lower()
    if action not in ACTIONS:
        raise D1HandoffError("bad_action", "action 必须是 %s 之一" % "/".join(ACTIONS))
    if action == "trade" and target not in ("TR1", "TR2", "TR3"):
        raise D1HandoffError("bad_target_action", "trade 只能交 TR1/TR2/TR3")
    if action == "watch" and target not in ("WA1", "WA2", "WA3", "WATCH_TOWER"):
        raise D1HandoffError("bad_target_action", "watch 只能交 WA1/WA2/WA3/WATCH_TOWER")
    if payload.get("paper_only") is False:
        raise D1HandoffError("not_paper", "只允许 paper_only=true")
    for key in ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account"):
        if key in payload and payload.get(key) not in (None, "", [], {}):
            raise D1HandoffError("execution_field_forbidden", "handoff 不得携带执行字段 %r" % key)
    buy_zone = payload.get("buy_zone")
    if buy_zone is not None:
        if not isinstance(buy_zone, (list, tuple)) or len(buy_zone) != 2:
            raise D1HandoffError("bad_buy_zone", "buy_zone 必须是 [low, high]")
        lo, hi = _f(buy_zone[0]), _f(buy_zone[1])
        if lo is None or hi is None or lo > hi:
            raise D1HandoffError("bad_buy_zone", "buy_zone 数值非法")
        buy_zone = [lo, hi]
    return {
        "schema": SCHEMA,
        "receipt_id": _clip(payload.get("receipt_id"), 40) or new_receipt_id(),
        "code": _clip(payload.get("code"), 32),
        "name": _clip(payload.get("name"), 64),
        "decision_id": _clip(payload.get("decision_id"), 40),
        "target": target,
        "action": action,
        "rationale": _clip(payload.get("rationale"), 1000),
        "buy_zone": buy_zone,
        "stop_loss": _f(payload.get("stop_loss")),
        "trigger": _clip(payload.get("trigger"), 200),
        "evidence": [_clip(x, 300) for x in (payload.get("evidence") or [])][:20],
        "created_by": "D1",
        "created_at": _clip(payload.get("created_at"), 40) or _now(),
        "status": "pending_ack",
        "ack": None,
        "paper_only": True,
        "not_a_trade": True,
    }


def read_handoffs(ledger=None):
    path = Path(ledger or DEFAULT_LEDGER)
    rows = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_all(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_handoff(payload, ledger=None):
    row = validate_handoff(payload)
    path = Path(ledger or DEFAULT_LEDGER)
    rows = read_handoffs(path)
    if any(r.get("receipt_id") == row["receipt_id"] for r in rows):
        raise D1HandoffError("duplicate_receipt", "receipt_id 已存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return row


def ack_handoff(receipt_id, by, status="received", note="", ledger=None):
    if status not in ACK_STATUS:
        raise D1HandoffError("bad_ack_status", "ack status 必须是 %s" % "/".join(ACK_STATUS))
    path = Path(ledger or DEFAULT_LEDGER)
    rows = read_handoffs(path)
    hit = None
    for r in rows:
        if r.get("receipt_id") == receipt_id:
            hit = r
            break
    if hit is None:
        raise D1HandoffError("receipt_not_found", "receipt 不存在")
    if hit.get("ack"):
        raise D1HandoffError("already_acked", "receipt 已确认")
    hit["ack"] = {"by": _clip(by, 32), "status": status,
                  "note": _clip(note, 300), "acked_at": _now()}
    hit["status"] = "acked"
    _write_all(path, rows)
    return hit


def latest_for(code, ledger=None):
    code = _clip(code, 32)
    for r in reversed(read_handoffs(ledger)):
        if r.get("code") == code:
            return r
    return None


def summary(ledger=None):
    rows = read_handoffs(ledger)
    pending = [r for r in rows if r.get("status") == "pending_ack"]
    return {
        "schema": "d1_handoff_summary/v1",
        "total": len(rows),
        "pending_ack": len(pending),
        "acked": len([r for r in rows if r.get("status") == "acked"]),
        "latest": rows[-1] if rows else None,
        "paper_only": True,
    }
