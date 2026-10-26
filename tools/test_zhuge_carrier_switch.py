# -*- coding: utf-8 -*-
"""zhuge_carrier_switch.ps1 干跑用例（不联网、不改配置）。

策略：用 -ForceTier 跳过探测，只验证 tier→model 映射与 dry-run JSON 契约。
-Apply 的真实写入/自检不在单测里跑（会改本机 OpenClaw 配置）。
"""
import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ops" / "zhuge_carrier_switch.ps1"
PS = shutil.which("powershell") or shutil.which("pwsh") or "powershell.exe"


def _run(args, timeout=120):
    return subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)


def _json_out(cp):
    start = cp.stdout.find("{")
    assert start >= 0, cp.stdout + cp.stderr
    return json.loads(cp.stdout[start:])


def test_dryrun_force_gpt6():
    cp = _run(["-DryRun", "-ForceTier", "gpt6", "-Json"])
    assert cp.returncode == 0, cp.stderr
    d = _json_out(cp)
    assert d["dry_run"] is True
    assert d["target_tier"] == "gpt6"
    assert d["target"] == "itokengate/gpt-6-sol"


def test_dryrun_force_ms1max35():
    cp = _run(["-DryRun", "-ForceTier", "ms1max35", "-Json"])
    assert cp.returncode == 0, cp.stderr
    d = _json_out(cp)
    assert d["target"] == "ms1max-35b/qwen36-35b-fast"


def test_dryrun_force_deepseek_fallback():
    cp = _run(["-DryRun", "-ForceTier", "deepseek", "-Json"])
    assert cp.returncode == 0, cp.stderr
    d = _json_out(cp)
    assert d["target"] == "deepseek/deepseek-chat"
    assert d["action"] in ("plan-only", "no-change")
