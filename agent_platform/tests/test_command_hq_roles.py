# -*- coding: utf-8 -*-
"""五角色适配器测试（2026-11-01 K-ORG-20261101-HQROLE；专项计划 §3.1/§4 W-C1）。

覆盖：五角色身份（复用 org/registry，不新造）/声明与生效模型绑定（engine/router）/
prompt 绑定/五角色同题各出合法意见/契约强制/只读边界（无执行、无网络）/tmp 身份注入。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[2]
if str(WT) not in sys.path:
    sys.path.insert(0, str(WT))

from agent_platform.command_hq import role_contract as rc  # noqa: E402
from agent_platform.command_hq import roles  # noqa: E402

EXPECTED_SEATS = {
    "TIANFENG": "A0",
    "ZHUGE": "AI1",
    "ZHAOYUN": "ZHAOYUN",
    "HUANGZHONG": "HUANGZHONG",
    "GUANYU": "GUANYU",
}
EXPECTED_CN = {
    "TIANFENG": "田丰",
    "ZHUGE": "诸葛亮",
    "ZHAOYUN": "赵云",
    "HUANGZHONG": "黄忠",
    "GUANYU": "关羽",
}
EVIDENCE = [{"kind": "file",
             "ref": "docs/plans/2026-11-01_指挥部五角色智能体与验收闭环研发计划_v1.md",
             "verified": True}]


def test_identity_five_roles_reuse_registry():
    identity = roles.load_identity()
    assert list(identity["roles"]) == list(roles.ROLE_ORDER)
    assert identity["issues"] == [], identity["issues"]
    assert set(identity["command_hq"]) == set(EXPECTED_SEATS.values())
    for role, seat in EXPECTED_SEATS.items():
        info = identity["roles"][role]
        assert info["seat_id"] == seat
        assert info["cn_name"] == EXPECTED_CN[role]
        assert info["in_command_hq"] is True
        assert info["llm_binding"]


def test_identity_sources_org_and_profile_seats():
    identity = roles.load_identity()
    assert identity["roles"]["TIANFENG"]["source"] == "org.json"
    assert identity["roles"]["ZHUGE"]["source"] == "org.json"
    assert identity["roles"]["ZHAOYUN"]["source"] == "profile_seats.json"
    assert identity["roles"]["ZHAOYUN"]["llm_binding"] == "deepseek/deepseek-v4-pro"
    assert identity["roles"]["HUANGZHONG"]["llm_binding"] == "glm/glm-5.3"
    assert identity["roles"]["GUANYU"]["llm_binding"] == "deepseek/deepseek-flash"


def test_router_effective_binding():
    overrides, error = roles.load_router_bindings()
    assert error == ""
    assert overrides.get("AI1") == "openai/gpt-6.0"
    bindings = {b["role"]: b for b in roles.all_role_bindings()}
    zhuge = bindings["ZHUGE"]
    assert zhuge["model"]["declared"] == "openai/gpt-6.0"
    assert zhuge["model"]["effective"] == "openai/gpt-6.0"
    assert zhuge["model"]["source"] == "engine/router.EXOTIC_ASSIGNMENTS"
    for binding in bindings.values():
        assert binding["model"]["declared"]
        assert binding["model"]["effective"]
        assert binding["read_only"] is True and binding["executes"] is False


def test_tianfeng_prompt_binding_is_registered():
    binding = roles.role_binding("TIANFENG")
    prompt = binding["prompt"]
    assert prompt["ref"] == "agent_platform/prompts/a0_chief.md"
    assert prompt["exists"] is True
    assert prompt["meta"]["registry_key"] == "a0_chief"


def test_five_adapters_produce_valid_opinions_same_topic():
    adapters = roles.adapters()
    assert tuple(adapters) == roles.ADAPTER_KEYS
    seen_roles = []
    for key, adapter in adapters.items():
        opinion = adapter.draft_opinion(
            "HQ-20261101-001", topic="M4 会诊：是否进入 Go-No-Go",
            position="abstain", recommendation="%s：待补充证据" % adapter.binding["cn_name"],
            evidence=EVIDENCE)
        assert rc.validate_opinion(opinion) == [], rc.validate_opinion(opinion)
        assert opinion["role"] == adapter.role
        assert opinion["topic_id"] == "HQ-20261101-001"
        assert opinion["evidence"][0]["kind"] == "file"
        seen_roles.append(opinion["role"])
        assert adapter.read_only is True and adapter.executes is False
    assert sorted(seen_roles) == sorted(roles.ROLE_ORDER)


def test_adapters_enforce_contract():
    zhuge = roles.RoleAdapter("ZHUGE")
    with pytest.raises(rc.RoleOpinionError):
        zhuge.draft_opinion("HQ-X", recommendation="x",
                            evidence=[{"kind": "file", "ref": "", "verified": True}])
    with pytest.raises(rc.RoleOpinionError):
        zhuge.draft_opinion("HQ-X", recommendation="x", position="veto",
                            evidence=EVIDENCE)
    with pytest.raises(rc.RoleOpinionError):
        zhuge.draft_opinion("HQ-X", recommendation="x", position="conditional",
                            evidence=EVIDENCE)
    with pytest.raises(ValueError):
        roles.RoleAdapter("LAOTIAN")


def test_draft_with_conditions_and_escalation():
    adapter = roles.RoleAdapter("HUANGZHONG")
    opinion = adapter.draft_opinion(
        "HQ-20261101-002", position="conditional",
        recommendation="附条件放行",
        conditions=["四签齐 + 证据齐 + 回滚演练通过"],
        evidence=EVIDENCE, risks=["P1：抽样面有限"],
        escalate=True, escalate_reason="涉及 M4 正式验收，需老田确认")
    assert rc.validate_opinion(opinion) == []
    assert opinion["conditions"] and opinion["escalate"] is True


def test_compose_prompt_contains_contract_and_boundary():
    text = roles.compose_prompt(
        "ZHAOYUN", "HQ-20261101-001", "M4 会诊",
        context="M4 四签 0/4", constraints=["只读", "不代签"])
    assert roles.rc.SCHEMA in text
    assert "RoleOpinion v1" in text
    assert "赵云" in text and "ZHAOYUN" in text
    assert "无证据不得 verified=true" in text
    assert "HQ-20261101-001" in text
    assert "不执行" in text
    assert "只读" in text


def test_read_only_boundary_no_execution_or_network():
    assert roles.READ_ONLY is True
    assert roles.EXECUTES is False
    source = Path(roles.__file__).read_text(encoding="utf-8")
    contract_source = Path(rc.__file__).read_text(encoding="utf-8")
    forbidden = re.compile(
        r"(?m)^\s*(?:import|from)\s+(subprocess|requests|urllib|socket|http|openai)\b")
    for text in (source, contract_source):
        assert not forbidden.search(text), "只读契约包不得引入执行/网络依赖"
    assert "subprocess" not in source.split("importlib", 1)[0]


def test_tmp_registry_identity_injection(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    org = {
        "command_hq": {"members": ["A0", "ZHAOYUN", "AI1", "GUANYU", "HUANGZHONG"]},
        "departments": [{
            "agent_profiles": [
                {"agent_id": "A0", "llm_binding": "glm/glm-test-1"},
                {"agent_id": "AI1", "cn_name": "诸葛亮", "llm_binding": "openai/gpt-test"},
                {"agent_id": "GUANYU", "cn_name": "关羽", "llm_binding": "deepseek/flash-test"},
                {"agent_id": "HUANGZHONG", "cn_name": "黄忠", "llm_binding": "glm/glm-test-2"},
            ]}],
    }
    seats = {"schema": "profile_seats/v1", "seats": [
        {"agent_id": "ZHAOYUN", "cn_name": "赵云", "llm_binding": "deepseek/pro-test"}]}
    (registry / "org.json").write_text(json.dumps(org, ensure_ascii=False),
                                       encoding="utf-8")
    (registry / "profile_seats.json").write_text(
        json.dumps(seats, ensure_ascii=False), encoding="utf-8")
    identity = roles.load_identity(registry_dir=registry)
    assert identity["issues"] == []
    assert identity["roles"]["TIANFENG"]["llm_binding"] == "glm/glm-test-1"
    assert identity["roles"]["ZHUGE"]["llm_binding"] == "openai/gpt-test"
    assert identity["roles"]["ZHAOYUN"]["source"] == "profile_seats.json"
    assert identity["roles"]["ZHAOYUN"]["llm_binding"] == "deepseek/pro-test"
    assert identity["roles"]["HUANGZHONG"]["cn_name"] == "黄忠"

    empty = tmp_path / "empty_registry"
    empty.mkdir()
    fallback = roles.load_identity(registry_dir=empty)
    assert len(fallback["issues"]) >= 5
    for role in roles.ROLE_ORDER:
        assert fallback["roles"][role]["llm_binding"] == roles.FALLBACK_BINDINGS[role]
