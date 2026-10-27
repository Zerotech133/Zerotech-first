# -*- coding: utf-8 -*-
"""沙箱出口白名单策略测试（WO-SANDBOX-QUOTA-20261027）。"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "network_policy_check.py"
_SPEC = importlib.util.spec_from_file_location("network_policy_check", str(TOOL))
NP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(NP)


def test_actual_policy_valid_and_check_cli():
    pol = NP._read(NP.POLICY)
    assert NP.validate(pol) == []
    r = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["default"] == "deny"


def test_decide_allow_and_deny():
    pol = NP._read(NP.POLICY)
    assert NP.decide("pypi.org", 443, pol)["allowed"] is True
    assert NP.decide("pypi.org", 80, pol)["allowed"] is False
    assert NP.decide("evil.example.com", 443, pol)["allowed"] is False


def test_cli_denied_exit3(tmp_path):
    r = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--host", "evil.example.com", "--port", "443"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 3
    assert json.loads(r.stdout)["allowed"] is False


def test_wildcard_rejected(tmp_path):
    bad = {
        "schema": "sandbox_network_policy/v1", "updated": "x",
        "default": "deny", "mode": "proxy", "proxy": "http://127.0.0.1:3128",
        "allow": [{"host": "*.example.com", "port": 443}],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    problems = NP.validate(bad)
    assert any("host_not_exact" in x for x in problems)
    r = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--policy", str(p), "--check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 2
