# -*- coding: utf-8 -*-
"""feishu_bot_base 单测（W1）：群策略纯函数、凭据加载、心跳、AT 清洗、注册表联动。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_bot_base as base  # noqa: E402

import shutil
import uuid


@pytest.fixture()
def ws_tmp():
    root = os.path.join(r"C:\项目\work\_dsh_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_group_p2p_always_allowed():
    ok, _ = base.group_policy_allows({"mode": "p2p_only"}, "p2p", "oc_x", "")
    assert ok


def test_group_p2p_only_rejects_group():
    ok, why = base.group_policy_allows({"mode": "p2p_only"}, "group", "oc_x", "")
    assert not ok and "p2p_only" in why


def test_mention_requires_at_placeholder():
    ok, _ = base.group_policy_allows({"mode": "mention"}, "group", "oc_x", "@_user_1 你好")
    assert ok
    ok2, _ = base.group_policy_allows({"mode": "mention"}, "group", "oc_x", "你好")
    assert not ok2


def test_whitelist_group_allows_without_at():
    ok, why = base.group_policy_allows(
        {"mode": "mention_or_whitelist"}, "group", "oc_allowed", "你好",
        whitelist_chat_ids=["oc_allowed"])
    assert ok and why == "whitelist_hit"


def test_whitelist_mode_rejects_unknown_group():
    ok, why = base.group_policy_allows(
        {"mode": "whitelist"}, "group", "oc_other", "hi", whitelist_chat_ids=["oc_ok"])
    assert not ok and "whitelist" in why


def test_broadcast_mode_never_accepts_inbound():
    for ct in ("p2p", "group"):
        ok, _ = base.group_policy_allows({"mode": "broadcast"}, ct, "oc_x", "")
        assert not ok


def test_strip_at_placeholder():
    assert base.strip_at("@_user_1  看看今天行情") == "看看今天行情"
    assert base.strip_at("无at") == "无at"


def test_mask_open_id():
    assert base.mask_open_id("ou_50a95455c7dffec1090ade400886d0d4") == "ou_50a95**"
    assert base.mask_open_id("") == "**"


def test_load_secret_ok_and_errors(ws_tmp):
    p = os.path.join(ws_tmp, "zb.owner.exec.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"app_id": "cli_xxx", "app_secret": "s3cret"}, f)
    app_id, app_secret = base.load_secret("zb.owner.exec", ws_tmp)
    assert app_id == "cli_xxx" and app_secret == "s3cret"
    with pytest.raises(base.BotError):
        base.load_secret("zb.no.such", ws_tmp)
    with open(os.path.join(ws_tmp, "zb.bad.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with pytest.raises(base.BotError):
        base.load_secret("zb.bad", ws_tmp)


def test_heartbeat_roundtrip(ws_tmp):
    base.write_heartbeat("zb.test.x", "patrol", ws_tmp)
    txt = base.read_heartbeat("zb.test.x", ws_tmp)
    assert "patrol" in txt


def test_shell_uses_registry_policy_and_whitelist():
    raw = {
        "_index": {
            "zb.demo.echo": {
                "role": "zb.demo.echo", "display": "零界·演练",
                "domain": ["owner"], "events": ["im.message.receive_v1"],
                "perms": [], "status": "planned",
                "group_policy": {"mode": "mention_or_whitelist",
                                 "allow_chats": []},
            }
        },
        "group_whitelist": [
            {"name": "内部测试", "chat_id": "oc_6b97bee8b5340e071d0fc10cc6c8569f"}],
    }
    shell = base.Shell("zb.demo.echo", registry=raw)
    assert shell.display == "零界·演练"
    assert shell.allow_inbound("p2p", "oc_x", "hi")
    assert shell.allow_inbound("group", "oc_6b97bee8b5340e071d0fc10cc6c8569f", "hi")
    assert not shell.allow_inbound("group", "oc_other", "hi")
