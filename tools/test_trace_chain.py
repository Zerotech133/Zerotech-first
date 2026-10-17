"""shadow -> paper pending/fill -> performance 的离线追踪链验证。"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant_backtest"))
sys.path.insert(0, str(ROOT / "k_stock" / "paper"))

from ai_operator.shadow import build_fact_pack, make_shadow_decision
from engine import PaperEngine
from market import FakeMarket


def _load_ledger():
    spec = importlib.util.spec_from_file_location(
        "build_performance_ledger", ROOT / "tools" / "build_performance_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bar(day, open_, pre_close, close):
    return {"trade_date": day, "open": open_, "high": max(open_, close),
            "low": min(open_, close), "close": close, "pre_close": pre_close}


def test_trace_fields_survive_shadow_paper_fill_and_ledger(tmp_path):
    pack = build_fact_pack(
        "600519.SH", "1d", "2026-10-08T07:00:00+08:00",
        [{"id": "bar", "source": "fixture", "occurred_at": "2026-10-08T06:59:00+08:00",
          "available_at": "2026-10-08T07:00:00+08:00", "data": {"close": 100}}],
        {"data": "d1", "algorithm": "a1", "knowledge": "k1", "rules": "r1"})
    decision = make_shadow_decision(
        pack, "candidate_buy", ["bar"], "离线链路", "2026-10-08T08:00:00+08:00",
        trace_id="trace:fixture-1", idempotency_key="idem:fixture-1")
    market = FakeMarket({"600519.SH": [
        _bar("20261008", 99, 98, 100), _bar("20261009", 101, 100, 102)]})
    db = tmp_path / "paper.db"
    engine = PaperEngine(db, market, account="pick_bridge")
    planned = engine.plan_bridge_buy(
        "600519.SH", 100, "20261009", "idem:fixture-1",
        trace_id=decision["trace_id"], fact_hash=decision["fact_hash"],
        decision_id=decision["decision_id"], idempotency_key=decision["idempotency_key"])
    assert planned["status"] == "pending"
    order = engine.con.execute("SELECT * FROM orders WHERE id=?", (planned["order_id"],)).fetchone()
    assert order["trace_id"] == "trace:fixture-1"
    assert order["fact_hash"] == pack["fact_hash"]
    assert order["decision_id"] == decision["decision_id"]
    assert order["idempotency_key"] == "idem:fixture-1"
    filled = engine.settle()["filled"][0]
    assert filled["trace_id"] == "trace:fixture-1"
    ledger_row = engine.con.execute("SELECT * FROM ledger WHERE order_id=?", (planned["order_id"],)).fetchone()
    assert ledger_row["decision_id"] == decision["decision_id"]

    pv = tmp_path / "pv.json"
    pv.write_text(json.dumps({"summary": {"asof": "20261008"}, "per_code": {}}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "ledger.json"
    ledger = _load_ledger().build(pv, out, db)
    assert ledger["paper"]["trade_count"] == 1
    trade = ledger["paper"]["per_trade"][0]
    assert trade["trace_id"] == "trace:fixture-1"
    assert trade["fact_hash"] == pack["fact_hash"]
    assert trade["decision_id"] == decision["decision_id"]
    assert trade["idempotency_key"] == "idem:fixture-1"
