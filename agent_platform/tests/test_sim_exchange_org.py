# -*- coding: utf-8 -*-
"""TR1 调入 D1 / 模拟交易所主管的 org 断言（WO-SIM-EXCHANGE-ORG-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ORG = REPO / "agent_platform" / "registry" / "org.json"
TT = REPO / "agent_platform" / "registry" / "team_trading.json"
TI = REPO / "agent_platform" / "registry" / "team_invest.json"
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
    """2026-11-16 交易组并入 D1：TR1/2/3 在 team_invest.json 的 `roster_extra`。

    team_trading.json 已按合并口径清空（保留壳），数据源改 TI；断言意图不变。
    """
    team = _j(TI)
    assert team["dept"] == "invest"
    tr1 = next(a for a in team["roster_extra"] if a["agent_id"] == "TR1")
    assert tr1["reports_to"] == "D1"
    assert {a["agent_id"] for a in team["roster_extra"] if a["agent_id"].startswith("TR")} == {
        "TR1", "TR2", "TR3"}


def test_sim_exchange_approved():
    se = _j(SE)
    assert se["owner"] == "D1"
    assert se["manager"] == "TR1"
    assert se["manager_status"] == "approved"
