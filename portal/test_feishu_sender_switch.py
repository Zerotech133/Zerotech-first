# -*- coding: utf-8 -*-
"""sender 统一切换器单测：目标域阻塞判定、步骤清单、状态盘点、CLI。"""
import json
import os
import tempfile
import shutil
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_sender_switch as sw  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_target_domain_blockers_mismatch():
    targets = [{"name": "个人", "id": "ou_a", "app": "cli_OLD"},
               {"name": "量化群", "id": "oc_b", "app": "cli_OLD"}]
    blockers = sw.target_domain_blockers(targets, "cli_NEW")
    assert len(blockers) == 2
    assert "230001" in blockers[0]["reason"]


def test_target_domain_blockers_clean():
    targets = [{"name": "个人", "id": "ou_a", "app": "cli_NEW"}]
    assert sw.target_domain_blockers(targets, "cli_NEW") == []


def test_target_domain_blockers_missing_app():
    targets = [{"name": "老条目", "id": "ou_a"}]
    blockers = sw.target_domain_blockers(targets, "cli_NEW")
    assert len(blockers) == 1 and "未登记 app" in blockers[0]["reason"]


def test_switch_steps_contains_blocker_when_domain_mismatch(ws_tmp, monkeypatch):
    monkeypatch.setattr(sw, "SECRETS", ws_tmp)
    steps = sw.switch_steps("zb.ops.notify", "cli_NEW",
                            [{"name": "个人", "id": "ou_a", "app": "cli_OLD"}])
    assert steps[0]["ok"] is False
    assert steps[1]["ok"] is False  # 无凭据
    assert "阻塞" in steps[0]["detail"]


def test_role_secret_ready(ws_tmp, monkeypatch):
    monkeypatch.setattr(sw, "SECRETS", ws_tmp)
    assert sw.role_secret_ready("zb.ops.notify") is False
    with open(os.path.join(ws_tmp, "zb.ops.notify.json"), "w",
              encoding="utf-8") as f:
        json.dump({"app_id": "cli_x", "app_secret": "s"}, f)
    assert sw.role_secret_ready("zb.ops.notify") is True


def test_status_structure():
    st = sw.status()
    assert set(st) >= {"ts", "canonical_app", "roles", "targets_total",
                       "target_domain_blockers", "ready_to_switch_sender"}
    assert len(st["roles"]) == 5
    assert isinstance(st["target_domain_blockers"], list)


def test_render_status_contains_blockers():
    st = sw.status()
    md = sw.render_status(st)
    assert "sender 统一" in md and "能否切 sender" in md


def test_cli_json_status():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_sender_switch.py")
    r = subprocess.run([sys.executable, script, "--status", "--json"],
                       capture_output=True, timeout=120)
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["canonical_app"]
