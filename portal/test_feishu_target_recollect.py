# -*- coding: utf-8 -*-
"""收件人重采工具单测：纯匹配逻辑、补丁生成、离线 chats-file、CLI 无凭据降级。"""
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_target_recollect as tr  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(r"C:\项目\work\_dsh_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


OLD = [{"name": "个人", "type": "open_id", "app": "cli_OLD"},
       {"name": "内部测试", "type": "chat_id", "app": "cli_OLD"},
       {"name": "量化群", "type": "chat_id", "app": "cli_OLD"}]


def test_build_patch_all_matched_with_alias():
    chats = [{"chat_id": "oc_new1", "name": "内部测试"},
             {"chat_id": "oc_new2", "name": "老田的AI量化"}]
    out = tr.build_patch(OLD, chats, owner_open_id="ou_new", app_id="cli_NEW")
    assert out["unmatched"] == []
    assert len(out["patch"]) == 3
    ids = {p["name"]: p["id"] for p in out["patch"]}
    assert ids["个人"] == "ou_new"
    assert ids["量化群"] == "oc_new2"  # alias 命中「老田的AI量化」
    assert all(p["app"] == "cli_NEW" for p in out["patch"])


def test_build_patch_missing_owner_and_group():
    out = tr.build_patch(OLD, [{"chat_id": "oc_new1", "name": "内部测试"}])
    names = {u["name"] for u in out["unmatched"]}
    assert names == {"个人", "量化群"}


def test_build_patch_empty_chats_warns():
    out = tr.build_patch(OLD, [], owner_open_id="ou_new", app_id="cli_NEW")
    assert out["warnings"] and "未加入任何群" in out["warnings"][0]


def test_run_without_creds_fails_soft(ws_tmp, monkeypatch):
    monkeypatch.setattr(tr, "SECRETS", ws_tmp)
    rep = tr.run("zb.ops.notify")
    assert rep["ok"] is False and "凭据不可用" in rep["error"]


def test_run_with_chats_file_and_fake_secret(ws_tmp, monkeypatch):
    monkeypatch.setattr(tr, "SECRETS", ws_tmp)
    with open(os.path.join(ws_tmp, "zb.ops.notify.json"), "w",
              encoding="utf-8") as f:
        json.dump({"app_id": "cli_NEW", "app_secret": "s"}, f)
    monkeypatch.setattr(tr, "role_creds", lambda role, secrets_dir=None: ("cli_NEW", "tok"))
    chats = os.path.join(ws_tmp, "chats.json")
    with open(chats, "w", encoding="utf-8") as f:
        json.dump([{"chat_id": "oc_n1", "name": "内部测试"},
                   {"chat_id": "oc_n2", "name": "老田的AI量化"}], f)
    rep = tr.run("zb.ops.notify", owner_open_id="ou_new",
                 chats_file=chats, dry_run=True)
    assert rep["ok"] is True
    assert len(rep["patch"]) == 3


def test_cli_json_without_creds_returns_nonzero():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_target_recollect.py")
    r = subprocess.run([sys.executable, script, "--role", "zb.no.such.role",
                        "--json"], capture_output=True, timeout=60)
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["ok"] is False
    assert "凭据不可用" in data["error"]
