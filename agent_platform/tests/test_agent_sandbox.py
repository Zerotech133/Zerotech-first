# -*- coding: utf-8 -*-
"""Agent 沙箱/benchmark 测试（WO-AGENT-SANDBOX-20261027）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "agent_sandbox", str(REPO / "agent_platform" / "tools" / "agent_sandbox.py")
)
AS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(AS)


def test_docker_cmd_security_flags(tmp_path):
    cmd = AS.build_docker_cmd("img:1", tmp_path, tmp_path / "scratch", "echo hi", "none")
    s = " ".join(cmd)
    assert "--network none" in s
    assert "--cap-drop ALL" in s
    assert "no-new-privileges" in s
    assert ":ro" in s  # 仓库只读
    assert "--read-only" in s
    assert "PYTHONDONTWRITEBYTECODE=1" in s
    bridged = AS.build_docker_cmd("img:1", tmp_path, tmp_path / "scratch", "echo hi", "bridge")
    assert "--network bridge" in " ".join(bridged)


def test_dry_run_zero_write(tmp_path):
    out = AS.run_cmd("python3 -c 'print(1)'", dry_run=True, repo=tmp_path)
    assert out["dry_run"] is True
    assert "docker_cmd" in out


def test_benchmark_tasks_shape():
    data = AS.load_benchmark()
    tasks = data["tasks"]
    assert len(tasks) == 10
    cats = {}
    for t in tasks:
        cats[t["category"]] = cats.get(t["category"], 0) + 1
        assert t.get("id") and t.get("cmd") and t.get("expect_stdout")
    assert cats == {"SWE": 4, "research": 3, "data": 3}


def test_benchmark_dry_run(tmp_path):
    out = AS.benchmark(out=str(tmp_path / "bench.json"), dry_run=True)
    assert out["dry_run"] is True
    assert out["tasks"] == 10
    assert not (tmp_path / "bench.json").exists()
