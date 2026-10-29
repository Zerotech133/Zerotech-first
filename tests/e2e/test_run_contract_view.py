# -*- coding: utf-8 -*-
"""E2E：运行契约只读聚合（O6 P2d）。"""
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


def _traces():
    """兼容 worktree（数据在主编排目录）与主仓。"""
    candidates = [
        REPO / "agent_platform" / "data" / "observability" / "traces.sqlite",
        REPO.parents[1] / "agent_platform" / "data" / "observability" / "traces.sqlite",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def test_run_contract_view_json():
    traces = _traces()
    assert traces, "找不到 traces.sqlite"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, "-X", "utf8", "tools/run_contract_view.py", "--traces", traces, "--json"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=120)
    assert p.returncode == 0, p.stdout[-300:] + p.stderr[-300:]
    data = json.loads(p.stdout[p.stdout.find("{"):])
    assert data["ok"] is True
    assert data["runs"] >= 1
    assert "task_success_rate" in data
    assert data["validation_errors"] == []
