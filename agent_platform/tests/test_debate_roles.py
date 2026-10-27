# -*- coding: utf-8 -*-
"""控辩审契约测试（V2 思路 2 并入 / K-ORG-166，2026-10-18 老田拍板）。

契约定义（对齐军团方案总纲「策略是插件、研究/执行物理分离」的立场互补纪律）：
- debate 节点带显式 ``role`` 字段（bull/bear），缺省从 role_profile 后缀
  （``_bull``/``_bear``）推断；
- 辩论对必须立场互补——同角色配对 = TopologyError（配置错误 fail-fast）；
- 双方都无法定性时跳过校验（最小测试团队向后兼容，不溯及既有构造）；
- 终审节点 D1 role=evaluator（审方独立于控辩双方，契约层数据化）。
"""
import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

from interpreter import Team, TopologyError, _infer_role  # noqa: E402

REAL_REGISTRY = PLATFORM / "registry" / "team_invest.json"


def _pair_team(role_a=None, role_b=None, profile_a="t1_bull", profile_b="t2_bear",
               tmp_path=None, name="pair.json"):
    """构造仅含一个辩论对的最小拓扑（无父节点/无下游，聚焦角色校验）。"""
    a = {"agent_id": "TA", "node_key": "na", "role_profile": profile_a,
         "analysis_type": "market_technical", "parent_node_keys": [],
         "level": 1, "is_debate_node": True, "debate_partner_key": "nb",
         "max_debate_rounds": 2}
    b = dict(a, agent_id="TB", node_key="nb", role_profile=profile_b,
             debate_partner_key="na")
    if role_a is not None:
        a["role"] = role_a
    if role_b is not None:
        b["role"] = role_b
    p = tmp_path / name
    p.write_text(json.dumps({"team": "t", "dept": "d", "agents": [a, b]},
                            ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 真仓契约

def test_real_registry_roles_present():
    """真仓 alpha 团队：T1=bull / T2=bear / D1=evaluator 三角色契约齐备。"""
    data = json.loads(REAL_REGISTRY.read_text(encoding="utf-8"))
    roles = {a["agent_id"]: a.get("role") for a in data["agents"]}
    assert roles["T1"] == "bull"
    assert roles["T2"] == "bear"
    assert roles["D1"] == "evaluator"
    # 证据节点 N1/F1 不承担控辩审角色（缺省合法）
    assert roles["N1"] is None and roles["F1"] is None


def test_real_registry_team_validates():
    """真仓拓扑（含新 role 校验）整体可加载——契约不破坏既有执行链。"""
    Team(REAL_REGISTRY)


# ---------------------------------------------------------------- 互补校验

def test_same_explicit_role_pair_rejected(tmp_path):
    """显式 role 同角色辩论对（bull vs bull）→ TopologyError。"""
    with pytest.raises(TopologyError, match="立场相同"):
        Team(_pair_team("bull", "bull", tmp_path=tmp_path))


def test_inferred_same_role_pair_rejected(tmp_path):
    """无显式 role，但两个 role_profile 都推断为 bull → TopologyError。"""
    with pytest.raises(TopologyError, match="立场相同"):
        Team(_pair_team(profile_a="x_bull", profile_b="y_bull", tmp_path=tmp_path))


def test_inferred_opposite_pair_passes(tmp_path):
    """经典形态：t1_bull vs t2_bear 无显式 role，靠推断互补 → 通过。"""
    Team(_pair_team(tmp_path=tmp_path))


def test_uninferrable_pair_skips_check(tmp_path):
    """双方都无法定性（普通 profile 名）→ 跳过校验，向后兼容最小构造。"""
    Team(_pair_team(profile_a="analyst_a", profile_b="analyst_b", tmp_path=tmp_path))


def test_explicit_role_overrides_inference(tmp_path):
    """显式 role 优先于 profile 推断：profile 是 _bull 但显式 bear，
    对手显式 bear → 同角色拒绝（证明 override 生效而非推断兜底）。"""
    with pytest.raises(TopologyError, match="立场相同"):
        Team(_pair_team("bear", "bear", profile_a="t1_bull", tmp_path=tmp_path))


def test_explicit_complementary_overrides(tmp_path):
    """显式角色互补但 profile 同后缀（都是 _bull）→ 显式字段救场，通过。"""
    Team(_pair_team("bull", "bear", profile_a="x_bull", profile_b="y_bull",
                    tmp_path=tmp_path))


# ---------------------------------------------------------------- 推断函数

def test_infer_role_suffix_and_prefix():
    assert _infer_role("t1_bull") == "bull"
    assert _infer_role("t2_bear") == "bear"
    assert _infer_role("bull_specialist") == "bull"
    assert _infer_role("bear_guard") == "bear"


def test_infer_role_uninferrable():
    assert _infer_role("d1_decision") is None
    assert _infer_role("") is None
    assert _infer_role(None) is None
