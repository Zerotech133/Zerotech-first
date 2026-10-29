# -*- coding: utf-8 -*-
"""C6 paper 指标测试（2026-10-30 WO-INV-PAPER-OBS）。"""
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


c6 = _load("c6_paper_t", REPO / "tools" / "c6_daily_kpi.py")
book = _load("paper_book_c6_t", REPO / "agent_platform" / "paper" / "paper_book.py")


def _snapshot(tmp_path, monkeypatch):
    ledger = tmp_path / "paper_ledger.jsonl"
    state = tmp_path / "state.json"
    kill = tmp_path / "KILL"
    monkeypatch.setattr(c6, "PAPER_LEDGER", str(ledger))
    monkeypatch.setattr(c6, "PAPER_STATE", str(state))
    monkeypatch.setattr(c6, "PAPER_KILL", str(kill))
    return ledger, state, kill


def test_paper_snapshot(tmp_path, monkeypatch):
    ledger, state, _kill = _snapshot(tmp_path, monkeypatch)
    for d in ("2026-10-29", "2026-10-30"):
        book.append_ledger({"date": d, "symbol": "600519", "verdict": "pass",
                            "action": "hold", "nav": 1.0}, str(ledger))
    state.write_text(json.dumps({"nav": 1.0, "cash_weight": 1.0, "positions": {}}),
                     encoding="utf-8")
    snap = c6._paper_snapshot()
    assert snap["ledger_rows"] == 2
    assert snap["last_date"] == "2026-10-30"
    assert snap["hash_chain_ok"] is True
    assert snap["nav"] == 1.0 and snap["positions"] == 0


def test_paper_snapshot_broken_chain_and_kill(tmp_path, monkeypatch):
    ledger, _state, kill = _snapshot(tmp_path, monkeypatch)
    ledger.write_text(json.dumps({"date": "2026-10-30", "prev_hash": "bad", "hash": "x"}) + "\n",
                      encoding="utf-8")
    snap = c6._paper_snapshot()
    assert snap["hash_chain_ok"] is False
    kill.write_text("1", encoding="utf-8")
    assert c6._paper_snapshot()["killed"] is True
