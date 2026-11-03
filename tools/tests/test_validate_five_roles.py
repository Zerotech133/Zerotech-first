# -*- coding: utf-8 -*-
"""五角色入职台账 ↔ org/profile_seats ↔ 17席校验。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import validate_five_roles as V  # noqa: E402


def test_five_roles_onboarded_to_actual_positions():
    r = V.validate()
    assert r["ok"] is True, r["errors"]
    led = V._load(V.LEDGER)
    pos = {x["agent_id"]: x["actual_position"] for x in led["roles"]}
    assert pos["A0"] == "幕府参谋长"
    assert pos["AI1"] == "军师（反向军师/方法论）"
    assert pos["ZHAOYUN"] == "治理官"
    assert pos["GUANYU"] == "整治官（代码审计/技术债）"
    assert pos["HUANGZHONG"] == "验收官"
    org = V._load(V.ORG)
    assert set(org["command_hq"]["members"]) == set(pos)
