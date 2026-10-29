# -*- coding: utf-8 -*-
"""纸面组合账本（paper MVP，2026-10-30 WO-INV-PAPER-MVP）。

- 只记账、不真实下单；
- append-only hash-chain 台账（prev_hash/hash）；
- 幂等：同一 (date, symbol) 已记账则跳过；
- kill switch：环境 PAPER_KILL=1 或文件 data/paper/KILL 存在即停止；
- 状态文件原子替换。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PAPER_DATA = os.path.join(os.path.dirname(BASE), "data", "paper")
DEFAULT_STATE = os.path.join(PAPER_DATA, "state.json")
DEFAULT_LEDGER = os.path.join(PAPER_DATA, "paper_ledger.jsonl")
KILL_FILE = os.path.join(PAPER_DATA, "KILL")


def kill_switch_on(env=None):
    env = os.environ if env is None else env
    return str(env.get("PAPER_KILL") or "").strip().lower() in ("1", "on", "true", "yes") \
        or os.path.exists(KILL_FILE)


def empty_state():
    return {"positions": {}, "cash_weight": 1.0, "nav": 1.0, "updated_at": ""}


def load_state(path=None):
    try:
        s = json.load(io.open(path or DEFAULT_STATE, encoding="utf-8"))
        if isinstance(s, dict) and isinstance(s.get("positions"), dict):
            return s
    except (OSError, ValueError):
        pass
    return empty_state()


def save_state(state, path=None):
    path = path or DEFAULT_STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _canon(rec):
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def last_hash(path=None):
    path = path or DEFAULT_LEDGER
    if not os.path.exists(path):
        return ""
    h = ""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                h = json.loads(line).get("hash") or h
            except ValueError:
                continue
    return h


def existing_keys(path=None):
    path = path or DEFAULT_LEDGER
    out = set()
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
                out.add((r.get("date"), r.get("symbol")))
            except ValueError:
                continue
    return out


def append_ledger(record, path=None):
    path = path or DEFAULT_LEDGER
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prev = last_hash(path)
    rec = dict(record)
    rec["prev_hash"] = prev
    rec["hash"] = hashlib.sha256((prev + _canon(record)).encode("utf-8")).hexdigest()
    with io.open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _nav(state):
    cash = float(state.get("cash_weight") or 0.0)
    nav = cash
    for p in (state.get("positions") or {}).values():
        entry = float(p.get("entry_price") or 0.0)
        last = float(p.get("last_price") or entry or 1.0)
        nav += float(p.get("weight") or 0.0) * ((last / entry) if entry > 0 else 1.0)
    return round(nav, 6)


def apply_decision(decision, gate, state, date, ledger_path=None, seen=None):
    """应用一条决策；返回 {record, duplicate, state}。不真实下单。"""
    symbol = str(decision.get("symbol") or "")
    seen = seen if seen is not None else existing_keys(ledger_path)
    key = (date, symbol)
    if key in seen:
        return {"duplicate": True, "record": None, "state": state}
    positions = state.setdefault("positions", {})
    before = float((positions.get(symbol) or {}).get("weight") or 0.0)
    action = gate.get("action")
    if action == "buy":
        after = float(gate.get("target_weight") or 0.0)
        pos = positions.get(symbol) or {}
        old_entry = float(pos.get("entry_price") or 0.0)
        price = float(decision.get("price_at_decision") or 0.0)
        if before > 0 and old_entry > 0 and after > before:
            entry = (old_entry * before + price * (after - before)) / after
        else:
            entry = old_entry or price
        positions[symbol] = {
            "weight": round(after, 6),
            "industry": gate.get("industry") or "未分类",
            "entry_price": round(entry, 4),
            "last_price": price,
            "updated_at": date,
        }
    elif action == "close":
        after = 0.0
        positions.pop(symbol, None)
    else:
        after = before
        if symbol in positions and decision.get("price_at_decision"):
            positions[symbol]["last_price"] = float(decision["price_at_decision"])
    state["cash_weight"] = round(max(0.0, 1.0 - sum(
        float(p.get("weight") or 0.0) for p in positions.values())), 6)
    state["nav"] = _nav(state)
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = {
        "date": date,
        "ts": decision.get("ts") or datetime.now().timestamp(),
        "symbol": symbol,
        "name": decision.get("name") or "",
        "recommendation": decision.get("recommendation"),
        "confidence": decision.get("confidence"),
        "verdict": gate.get("verdict"),
        "action": action,
        "target_weight": gate.get("target_weight"),
        "weight_before": round(before, 6),
        "weight_after": round(after, 6),
        "price": decision.get("price_at_decision"),
        "industry": gate.get("industry"),
        "risk_level": gate.get("risk_level"),
        "reasons": gate.get("reasons") or [],
        "nav": state["nav"],
        "mode": "paper",
        "trading_permitted": False,
    }
    rec = append_ledger(rec, ledger_path)
    seen.add(key)
    return {"duplicate": False, "record": rec, "state": state}
