# -*- coding: utf-8 -*-
"""O2 任务契约 2.0 测试（WO-HARNESS-O12）：devtask 可选字段落账与旧调用兼容。"""
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
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"approval": {"mode": "warn", "approver": "ARCH1",
                                             "alternates": ["DEV1"]}}),
                    encoding="utf-8")
    monkeypatch.setattr(devtask, "DEV_AUTHORITY", str(auth))
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "")
    monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
    monkeypatch.setattr(devtask, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(devtask, "_owner_check",
                        lambda owner: (True, {"unowned_tag": "[UNOWNED]"}, 60))
    monkeypatch.setattr(devtask, "_ev_precheck", lambda *a, **k: [])
    monkeypatch.setattr(devtask, "_ears_lint", lambda *a, **k: [])
    monkeypatch.setenv("AGENT_NAME", "DEV1")
    return {"tmp": tmp_path}


def _rec(sandbox, tid):
    return json.loads((sandbox["tmp"] / ".worktrees" / "tasks.json")
                      .read_text(encoding="utf-8"))[tid]


def test_contract_fields_stored(sandbox):
    rc = devtask.cmd_new(
        "T-CONTRACT-1", "契约测试单", "--dept", "dev",
        "--acceptance", "pytest -q",
        "--expected-output", "JSON 报告",
        "--boundaries", "只改 tools/",
        "--effort", "S",
        "--deps", "T-A,T-B")
    assert rc == 0
    r = _rec(sandbox, "T-CONTRACT-1")
    assert r["expected_output"] == "JSON 报告"
    assert r["boundaries"] == "只改 tools/"
    assert r["effort"] == "S"
    assert r["deps"] == ["T-A", "T-B"]
    assert r["approver"] == "ARCH1"


def test_contract_fields_optional_old_shape(sandbox):
    rc = devtask.cmd_new("T-CONTRACT-2", "旧式单", "--dept", "dev",
                         "--acceptance", "pytest -q")
    assert rc == 0
    r = _rec(sandbox, "T-CONTRACT-2")
    for key in ("expected_output", "boundaries", "effort", "deps"):
        assert key not in r


def test_contract_missing_value_raises(sandbox):
    with pytest.raises(SystemExit):
        devtask.cmd_new("T-CONTRACT-3", "缺值单", "--dept", "dev",
                        "--acceptance", "pytest -q", "--deps")
