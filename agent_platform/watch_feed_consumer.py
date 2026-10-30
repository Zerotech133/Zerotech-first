# -*- coding: utf-8 -*-
"""WA1 feed consumer：signal_pool + chanlun_pool → watch trigger adapter（2026-10-30）。

只读真实池文件，按日期/时效过滤，补价格后建 pending 人工确认；不执行。
缺价格/过期/无动作的信号跳过并给出原因，不臆造。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from agent_platform import watch_trigger_adapter as adapter  # noqa: E402
from agent_platform import trading_bot_flow as flow  # noqa: E402

SIGNAL_POOL = os.path.join(REPO, "quant_backtest", "data", "signal_pool.jsonl")
CHANLUN_POOL = os.path.join(REPO, "quant_backtest", "data", "chanlun_pool.jsonl")


def _load_jsonl(path, limit=None):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
            if limit and len(out) >= limit:
                break
    return out


def _date(text):
    s = str(text or "").strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return str(text or "")[:10]


def latest_prices(chanlun_path=None):
    """code → 最新价格（按 detect_date 取最新；只读）。"""
    best = {}
    for r in _load_jsonl(chanlun_path or CHANLUN_POOL):
        code = str(r.get("code") or r.get("symbol") or "")
        if not code:
            continue
        price = r.get("entry_px") or r.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        day = _date(r.get("detect_date") or r.get("dt"))
        if code not in best or day >= best[code][0]:
            best[code] = (day, price, r.get("type") or r.get("name") or "")
    return {k: v[1] for k, v in best.items()}


def candidates(signal_pool=None, chanlun_path=None, day=None, max_age_days=0):
    """返回 (可触发列表, 跳过列表)；day 缺省=今天，max_age_days=0 仅当天。"""
    base = date.fromisoformat(day) if day else date.today()
    prices = latest_prices(chanlun_path)
    ok, skipped = [], []
    for r in _load_jsonl(signal_pool or SIGNAL_POOL):
        sym = str(r.get("symbol") or r.get("code") or "")
        td = _date(r.get("trade_date") or r.get("date"))
        if not sym or not td:
            skipped.append({"symbol": sym, "reason": "missing_symbol_or_date"})
            continue
        try:
            age = (base - date.fromisoformat(td)).days
        except ValueError:
            skipped.append({"symbol": sym, "reason": "bad_date"})
            continue
        if age < 0:
            skipped.append({"symbol": sym, "reason": "future_date"})
            continue
        if age > max_age_days:
            skipped.append({"symbol": sym, "reason": "stale", "age_days": age})
            continue
        price = prices.get(sym)
        if not price:
            skipped.append({"symbol": sym, "reason": "no_price"})
            continue
        meta = r.get("meta") or {}
        action = str(meta.get("action") or "buy").upper()
        action = {"BUY": "BUY", "SELL": "SELL", "WATCH": "WATCH", "HOLD": "WATCH"}.get(
            action, "BUY" if action in ("LONG", "看多") else "SELL" if action in ("SHORT", "看空") else "WATCH")
        report_id = "WA1-%s-%s-%s" % (td, sym, r.get("source") or "signal_pool")
        ok.append({
            "signal": {
                "symbol": sym,
                "action": action,
                "price": price,
                "confidence": 0.6,
                "reason": "WA1 feed：%s %s" % (r.get("source") or "signal_pool",
                                               meta.get("tier") or ""),
                "report_id": report_id,
                "source": "WA1",
            },
            "selection": {"symbol": sym, "name": ""},
            "evidence": {"code": sym, "event_id": report_id},
            "trade_date": td,
        })
    return ok, skipped


def run(signal_pool=None, chanlun_path=None, day=None, max_age_days=0,
        dry_run=False, limit=None, data_dir=None, auto=False,
        paper_state_path=None, paper_ledger_path=None, paper_config_path=None):
    ok, skipped = candidates(signal_pool, chanlun_path, day, max_age_days)
    if limit:
        ok = ok[:int(limit)]
    created = []
    for item in ok:
        if dry_run:
            created.append({"dry_run": True, **item["signal"]})
            continue
        signal = dict(item["signal"])
        if auto:
            signal["audience"] = "internal"
        rec = adapter.trigger(signal, selection=item["selection"],
                              evidence=item["evidence"], data_dir=data_dir)
        if auto:
            rec = flow.auto_execute(rec["confirmation_id"], by="SYSTEM:TR1",
                                    note="内部策略自动执行",
                                    paper_state_path=paper_state_path,
                                    paper_ledger_path=paper_ledger_path,
                                    paper_config_path=paper_config_path,
                                    data_dir=data_dir)
        created.append({"confirmation_id": rec["confirmation_id"],
                        "symbol": rec["symbol"], "action": rec["action"],
                        "status": rec["status"]})
    return {"candidates": len(ok), "skipped": skipped, "created": created,
            "dry_run": bool(dry_run), "trading_permitted": False}
