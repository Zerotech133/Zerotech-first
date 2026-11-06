# -*- coding: utf-8 -*-
"""paper 平仓路径与每日 mark 去重回归（2026-11-06）。"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


book = _load("paper_book_close_t", REPO / "agent_platform" / "paper" / "paper_book.py")
CFG = json.loads((REPO / "agent_platform" / "paper" / "paper_config.json").read_text(encoding="utf-8"))

SYMBOL = "sz002463"


def _open_state():
    return {
        "positions": {
            SYMBOL: {
                "weight": 0.05,
                "industry": "未分类",
                "entry_price": 82.0,
                "last_price": 73.21,
                "updated_at": "2026-11-06",
            }
        },
        "cash_weight": 0.95,
        "nav": 0.99464,
        "updated_at": "2026-11-06 16:42:35",
    }


def _close_decision(price=73.21):
    return {
        "date": "2026-11-06",
        "ts": 1770000000,
        "symbol": SYMBOL,
        "name": "沪电股份",
        "recommendation": "SELL",
        "confidence": 1.0,
        "price_at_decision": price,
        "reason": "stop_loss",
    }


def _close_gate():
    return {
        "verdict": "pass",
        "action": "close",
        "target_weight": 0.0,
        "risk_level": "MEDIUM",
        "industry": "未分类",
        "reasons": [{"code": "stop_loss", "detail": "last_price <= 78.0"}],
    }


def test_stop_loss_config_has_symbol_stop():
    stop = (CFG.get("stop_loss") or {}).get("positions", {}).get(SYMBOL)
    assert stop and stop.get("stop_price") == 78.0
    assert CFG["stop_loss"]["default_pct"] == 0.05


def test_close_preserves_realized_nav_and_cash(tmp_path):
    state = _open_state()
    ledger = tmp_path / "ledger.jsonl"
    res = book.apply_decision(
        _close_decision(), _close_gate(), state, "2026-11-06",
        ledger_path=str(ledger),
    )
    assert res["duplicate"] is False
    assert state["positions"] == {}
    assert state["cash_weight"] == 0.99464
    assert state["nav"] == 0.99464
    assert state["last_close"]["realized_pnl_pct"] == -10.7195
    assert state["last_close"]["realized_pnl_nav"] == -0.00536
    rec = res["record"]
    assert rec["action"] == "close"
    assert rec["weight_before"] == 0.05
    assert rec["weight_after"] == 0.0
    assert rec["realized_pnl_pct"] == -10.7195
    assert rec["mode"] == "paper"
    assert rec["trading_permitted"] is False
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["prev_hash"] == ""


def test_close_twice_is_idempotent(tmp_path):
    state = _open_state()
    ledger = tmp_path / "ledger.jsonl"
    first = book.apply_decision(_close_decision(), _close_gate(), state, "2026-11-06",
                                ledger_path=str(ledger))
    assert first["duplicate"] is False
    nav = state["nav"]
    second = book.apply_decision(_close_decision(), _close_gate(), state, "2026-11-06",
                                 ledger_path=str(ledger))
    assert second["duplicate"] is True
    assert state["nav"] == nav
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_daily_mark_flat_does_not_double_count(tmp_path):
    state = _open_state()
    book.apply_decision(_close_decision(), _close_gate(), state, "2026-11-06",
                        ledger_path=str(tmp_path / "ledger.jsonl"))
    nav_after_close = state["nav"]
    res = book.mark_to_market(state, {SYMBOL: 70.0}, date="2026-11-06")
    assert res["flat"] is True
    assert res["marked"] == 0
    assert state["nav"] == nav_after_close == 0.99464
    assert state["cash_weight"] == 0.99464


def test_close_after_same_day_buy_is_allowed(tmp_path):
    state = book.empty_state()
    ledger = str(tmp_path / "ledger.jsonl")
    buy = _close_decision(price=82.0)
    buy["recommendation"] = "BUY"
    buy["confidence"] = 0.6
    buy_gate = _close_gate()
    buy_gate.update({"action": "buy", "target_weight": 0.05, "verdict": "pass"})
    first = book.apply_decision(buy, buy_gate, state, "2026-11-06", ledger_path=ledger)
    assert first["duplicate"] is False
    assert state["positions"][SYMBOL]["weight"] == 0.05
    closed = book.apply_decision(_close_decision(), _close_gate(), state, "2026-11-06",
                                 ledger_path=ledger)
    assert closed["duplicate"] is False
    assert state["positions"] == {}
    assert state["last_close"]["realized_pnl_pct"] == -10.7195
    assert len(Path(ledger).read_text(encoding="utf-8").splitlines()) == 2


def test_daily_mark_open_position_recomputes_once():
    state = _open_state()
    res = book.mark_to_market(state, {SYMBOL: 70.0}, date="2026-11-06")
    assert res["flat"] is False
    assert res["marked"] == 1
    assert state["positions"][SYMBOL]["last_price"] == 70.0
    assert state["nav"] == round(0.95 + 0.05 * (70.0 / 82.0), 6)
