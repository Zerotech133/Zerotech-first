# -*- coding: utf-8 -*-
"""jev_card_action 单测（W3 清债：9104 老链迁移目标）+ dispatch 接线。"""
import os
import shutil
import sys
import types
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_card_action as jca  # noqa: E402
import card_actions  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(r"C:\项目\work\_dsh_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setenv("LARK_ALERT_OPEN_ID", "ou_owner")
    monkeypatch.delenv("LARK_ALLOWED_OPERATORS", raising=False)
    return "ou_owner"


def _ack_true(alert_id):
    return True


def test_ack_success(owner):
    ok, msg = jca.handle_action("jev_ack", {"alert_id": "a1"}, who=owner,
                                ack_fn=_ack_true, title_fn=lambda a: "")
    assert ok is True and "ACKED" in msg


def test_missing_alert_id(owner):
    ok, msg = jca.handle_action("jev_ack", {}, who=owner, ack_fn=_ack_true)
    assert ok is False and "alert_id" in msg


def test_operator_not_allowed(owner):
    ok, msg = jca.handle_action("jev_ack", {"alert_id": "a1"}, who="ou_other",
                                ack_fn=_ack_true)
    assert ok is False and "允许名单" in msg


def test_empty_allowlist_denies(monkeypatch):
    monkeypatch.delenv("LARK_ALERT_OPEN_ID", raising=False)
    monkeypatch.delenv("LARK_ALLOWED_OPERATORS", raising=False)
    monkeypatch.setattr(jca, "_push_owner_open_id", lambda: "")
    ok, _ = jca.handle_action("jev_ack", {"alert_id": "a1"}, who="ou_x",
                              ack_fn=_ack_true)
    assert ok is False


def test_allowlist_falls_back_to_push_owner(monkeypatch):
    """bot 进程无 LARK_ALERT_OPEN_ID 时，回退 push_targets「个人」（老田）。"""
    monkeypatch.delenv("LARK_ALERT_OPEN_ID", raising=False)
    monkeypatch.delenv("LARK_ALLOWED_OPERATORS", raising=False)
    monkeypatch.setattr(jca, "_push_owner_open_id", lambda: "ou_owner_from_table")
    assert jca.allowed_operators() == {"ou_owner_from_table"}
    ok, msg = jca.handle_action("jev_ack", {"alert_id": "a1"},
                                who="ou_owner_from_table", ack_fn=_ack_true)
    assert ok is True


def test_push_owner_reads_real_config():
    """生产 news_config 可读出个人 open_id（不写死）。"""
    oid = jca._push_owner_open_id()
    assert oid.startswith("ou_") and len(oid) > 10


def test_db_failure_not_faked(owner):
    ok, msg = jca.handle_action("jev_ack", {"alert_id": "a1"}, who=owner,
                                ack_fn=lambda a: False)
    assert ok is False and "数据库" in msg


def test_ignore_writes_and_dedupes(owner, ws_tmp):
    path = os.path.join(ws_tmp, "gate_ignored.json")
    ok, msg = jca.handle_action("jev_ignore", {"alert_id": "a1"}, who=owner,
                                ack_fn=_ack_true, title_fn=lambda a: "标题A",
                                ignore_file=path)
    assert ok is True and "已忽略" in msg
    items = jca.load_ignored(path)
    assert len(items) == 1 and items[0]["alert_id"] == "a1"
    assert items[0]["title"] == "标题A" and items[0]["ts"]
    # 重复忽略同一条 → 只有一条
    jca.handle_action("jev_ignore", {"alert_id": "a1"}, who=owner,
                      ack_fn=_ack_true, title_fn=lambda a: "标题A2",
                      ignore_file=path)
    items = jca.load_ignored(path)
    assert len(items) == 1 and items[0]["title"] == "标题A2"


def test_ignore_write_failure_reported(owner, ws_tmp, monkeypatch):
    def boom(items, path=None):
        raise OSError("disk full")
    monkeypatch.setattr(jca, "save_ignored", boom)
    ok, msg = jca.handle_action("jev_ignore", {"alert_id": "a2"}, who=owner,
                                ack_fn=_ack_true, title_fn=lambda a: "",
                                ignore_file=os.path.join(ws_tmp, "x.json"))
    assert ok is False and "忽略集写入失败" in msg


def test_unknown_action(owner):
    ok, msg = jca.handle_action("tune_approve", {"alert_id": "a1"}, who=owner)
    assert ok is False and "不支持" in msg


def test_dispatch_routes_jev_actions(monkeypatch, ws_tmp):
    monkeypatch.setattr(card_actions, "LOG_PATH",
                        os.path.join(ws_tmp, "action_log.jsonl"))
    calls = {}

    def fake_handle(action_id, payload, who=""):
        calls["args"] = (action_id, payload, who)
        return True, "done"

    fake = types.SimpleNamespace(handle_action=fake_handle)
    monkeypatch.setitem(sys.modules, "jev_card_action", fake)
    ok, msg = card_actions.dispatch("jev_ack", {"alert_id": "a1"}, "ou_owner")
    assert ok is True and msg == "done"
    assert calls["args"][0] == "jev_ack" and calls["args"][2] == "ou_owner"


def test_registry_describes_new_chain():
    info = card_actions.lookup("jev_ack")
    assert info is not None
    assert "jev_card_action" in info["handler"]
