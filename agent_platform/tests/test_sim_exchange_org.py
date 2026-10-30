# -*- coding: utf-8 -*-
"""TR1 调入 D1 / 模拟交易所主管的 org 断言（WO-SIM-EXCHANGE-ORG-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ORG = REPO / "agent_platform" / "registry" / "org.json"
TT = REPO / "agent_platform" / "registry" / "team_trading.json"
SE = REPO / "agent_platform" / "registry" / "sim_exchange.json"


def _j(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_tr1_moved_to_invest():
    org = _j(ORG)
    deps = {d["dept"]: d for d in org["departments"]}
    assert "TR1" in deps["invest"]["agents"]
    assert "TR1" not in deps["trading"]["agents"]
    assert deps["trading"]["status"] == "merged_into_invest"
    tr1 = [p for p in deps["invest"]["agent_profiles"] if p["agent_id"] == "TR1"][0]
    assert tr1["reports_to"] == "D1"
    assert "模拟交易所主管" in tr1["manager_title"]


def test_team_trading_reports_to_d1():
    team = _j(TT)
    assert team["dept"] == "invest"
    assert team["agents"][0]["agent_id"] == "TR1"
    assert team["agents"][0]["reports_to"] == "D1"


def test_sim_exchange_approved():
    se = _j(SE)
    assert se["owner"] == "D1"
    assert se["manager"] == "TR1"
    assert se["manager_status"] == "approved"
