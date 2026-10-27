# -*- coding: utf-8 -*-
"""沙箱资源配额测试（WO-SANDBOX-QUOTA-20261027）。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "agent_sandbox", str(REPO / "agent_platform" / "tools" / "agent_sandbox.py")
)
AS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(AS)


def test_quota_flags_in_docker_cmd(tmp_path):
    cmd = AS.build_docker_cmd("img:1", tmp_path, tmp_path / "s", "echo hi",
                              memory="256m", cpus="0.5", pids_limit=64)
    joined = " ".join(cmd)
    assert "--memory 256m" in joined
    assert "--cpus 0.5" in joined
    assert "--pids-limit 64" in joined


def test_run_dry_run_reports_quotas(tmp_path):
    out = AS.run_cmd("python3 -c 'print(1)'", dry_run=True, repo=tmp_path,
                     memory="128m", cpus="0.25", pids_limit=32)
    assert out["quotas"] == {"memory": "128m", "cpus": "0.25", "pids_limit": 32}
    joined = " ".join(out["docker_cmd"])
    assert "--memory 128m" in joined and "--pids-limit 32" in joined
