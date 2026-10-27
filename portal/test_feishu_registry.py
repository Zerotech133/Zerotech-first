# -*- coding: utf-8 -*-
"""feishu_registry 单测（W1 底座）：合法/非法注册表、路由匹配、白名单、通道铁律。"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_registry as fr  # noqa: E402


@pytest.fixture(scope="module")
def real_registry():
    return fr._read_json(fr.DEFAULT_REGISTRY)


@pytest.fixture(scope="module")
def real_routes():
    return fr._read_json(fr.DEFAULT_ROUTES)


def test_real_registry_valid():
    roles, warnings = fr.validate_registry(fr._read_json(fr.DEFAULT_REGISTRY))
    assert "zb.owner.exec" in roles
    assert "zb.ops.notify" in roles
    assert roles["zb.owner.exec"]["group_policy"]["mode"] == "p2p_only"
    assert not warnings, warnings


def test_naming_rule_display_prefix(real_registry):
    data = copy.deepcopy(real_registry)
    data["roles"][0]["display"] = "飞书狗"
    _, warnings = fr.validate_registry(data)
    assert any("零界·" in w for w in warnings)


def test_duplicate_role_rejected(real_registry):
    data = copy.deepcopy(real_registry)
    data["roles"].append(copy.deepcopy(data["roles"][0]))
    with pytest.raises(fr.RegistryError):
        fr.validate_registry(data)


def test_channel_policy_locked(real_registry):
    for key, bad in (
        ("outbound", "any"),
        ("inbound", "any"),
        ("direct_openapi", "allowed"),
    ):
        data = copy.deepcopy(real_registry)
        data["channel_policy"][key] = bad
        with pytest.raises(fr.RegistryError):
            fr.validate_registry(data)


def test_bad_domain_rejected(real_registry):
    data = copy.deepcopy(real_registry)
    data["roles"][0]["domain"] = ["customerx"]
    with pytest.raises(fr.RegistryError):
        fr.validate_registry(data)


def test_group_whitelist_has_two_chats(real_registry):
    groups = real_registry["group_whitelist"]
    names = [g["name"] for g in groups]
    assert "老田的AI量化" in names and "内部测试" in names
    for g in groups:
        assert g["chat_id"].startswith("oc_")


def test_routes_valid_and_reference_registered_roles(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    routes = fr.validate_routes(real_routes, roles_index=roles)
    assert len(routes) >= 5


def test_route_unknown_role_rejected(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    data = copy.deepcopy(real_routes)
    data["routes"][0]["match"] = {"role": "zb.no.such"}
    with pytest.raises(fr.RegistryError):
        fr.validate_routes(data, roles_index=roles)


def test_match_exec_task_p2p(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    routes = fr.validate_routes(real_routes, roles_index=roles)
    assert (
        fr.match_route(routes, role="zb.owner.exec", intent="task", chat_type="p2p")
        == "dsh:exec"
    )
    assert (
        fr.match_route(routes, role="zb.owner.exec", intent="audio", chat_type="p2p")
        == "dsh:voice"
    )


def test_match_exec_group_falls_to_fallback(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    routes = fr.validate_routes(real_routes, roles_index=roles)
    # 执行域只允许 p2p；群聊任务不得命中 dsh:exec
    assert (
        fr.match_route(routes, role="zb.owner.exec", intent="task", chat_type="group")
        != "dsh:exec"
    )


def test_match_feed_and_confirm_global(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    routes = fr.validate_routes(real_routes, roles_index=roles)
    assert fr.match_route(routes, intent="feed") == "w43:feed"
    assert fr.match_route(routes, intent="confirm") == "decision:confirm"


def test_match_ticker_vs_qa(real_registry, real_routes):
    roles, _ = fr.validate_registry(real_registry)
    routes = fr.validate_routes(real_routes, roles_index=roles)
    assert (
        fr.match_route(routes, role="zb.quant.ticker", intent="command")
        == "quant:ticker_cmd"
    )
    assert fr.match_route(routes, role="zb.quant.qa", intent="question") == "qa:answer"


def test_cli_check_exit_zero():
    import subprocess

    py = sys.executable
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "feishu_registry.py"
    )
    r = subprocess.run([py, script, "--check"], capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "zb.owner.exec" in r.stdout.decode("utf-8", "replace")
