# -*- coding: utf-8 -*-
"""门户人工确认入口测试（WO-BOT-CONFIRM）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"


def test_page_assets_and_api_hooks():
    html = (PORTAL / "sim_confirm.html").read_text(encoding="utf-8")
    assert "/api/sim/confirmations" in html
    assert "/api/sim/confirm" in html
    assert "/static/tokens.css" in html and "/static/nav.js" in html and "/static/shortscreen.js" in html
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert '"/api/sim/confirm"' in api
    assert '"/api/sim/confirmations"' in api
    assert '"/sim/confirm"' in api


def test_flow_module_exposes_human_gate():
    src = (REPO / "agent_platform" / "trading_bot_flow.py").read_text(encoding="utf-8")
    assert "human_gate" in src and "trading_permitted" in src
    assert "PAPER_KILL" not in src  # kill 由 paper_book 统一判定
