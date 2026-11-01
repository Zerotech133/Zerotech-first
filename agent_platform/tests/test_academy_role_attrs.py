# -*- coding: utf-8 -*-
"""研究院角色属性一致性（2026-10-29 A，WO-ACAD-ROLE-ATTR-20261029）。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "registry" / "org.json"
TEAM = ROOT / "registry" / "team_academy.json"
# ORG 投影（org.json departments.academy.formal_agents）：本批未扩 org，保持 7 席；
# TEAM 投影（team_academy.json）：2026-11-01 A0 批准 A 方案新增 STRAT2（市场策略研究员·
# 庞统，E2），研究院 team 正式 8 席。两个真源当前不同步属已知缺口（见 STRAT2FIX 回执）。
FORMAL = ("ACAD1", "STRAT1", "BACK1", "COMM1", "INTEL1", "AI1", "PROD1")
TEAM_FORMAL = FORMAL + ("STRAT2",)
LIAISON = ("GUANYU", "HUANGZHONG")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _academy():
    org = _load(ORG)
    return next(d for d in org["departments"] if d.get("dept") == "academy")


def test_dept_membership_clear():
    dep = _academy()
    assert dep["formal_agents"] == list(FORMAL)
    assert [x["agent_id"] for x in dep["liaison_agents"]] == list(LIAISON)
    assert set(dep["agents"]) == set(FORMAL) | set(LIAISON)
    for x in dep["liaison_agents"]:
        assert x.get("reports_to") == "A0"
        assert x.get("basis")


def test_org_profiles_analysis_and_reporting():
    dep = _academy()
    profs = {p["agent_id"]: p for p in dep["agent_profiles"]}
    for aid in FORMAL + LIAISON:
        p = profs[aid]
        assert p.get("analysis_type"), aid
        assert p.get("reports_to"), aid
    assert profs["ACAD1"]["reports_to"] == "A0"
    assert profs["ACAD1"]["parent_node_keys"] == []
    for aid in FORMAL[1:]:
        assert profs[aid]["reports_to"] == "ACAD1"
        assert profs[aid]["parent_node_keys"] == ["acad1_core"]
    for aid in ("GUANYU", "HUANGZHONG"):
        assert profs[aid]["reports_to"] == "A0"
        assert profs[aid]["parent_node_keys"] == []
        assert profs[aid].get("academy_status") == "liaison"


def test_team_formal_eight_and_reporting():
    """team_academy 正式编制 8 席：7 席 + 2026-11-01 STRAT2（庞统·市场策略研究员）。"""
    team = _load(TEAM)
    agents = {a["agent_id"]: a for a in team["agents"]}
    assert list(agents) == list(TEAM_FORMAL)
    assert agents["ACAD1"]["reports_to"] == "A0"
    assert agents["ACAD1"]["parent_node_keys"] == []
    for aid in TEAM_FORMAL[1:]:
        assert agents[aid]["reports_to"] == "ACAD1"
        assert agents[aid]["parent_node_keys"] == ["acad1_core"]
        assert agents[aid].get("member_kind") == "formal"
        assert agents[aid].get("analysis_type")
    # 防回归：STRAT2 必须是 team 正式席（A0 2026-11-01 批准 A 方案），不是临时/重复命名
    strat2 = agents["STRAT2"]
    assert strat2["member_kind"] == "formal"
    assert strat2["role_profile"] == "strategy_researcher"
    assert strat2["parent_node_keys"] == ["acad1_core"]
    assert strat2.get("grade") == "E2"


def test_four_seats_no_capability_expansion():
    """A 方案只修属性：4 席保留 events 工具；能力扩展（后续批次）允许增量，但不得丢失 events。"""
    dep = _academy()
    profs = {p["agent_id"]: p for p in dep["agent_profiles"]}
    for aid in ("COMM1", "INTEL1", "AI1", "PROD1"):
        tools = profs[aid].get("tools") or []
        assert "events" in tools, "%s 丢失 events 工具: %s" % (aid, tools)
