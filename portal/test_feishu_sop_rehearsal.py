# -*- coding: utf-8 -*-
"""新增机器人 7 步 SOP 离线演练（W4 预演，不创建真实飞书 app）。

覆盖 SOP 中不需要开放平台的部分：
  1) registry 追加角色（命名规则/域/群策略约束）
  2) routes 追加一行（不引用未注册角色）
  3) canonical/凭据装载接口可复用（临时 secret 文件）
  4) 机器人公共壳可构造、群策略生效
  5) 管理台数据模型能容纳新角色（registry 校验后即可见）
"""

import json
import os
import tempfile
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_bot_base as base  # noqa: E402
import feishu_registry as fr  # noqa: E402


def _paths(tmp):
    reg = os.path.join(tmp, "registry.json")
    routes = os.path.join(tmp, "routes.json")
    return reg, routes


def _load():
    reg = json.load(open(fr.DEFAULT_REGISTRY, encoding="utf-8"))
    routes = json.load(open(fr.DEFAULT_ROUTES, encoding="utf-8"))
    return reg, routes


def test_sop_step1_add_role_valid():
    reg, _ = _load()
    reg["roles"].append(
        {
            "role": "zb.demo.echo",
            "display": "零界·演练",
            "domain": ["owner"],
            "app_ref": "ZB_DEMO_ECHO",
            "app_id_current": "",
            "events": ["im.message.receive_v1"],
            "perms": ["im:message", "im:message:send_as_bot"],
            "group_policy": {"mode": "p2p_only", "allow_chats": []},
            "inputs": "text",
            "outputs": "text",
            "owner": "老田",
            "status": "planned",
        }
    )
    roles, warnings = fr.validate_registry(reg)
    assert "zb.demo.echo" in roles
    assert not warnings


def test_sop_step1_rejects_bad_naming():
    reg, _ = fr.load_registry()  # 返回 index 不是原始结构，改用原始
    raw = json.load(open(fr.DEFAULT_REGISTRY, encoding="utf-8"))
    raw["roles"].append(
        {
            "role": "dingzhi",
            "display": "邓芝",
            "domain": ["owner"],
            "events": [],
            "perms": [],
            "group_policy": {},
            "status": "planned",
        }
    )
    with pytest.raises(fr.RegistryError):
        fr.validate_registry(raw)


def test_sop_step2_route_for_new_role():
    reg, routes = _load()
    reg["roles"].append(
        {
            "role": "zb.demo.echo",
            "display": "零界·演练",
            "domain": ["owner"],
            "events": ["im.message.receive_v1"],
            "perms": [],
            "status": "planned",
            "group_policy": {"mode": "p2p_only"},
        }
    )
    roles, _ = fr.validate_registry(reg)
    routes["routes"].append(
        {
            "id": "r.demo.echo",
            "match": {"role": "zb.demo.echo"},
            "handler": "demo:echo",
            "priority": 70,
        }
    )
    rs = fr.validate_routes(routes, roles_index=roles)
    assert fr.match_route(rs, role="zb.demo.echo") == "demo:echo"


def test_sop_step2_route_unknown_role_rejected():
    _, routes = _load()
    routes["routes"].append(
        {"id": "r.bad", "match": {"role": "zb.no.such"}, "handler": "x", "priority": 1}
    )
    roles, _ = fr.load_registry()
    with pytest.raises(fr.RegistryError):
        fr.validate_routes(routes, roles_index=roles)


def test_sop_step3_credentials_interface(tmp_path=None):
    root = os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "aios_test_tmp",
        uuid.uuid4().hex,
    )
    os.makedirs(root, exist_ok=True)
    try:
        p = os.path.join(root, "zb.demo.echo.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"app_id": "cli_demo", "app_secret": "demo-secret"}, f)
        aid, sec = base.load_secret("zb.demo.echo", root)
        assert (aid, sec) == ("cli_demo", "demo-secret")
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_sop_step4_shell_and_group_policy():
    reg = json.load(open(fr.DEFAULT_REGISTRY, encoding="utf-8"))
    reg["roles"].append(
        {
            "role": "zb.demo.echo",
            "display": "零界·演练",
            "domain": ["owner"],
            "events": ["im.message.receive_v1"],
            "perms": [],
            "status": "planned",
            "group_policy": {"mode": "p2p_only"},
        }
    )
    roles, _ = fr.validate_registry(reg)
    raw = {"_index": roles, "group_whitelist": reg["group_whitelist"]}
    shell = base.Shell("zb.demo.echo", registry=raw)
    assert shell.display == "零界·演练"
    assert shell.allow_inbound("p2p", "oc_x", "hi")
    assert not shell.allow_inbound("group", "oc_x", "hi")


def test_sop_step5_console_model_accepts_role():
    # 管理台数据来自 registry 校验结果：新角色只要通过校验即可出现在台账
    reg, _ = _load()
    reg["roles"].append(
        {
            "role": "zb.demo.echo",
            "display": "零界·演练",
            "domain": ["owner"],
            "events": [],
            "perms": [],
            "status": "planned",
            "group_policy": {"mode": "p2p_only"},
        }
    )
    roles, _ = fr.validate_registry(reg)
    assert roles["zb.demo.echo"]["status"] == "planned"
    # 管理台的两个硬性数据字段（display/app_ref）都可取自 registry
    assert roles["zb.demo.echo"]["display"].startswith("零界·")


def test_sop_full_copy_does_not_touch_production_files():
    """演练只读生产 registry/routes 的副本，末尾应保持不变（无副作用验证）。"""
    before_reg = open(fr.DEFAULT_REGISTRY, encoding="utf-8").read()
    before_routes = open(fr.DEFAULT_ROUTES, encoding="utf-8").read()
    reg, routes = _load()
    reg["roles"].append(
        {
            "role": "zb.demo.echo",
            "display": "零界·演练",
            "domain": ["owner"],
            "events": [],
            "perms": [],
            "status": "planned",
            "group_policy": {},
        }
    )
    routes["routes"].append(
        {
            "id": "r.demo.echo",
            "match": {"role": "zb.demo.echo"},
            "handler": "demo:echo",
            "priority": 70,
        }
    )
    assert open(fr.DEFAULT_REGISTRY, encoding="utf-8").read() == before_reg
    assert open(fr.DEFAULT_ROUTES, encoding="utf-8").read() == before_routes
