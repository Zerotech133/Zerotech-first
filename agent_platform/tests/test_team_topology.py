# -*- coding: utf-8 -*-
"""团队拓扑防回退（2026-10-30 WO-INV-TOPOLOGY-GUARD）。

锁定对齐菜鸟后的结构：
  level1 N1/F1 分析师 → level2 T1/T2 多空辩论 → level3 D1 终审。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "agent_platform" / "engine"))

from interpreter import Team  # noqa: E402

REG = REPO / "agent_platform" / "registry" / "team_invest.json"


def test_topology_levels_and_parents():
    team = Team(REG)
    assert team.by_node["n1_news"]["level"] == 1
    assert team.by_node["f1_fundamental"]["level"] == 1
    assert team.by_node["t1_market"]["level"] == 2
    assert team.by_node["t2_market"]["level"] == 2
    assert team.by_node["d1_decision"]["level"] == 3
    for k in ("t1_market", "t2_market"):
        parents = team.by_node[k]["parent_node_keys"]
        assert "n1_news" in parents and "f1_fundamental" in parents
    d1_parents = team.by_node["d1_decision"]["parent_node_keys"]
    assert set(d1_parents) == {"t1_market", "t2_market", "n1_news", "f1_fundamental"}


def test_debate_pair_still_bull_bear():
    d = json.loads(REG.read_text(encoding="utf-8"))
    nodes = {a["node_key"]: a for a in d["agents"]}
    assert nodes["t1_market"]["is_debate_node"] and nodes["t2_market"]["is_debate_node"]
    assert nodes["t1_market"]["debate_partner_key"] == "t2_market"
    assert nodes["t2_market"]["debate_partner_key"] == "t1_market"
    assert nodes["t1_market"]["max_debate_rounds"] == 2
