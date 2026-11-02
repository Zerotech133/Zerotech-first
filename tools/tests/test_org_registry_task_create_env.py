# -*- coding: utf-8 -*-
"""8093 task_create：AGENT_NAME 必须真正传给 devtask 子进程。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import org_registry_server as srv  # noqa: E402


def test_task_create_passes_env(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    monkeypatch.setattr(srv, "task_load", lambda: {"T-1": {"branch": "task/T-1", "worktree": "w", "owner": "D1", "agent": "D1"}})
    err, res = srv.task_create({"desc": "test", "tid": "T-1", "agent": "D1"})
    assert err is None
    assert captured["env"] is not None
    assert captured["env"].get("AGENT_NAME") == "D1"
    assert res["tid"] == "T-1"
