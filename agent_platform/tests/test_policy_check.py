# -*- coding: utf-8 -*-
"""策略单源（policy.json）决策器测试（WO-POLICY-CODE-20261027）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "policy_check.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def test_matrix_parity_ok():
    r = _run("--check")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["ok"] is True
    assert "ARCH1" in data["covered"]
    assert data["mismatches"] == []


def test_arch1_four_tools_allowed():
    for tool in ("codebase_intel", "sandbox_verify", "eval_harness", "trace_log"):
        r = _run("--agent", "ARCH1", "--op", "execute", "--target", tool)
        assert r.returncode == 0, "%s should be allowed: %s" % (tool, r.stdout)
        assert json.loads(r.stdout)["allowed"] is True


def test_unlisted_tool_denied():
    r = _run("--agent", "ARCH1", "--op", "execute", "--target", "not_a_tool")
    assert r.returncode == 3
    assert json.loads(r.stdout)["allowed"] is False


def test_danger_deny_l2():
    r = _run("--agent", "ARCH1", "--op", "danger", "--target", "rm")
    assert r.returncode == 3
    data = json.loads(r.stdout)
    assert data["allowed"] is False
    assert data["approval"] == "L2"
    assert data["human_required"] is True


def test_unknown_agent_denied():
    r = _run("--agent", "DEV9", "--op", "execute", "--target", "python")
    assert r.returncode == 3
    assert "unknown agent" in json.loads(r.stdout)["reason"]


def test_host_system_requires_l2():
    # ARCH1 未声明 host_system → deny，但审批级别应为 L2 且 human_required=True
    r = _run("--agent", "ARCH1", "--op", "host_system", "--target", "firewall")
    assert r.returncode == 3
    data = json.loads(r.stdout)
    assert data["approval"] == "L2" and data["human_required"] is True
