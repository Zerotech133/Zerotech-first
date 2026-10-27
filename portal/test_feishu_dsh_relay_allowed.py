# -*- coding: utf-8 -*-
"""feishu_dsh_relay.allowed 多域白名单单测（2026-10-25 换新 app open_id 修复）。"""

import json
import os
import tempfile
import shutil
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_dsh_relay as relay  # noqa: E402

OLD_OWNER = "ou_50a95455c7dffec1090ade400886d0d4"
NEW_EXEC_OWNER = "ou_92e4a0000000000000000000000000"


@pytest.fixture()
def ws_tmp():
    root = os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "aios_test_tmp",
        uuid.uuid4().hex,
    )
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_owners(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _use(monkeypatch, path):
    monkeypatch.setattr(relay, "OWNERS_FILE", path)
    monkeypatch.delenv("FEISHU_OWNER_OPEN_IDS", raising=False)


def test_owner_in_owners_json(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"open_ids": [NEW_EXEC_OWNER], "allow_any_p2p": False})
    _use(monkeypatch, p)
    assert relay.allowed(NEW_EXEC_OWNER) is True
    assert relay.allowed("ou_stranger") is False


def test_owner_from_env(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"open_ids": []})
    _use(monkeypatch, p)
    monkeypatch.setenv("FEISHU_OWNER_OPEN_IDS", "ou_env1,ou_env2")
    assert relay.allowed("ou_env2") is True
    assert relay.allowed("ou_stranger") is False


def test_p2p_failover_when_enabled(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"open_ids": [], "allow_any_p2p": True})
    _use(monkeypatch, p)
    assert relay.allowed("ou_unknown_new_app_id", chat_type="p2p") is True
    assert relay.allowed("ou_unknown_new_app_id", chat_type="group") is False


def test_p2p_failover_disabled_denies_stranger(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"open_ids": [], "allow_any_p2p": False})
    _use(monkeypatch, p)
    assert relay.allowed("ou_unknown", chat_type="p2p") is False


def test_empty_open_id_denied(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"allow_any_p2p": True})
    _use(monkeypatch, p)
    assert relay.allowed("") is False


def test_legacy_push_target_owner_still_allowed(monkeypatch, ws_tmp):
    p = os.path.join(ws_tmp, "owners.json")
    _write_owners(p, {"open_ids": [], "allow_any_p2p": False})
    _use(monkeypatch, p)
    # 旧 app 域 id 走 push_targets 兼容路径
    assert relay.allowed(OLD_OWNER) is True


def test_real_owners_file_contains_old_owner():
    cfg = relay._owner_config()
    assert OLD_OWNER in (cfg.get("open_ids") or [])
