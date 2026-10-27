# -*- coding: utf-8 -*-
"""研发域最高权限 charter · devtask 审批署名测试（WO-AUTHORITY-B2）。

覆盖：①charter 配置加载 fail-safe；②_approval_check 阈值（非 dev 跳过/缺署名/
非授权人/自审自批/block）；③cmd_new --approver 落账与旧调用兼容。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import devtask  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    wt = tmp_path / ".worktrees"
    wt.mkdir()
    monkeypatch.setattr(devtask, "WT_DIR", str(wt))
    monkeypatch.setattr(devtask, "TASKS", str(wt / "tasks.json"))
    monkeypatch.setattr(devtask, "DEV_GATE", str(tmp_path / "gate.json"))
    monkeypatch.setattr(devtask, "DEV_ROUTING", str(tmp_path / "routing.json"))
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "approval": {"mode": "warn", "approver": "ARCH1", "alternates": ["DEV1"]}
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(devtask, "DEV_AUTHORITY", str(auth_path))
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "")
    monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
    monkeypatch.setattr(devtask, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(devtask, "_owner_check",
                        lambda owner: (True, {"unowned_tag": "[UNOWNED]"}, 60))
    monkeypatch.setattr(devtask, "_ev_precheck", lambda *a, **k: [])
    monkeypatch.setattr(devtask, "_ears_lint", lambda *a, **k: [])
    monkeypatch.setenv("AGENT_NAME", "DEV1")
    return {"tmp": tmp_path, "auth": auth_path}


def test_load_authority_defaults(sandbox):
    cfg = devtask._load_dev_authority()
    assert cfg["approval"]["mode"] == "warn"
    assert cfg["approval"]["approver"] == "ARCH1"
    assert "DEV1" in cfg["approval"]["alternates"]


def test_approval_check_logic():
    cfg = {"approval": {"mode": "warn", "approver": "ARCH1", "alternates": ["DEV1"]}}
    assert devtask._approval_check({"dept": "quant", "owner": "X"}, cfg) == ([], False)
    ok = {"dept": "dev", "owner": "DEV1", "approver": "ARCH1"}
    assert devtask._approval_check(ok, cfg) == ([], False)
    warn = []
    w, blocked = devtask._approval_check({"dept": "dev", "owner": "DEV1"}, cfg)
    assert blocked is False and any("缺 ARCH1" in x for x in w)
    w2, b2 = devtask._approval_check({"dept": "dev", "owner": "DEV1",
                                      "approver": "ARCH1"},
                                     {"approval": {"mode": "block",
                                                   "approver": "ARCH1",
                                                   "alternates": []}})
    assert b2 is False
    w3, b3 = devtask._approval_check({"dept": "dev", "owner": "DEV1"},
                                     {"approval": {"mode": "block",
                                                   "approver": "ARCH1",
                                                   "alternates": []}})
    assert b3 is True and w3
    w4, _ = devtask._approval_check({"dept": "dev", "owner": "ARCH1",
                                     "approver": "ARCH1"}, cfg)
    assert any("自审自批" in x for x in w4)


def test_cmd_new_stores_approver(sandbox):
    rc = devtask.cmd_new("T-APPROVER-1", "审批署名测试单", "--dept", "dev",
                         "--acceptance", "pytest -q", "--approver", "ARCH1")
    assert rc == 0
    rec = json.loads((sandbox["tmp"] / ".worktrees" / "tasks.json")
                     .read_text(encoding="utf-8"))["T-APPROVER-1"]
    assert rec["dept"] == "dev"
    assert rec["acceptance_cmd"] == "pytest -q"
    assert rec["approver"] == "ARCH1"


def test_cmd_new_without_approver_auto_fills_charter_default(sandbox):
    rc = devtask.cmd_new("T-APPROVER-2", "无署名单", "--dept", "dev",
                         "--acceptance", "pytest -q")
    assert rc == 0
    rec = json.loads((sandbox["tmp"] / ".worktrees" / "tasks.json")
                     .read_text(encoding="utf-8"))["T-APPROVER-2"]
    assert rec["approver"] == "ARCH1"   # charter 默认署名（block 模式防空门）
