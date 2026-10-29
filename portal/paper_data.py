# -*- coding: utf-8 -*-
"""paper MVP 只读数据口（2026-10-30 WO-INV-PAPER-UI）。

为 `/paper` 页面提供 JSON 快照；只读 paper 台账/状态/风控配置，不接实盘。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime

PORTAL = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PORTAL)
PAPER_DIR = os.path.join(REPO, "agent_platform", "data", "paper")
PAPER_LEDGER = os.path.join(PAPER_DIR, "paper_ledger.jsonl")
PAPER_STATE = os.path.join(PAPER_DIR, "state.json")
PAPER_CONFIG = os.path.join(REPO, "agent_platform", "paper", "paper_config.json")
PAPER_KILL = os.path.join(PAPER_DIR, "KILL")
RECENT_LIMIT = 30


def _read_json(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_ledger(path=None):
    rows = []
    p = path or PAPER_LEDGER
    if not os.path.exists(p):
        return rows
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _chain_ok(rows):
    prev = ""
    for r in rows:
        if r.get("prev_hash") != prev:
            return False
        prev = r.get("hash") or ""
    return True


def paper_snapshot(ledger=None, state=None, config=None, kill=None):
    rows = _read_ledger(ledger)
    st = _read_json(state or PAPER_STATE, {}) or {}
    cfg = _read_json(config or PAPER_CONFIG, {}) or {}
    positions = []
    for sym, p in (st.get("positions") or {}).items():
        if not isinstance(p, dict):
            continue
        positions.append({
            "symbol": sym,
            "industry": p.get("industry") or "",
            "weight": round(float(p.get("weight") or 0.0), 6),
            "entry_price": p.get("entry_price"),
            "last_price": p.get("last_price"),
            "updated_at": p.get("updated_at"),
        })
    positions.sort(key=lambda x: -x["weight"])
    by_date = {}
    blocked = reduced = 0
    for r in rows:
        d = str(r.get("date") or "")
        by_date[d] = by_date.get(d, 0) + 1
        if r.get("verdict") == "block":
            blocked += 1
        elif r.get("verdict") == "reduce":
            reduced += 1
    recent = []
    for r in rows[-RECENT_LIMIT:]:
        recent.append({
            "date": r.get("date"), "symbol": r.get("symbol"), "name": r.get("name"),
            "recommendation": r.get("recommendation"), "confidence": r.get("confidence"),
            "verdict": r.get("verdict"), "action": r.get("action"),
            "weight_before": r.get("weight_before"), "weight_after": r.get("weight_after"),
            "price": r.get("price"), "industry": r.get("industry"),
            "risk_level": r.get("risk_level"),
            "reasons": [x.get("code") for x in (r.get("reasons") or []) if isinstance(x, dict)],
            "nav": r.get("nav"), "hash": (r.get("hash") or "")[:12],
        })
    recent.reverse()
    return {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "paper",
        "trading_permitted": False,
        "nav": st.get("nav"),
        "cash_weight": st.get("cash_weight"),
        "positions": positions,
        "positions_count": len(positions),
        "ledger_rows": len(rows),
        "recent": recent,
        "decisions_by_date": by_date,
        "last_date": max(by_date) if by_date else "",
        "blocked": blocked,
        "reduced": reduced,
        "hash_chain_ok": _chain_ok(rows),
        "killed": os.path.exists(kill or PAPER_KILL),
        "limits": cfg.get("limits") or {},
        "industries": cfg.get("industries") or {},
    }
