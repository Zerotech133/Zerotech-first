# -*- coding: utf-8 -*-
"""on_demand 证据类型单测（K-ORG-20261026-AGENTHEALTH2）。

按需触发、无固定日产的事项显式标"不适用"，而不是永久"待接线"。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import agent_daily as ad  # noqa: E402


def test_on_demand_returns_na():
    item = {"id": "X-01", "agent": "DEV2", "freq": "every_min 10",
            "evidence": {"type": "on_demand", "note": "按需触发"}}
    row = ad.evaluate_item(item, datetime.now())
    assert row["status"] == ad.NA, row
    assert "按需" in row["evidence"]


def test_catalog_on_demand_items():
    catalog = json.loads(
        (REPO / "agent_platform" / "registry" / "daily_work.json").read_text(
            encoding="utf-8"))
    items = catalog if isinstance(catalog, list) else catalog.get("items") or []
    by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
    for key in ("DEV2-01", "ARCH1-01"):
        assert (by_id[key].get("evidence") or {}).get("type") == "on_demand", key