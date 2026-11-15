# -*- coding: utf-8 -*-
"""鲁肃部门扩编与盯盘/交易分组断言（WO-INV-STAFF-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ORG = REPO / "agent_platform" / "registry" / "org.json"
TT = REPO / "agent_platform" / "registry" / "team_trading.json"
TI = REPO / "agent_platform" / "registry" / "team_invest.json"
WM = REPO / "agent_platform" / "registry" / "watch_methods.json"
MEET = REPO / "agent_platform" / "registry" / "d1_meeting.json"


def _j(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_department_staffing():
    org = _j(ORG)
    invest = next(d for d in org["departments"] if d["dept"] == "invest")
    for aid in ("T1", "T2", "N1", "F1", "D1", "ST1", "WA1", "QS1", "TR1",
                "WA2", "WA3", "TR2", "TR3"):
        assert aid in invest["agents"], aid
    prof = {p["agent_id"]: p for p in invest["agent_profiles"]}
    assert prof["WA2"]["reports_to"] == "WA1"
    assert prof["WA3"]["reports_to"] == "WA1"
    assert prof["TR2"]["reports_to"] == "TR1"
    assert prof["TR3"]["reports_to"] == "TR1"
    assert prof["D1"].get("meeting_chair") is True


def test_meeting_and_watch_methods():
    meet = _j(MEET)
    assert meet["chair"] == "D1"
    assert set(meet["voters"]) == {"T1", "T2", "N1", "F1"}
    assert meet["quorum"] == 5
    wm = _j(WM)
    seats = {m["seat"] for m in wm["methods"]}
    assert {"WA1", "WA2", "WA3"}.issubset(seats)
    assert len(wm["methods"]) >= 3  # 多种盯法，不是单一


def test_trading_group_team():
    """2026-11-16 交易组并入 D1：TR1/2/3 登记在 team_invest.json 的 `roster_extra`。

    原断言读 team_trading.json 的 agents；该文件已按合并口径清空（仅保留壳与说明），
    数据迁到 team_invest 的人册投影里，故数据源改为 TI，断言意图（3 席 + 汇报链）不变。
    """
    team = _j(TI)
    tr = [a for a in team["roster_extra"] if a["agent_id"] in ("TR1", "TR2", "TR3")]
    ids = {a["agent_id"] for a in tr}
    assert ids == {"TR1", "TR2", "TR3"}
    assert next(a for a in tr if a["agent_id"] == "TR1")["reports_to"] == "D1"
    assert all(a["reports_to"] == "TR1" for a in tr if a["agent_id"] in ("TR2", "TR3"))
