# -*- coding: utf-8 -*-
"""paper 页面测试（2026-10-30 WO-INV-PAPER-UI）。"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PORTAL))


def _load_data():
    spec = importlib.util.spec_from_file_location("paper_data_t", PORTAL / "paper_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_book():
    spec = importlib.util.spec_from_file_location(
        "paper_book_ui_t", REPO / "agent_platform" / "paper" / "paper_book.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_empty(tmp_path):
    m = _load_data()
    snap = m.paper_snapshot(ledger=str(tmp_path / "none.jsonl"),
                            state=str(tmp_path / "none.json"),
                            config=str(REPO / "agent_platform" / "paper" / "paper_config.json"),
                            kill=str(tmp_path / "KILL"))
    assert snap["ok"] is True and snap["mode"] == "paper"
    assert snap["trading_permitted"] is False
    assert snap["ledger_rows"] == 0 and snap["hash_chain_ok"] is True
    assert snap["limits"]["single_name_max"] == 0.10
    assert snap["limits"]["industry_max"] == 0.30
    assert snap["limits"]["daily_drawdown_max"] == 0.05


def test_snapshot_with_ledger(tmp_path):
    m = _load_data()
    book = _load_book()
    ledger = tmp_path / "ledger.jsonl"
    book.append_ledger({"date": "2026-10-30", "symbol": "600519", "name": "贵州茅台",
                        "recommendation": "BUY", "verdict": "reduce", "action": "buy",
                        "weight_before": 0, "weight_after": 0.05, "price": 1354.0,
                        "nav": 1.0, "hash": "x"}, str(ledger))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"nav": 1.0, "cash_weight": 0.95,
                                 "positions": {"600519": {"weight": 0.05,
                                                          "industry": "白酒"}}}),
                     encoding="utf-8")
    snap = m.paper_snapshot(ledger=str(ledger), state=str(state),
                            config=str(REPO / "agent_platform" / "paper" / "paper_config.json"),
                            kill=str(tmp_path / "KILL"))
    assert snap["ledger_rows"] == 1 and snap["positions_count"] == 1
    assert snap["reduced"] == 1
    assert snap["recent"][0]["symbol"] == "600519"


def test_page_assets_and_routes():
    html = (PORTAL / "quant_paper.html").read_text(encoding="utf-8")
    assert "/static/tokens.css" in html
    assert "/static/nav.js" in html
    assert "/static/shortscreen.js" in html
    assert "/api/quant/paper" in html
    assert "不接实盘" in html
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert 'path == "/api/quant/paper"' in api
    assert 'path == "/quant/paper"' in api
