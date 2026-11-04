# -*- coding: utf-8 -*-
"""司马徽研究院 8 席入职台账校验。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import validate_academy_team as V  # noqa: E402


def test_academy_eight_members_onboarded():
    r = V.validate()
    assert r["ok"] is True, r["errors"]
    assert r["members"] == 8 and r["team_ids"] == 8
    led = V._load(V.LEDGER)
    names = {x["agent_id"]: x["name"] for x in led["members"]}
    assert names["ACAD1"] == "司马徽"
    assert names["STRAT1"] == "徐庶"
    assert names["INTEL1"] == "郭嘉"
    assert names["STRAT2"] == "庞统·策略"
    strat2 = next(x for x in led["members"] if x["agent_id"] == "STRAT2")
    assert strat2["org_profile"] is False
    ai1 = next(x for x in led["members"] if x["agent_id"] == "AI1")
    assert ai1["command_reports_to"] == "A0"
