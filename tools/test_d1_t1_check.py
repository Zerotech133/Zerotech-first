# -*- coding: utf-8 -*-
"""P0-05 T+1 检查工具测试（2026-11-16）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "d1_t1_check_t", REPO / "tools" / "d1_t1_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tc = _load()


def test_ledger_stats_counts_trade_ids(tmp_path, monkeypatch):
    ledger = tmp_path / "paper_ledger.jsonl"
    rows = [
        {"date": "2026-11-13", "symbol": "A", "trade_id": None},
        {"date": "2026-11-16", "symbol": "B", "trade_id": "pt-20261116010101-aaaaaa"},
        {"date": "2026-11-16", "symbol": "C", "trade_id": "pt-20261116010102-bbbbbb"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(tc, "LEDGER", ledger)
    st = tc.ledger_stats()
    assert st["rows"] == 3
    assert st["rows_with_trade_id"] == 2
    assert st["last_trade_ids"][-1].startswith("pt-")


def test_ledger_stats_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "LEDGER", tmp_path / "none.jsonl")
    st = tc.ledger_stats()
    assert st["rows"] == 0 and st["rows_with_trade_id"] == 0


def test_pending_internal_returns_list():
    rows = tc.pending_internal()
    assert isinstance(rows, list)
    for r in rows:
        assert str(r.get("audience")) == "internal"


def test_main_dry_run_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "LEDGER", tmp_path / "none.jsonl")
    monkeypatch.setattr(tc, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(tc, "LOG", tmp_path / "log.txt")
    rc = tc.main(["--dry-run"])
    assert rc == 0
    files = list((tmp_path / "out").rglob("P0-05_t1_check_*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["dry_run"] is True
    assert doc["p0_05_trade_id_condition"] in ("PASS", "NO_FILL_YET")
    assert doc["paper_only"] is True and doc["not_a_trade"] is True
