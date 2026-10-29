# -*- coding: utf-8 -*-
"""paper MVP 测试（2026-10-30 WO-INV-PAPER-MVP）。"""
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


risk = _load("paper_risk_t", REPO / "agent_platform" / "paper" / "risk_gate.py")
book = _load("paper_book_t", REPO / "agent_platform" / "paper" / "paper_book.py")
daily = _load("paper_daily_t", REPO / "tools" / "paper_daily.py")
CFG = risk.load_config()


def _dec(**kw):
    d = {"date": "2026-10-30", "symbol": "600519", "name": "贵州茅台", "status": "done", "parse_ok": True,
         "recommendation": "BUY", "confidence": 0.8, "price_at_decision": 100.0,
         "fallback": False, "degraded": False}
    d.update(kw)
    return d


def test_block_invalid():
    g = risk.evaluate(_dec(parse_ok=False), cfg=CFG)
    assert g["verdict"] == "block" and g["reasons"][0]["code"] == "decision_invalid"
    g2 = risk.evaluate(_dec(price_at_decision=None), cfg=CFG)
    assert g2["verdict"] == "block"


def test_buy_pass_and_reduce():
    g = risk.evaluate(_dec(confidence=0.9), cfg=CFG)
    assert g["verdict"] == "pass" and g["target_weight"] == 0.05
    g2 = risk.evaluate(_dec(), cfg=CFG)  # conf .8 -> pass .05
    assert g2["verdict"] == "pass"
    g3 = risk.evaluate(_dec(confidence=0.3), cfg=CFG)  # 减半 -> .025
    assert g3["verdict"] in ("reduce", "pass") and g3["target_weight"] == 0.025


def test_single_and_industry_caps():
    state = {"positions": {"600519": {"weight": 0.08}}}
    g = risk.evaluate(_dec(recommendation="STRONG_BUY", confidence=0.9),
                      state=state, cfg=CFG)
    assert g["verdict"] == "reduce" and g["target_weight"] == 0.02
    state2 = {"positions": {"600519": {"weight": 0.28}}}
    g2 = risk.evaluate(_dec(symbol="000858", confidence=0.9), state=state2, cfg=CFG)
    assert g2["verdict"] in ("reduce", "block")
    assert any(r["code"] in ("industry_cap", "industry_cap_full") for r in g2["reasons"])


def test_drawdown_blocks_buy_but_allows_sell():
    g = risk.evaluate(_dec(), cfg=CFG, day_pnl_pct=-0.05)
    assert g["verdict"] == "block" and g["reasons"][0]["code"] == "drawdown_limit"
    g2 = risk.evaluate(_dec(recommendation="SELL"), cfg=CFG, day_pnl_pct=-0.06)
    assert g2["verdict"] == "pass" and g2["action"] == "close"


def test_book_hash_chain_and_idempotent(tmp_path):
    state_path = tmp_path / "state.json"
    ledger = tmp_path / "ledger.jsonl"
    state = book.empty_state()
    d = _dec(recommendation="STRONG_BUY", confidence=0.9)
    g = risk.evaluate(d, state=state, cfg=CFG)
    r1 = book.apply_decision(d, g, state, "2026-10-30", ledger_path=str(ledger))
    assert r1["duplicate"] is False and r1["record"]["hash"]
    r2 = book.apply_decision(d, g, state, "2026-10-30", ledger_path=str(ledger))
    assert r2["duplicate"] is True
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["prev_hash"] == ""
    assert lines[0]["trading_permitted"] is False


def test_daily_end_to_end(tmp_path):
    shadow = tmp_path / "shadow.jsonl"
    shadow.write_text(json.dumps(_dec(recommendation="BUY", confidence=0.9)), encoding="utf-8")
    res = daily.run("2026-10-30", shadow=str(shadow),
                    state_path=str(tmp_path / "s.json"),
                    ledger_path=str(tmp_path / "l.jsonl"),
                    config=str(REPO / "agent_platform" / "paper" / "paper_config.json"))
    assert res["decisions"] == 1 and res["recorded"] == 1
    assert res["nav"] == 1.0
    # 重复跑不重复记账
    res2 = daily.run("2026-10-30", shadow=str(shadow),
                     state_path=str(tmp_path / "s.json"),
                     ledger_path=str(tmp_path / "l.jsonl"),
                     config=str(REPO / "agent_platform" / "paper" / "paper_config.json"))
    assert res2["recorded"] == 0


def test_run_shadow_has_paper_auto_hook():
    src = (REPO / "agent_platform" / "run_shadow.py").read_text(encoding="utf-8")
    assert "_paper_auto(today)" in src
    assert "PAPER_AUTO" in src and "rehearsal" in src

