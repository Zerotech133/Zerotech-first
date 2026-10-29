# -*- coding: utf-8 -*-
"""真仓 roster → D1 JSON 契约锚点测试（f59a0738 集成面收口）。

背景：f59a0738 把 D1 决策节点的输出契约收口为 JSON-only，条件键是
`node.get("analysis_type") == "final_decision"`。该锚点散落三处：
  1. registry/team_invest.json —— run_shadow 真源（Team 加载的就是它）；
  2. hr/D1/appointment.yaml —— 人设件（appointment 册封语义）；
  3. engine/interpreter.py:189 —— 契约分支。
test_d1_json_contract（f59a0738 自带）只测了 interpreter 单元面（手工构造
node dict）；本文件补**集成面**：真仓 roster 实际加载后的契约命中。若未来
有人改 team_invest.json 的 analysis_type 或新增 final_decision 节点，这里
立刻红，而不是等到周一 17:10 首跑 parse 大面积失败才发现。

零 LLM：只做 roster 加载 + _build_user 纯函数断言，秒级。
"""

import json
import re
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

from interpreter import Team, Interpreter  # noqa: E402

REGISTRY = PLATFORM / "registry" / "team_invest.json"
APPOINTMENT = PLATFORM / "hr" / "D1" / "appointment.yaml"
CTX = {"stock": "600519", "data": {"final_decision": "上游攻防结论摘要"}}


def _final_nodes():
    team = Team(REGISTRY)  # 拓扑校验失败（重复 key/层级倒挂）也在此暴露
    return team, [a for a in team.agents if a.get("analysis_type") == "final_decision"]


def test_registry_loads_and_topology_valid():
    team, _ = _final_nodes()
    assert team.by_agent.get("D1"), "真仓 roster 必须有 D1 决策节点"


def test_d1_analysis_type_is_final_decision():
    """f59a0738 契约分支的唯一锚点值——漂移即 JSON 尾句失效。"""
    _, finals = _final_nodes()
    d1 = [a for a in finals if a["agent_id"] == "D1"]
    assert len(d1) == 1, "D1 必须恰好一个且 analysis_type=final_decision"


def test_final_decision_nodes_are_unique_per_agent():
    """final_decision 只允许出现在决策位；多节点误标会全部被收进 JSON-only
    契约（上游分析节点被要求直出 JSON，输出质量坍塌）。"""
    team, finals = _final_nodes()
    ids = sorted(a["agent_id"] for a in finals)
    assert ids == ["D1"], "final_decision 节点当前应仅 D1，实际=%s" % ids


def test_real_roster_d1_user_prompt_json_only():
    team = Team(REGISTRY)
    u = Interpreter._build_user(None, team.by_agent["D1"], CTX)
    assert "严格只输出一个 JSON 对象" in u
    assert "3 句以内" not in u


def test_real_roster_non_decision_uses_structured_schema():
    """菜鸟结构化输出移植：非决策节点追加 analysis_type JSON Schema，而非旧 3 句尾。"""
    team = Team(REGISTRY)
    t1 = team.by_node.get("t1_market") or team.by_agent.get("T1")
    assert t1 and t1["analysis_type"] != "final_decision"
    u = Interpreter._build_user(None, t1, CTX)
    assert "输出格式要求" in u and "```json" in u
    assert "3 句以内" not in u


def test_appointment_yaml_stays_aligned():
    """hr 册封件与 registry 真源对齐（文本级扫描，不引 PyYAML 依赖）。
    两处漂移 = 册封语义与运行时契约分叉。"""
    assert APPOINTMENT.exists(), "hr/D1/appointment.yaml 缺失"
    text = APPOINTMENT.read_text(encoding="utf-8")
    assert re.search(r'"analysis_type"\s*:\s*"final_decision"', text)
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    d1 = next(a for a in reg["agents"] if a["agent_id"] == "D1")
    assert d1["analysis_type"] == "final_decision"
