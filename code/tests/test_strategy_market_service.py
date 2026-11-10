# -*- coding: utf-8 -*-
"""策略市场应用服务测试（不写 DB、不触发任务）。"""
from __future__ import annotations

from zhice_os_v2.application.services.strategy_market_service import load_research_items


def test_load_research_items_from_project_config():
    items = load_research_items()
    assert len(items) >= 32
    assert all(x.get("market") == "CN" for x in items)
    assert all(x.get("tradable") is False for x in items)
    statuses = {x["status"] for x in items}
    assert "RESEARCH_CLOSED" in statuses
    assert "RESEARCH_NEEDS_MORE" in statuses


def test_load_research_items_missing_file_returns_empty(tmp_path):
    assert load_research_items(tmp_path / "missing.json") == []
