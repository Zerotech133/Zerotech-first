# -*- coding: utf-8 -*-
"""devtask sandbox 开关测试（WO-SANDBOX-WIRE-20261027）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEV = REPO / "tools" / "devtask.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(DEV), "sandbox", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        cwd=str(REPO),
    )


def test_sandbox_dry_run_zero_container():
    r = _run("--cmd", "python3 -c 'print(1)'", "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    joined = " ".join(out["docker_cmd"])
    assert "--network none" in joined
    assert ":ro" in joined


def test_sandbox_requires_cmd():
    r = _run()
    assert r.returncode != 0
    assert "required" in r.stderr.lower() or "usage" in r.stderr.lower()


def test_sandbox_quota_passthrough():
    r = _run("--cmd", "python3 -c 'print(1)'", "--dry-run",
             "--memory", "128m", "--cpus", "0.25", "--pids-limit", "32")
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["quotas"] == {"memory": "128m", "cpus": "0.25", "pids_limit": 32}
    joined = " ".join(out["docker_cmd"])
    assert "--memory 128m" in joined and "--pids-limit 32" in joined
