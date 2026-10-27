# -*- coding: utf-8 -*-
"""E2E-04/05/06/07：工具只读冒烟（subprocess 调本仓脚本 --json/--print-only）。"""
import json
import os
import pathlib
import subprocess
import sys

REPO = str(pathlib.Path(__file__).resolve().parents[2])


def _run(args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8"] + args, cwd=REPO,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    return p


def _json_stdout(p):
    out = p.stdout or ""
    start = out.find("{")
    assert start >= 0, "无 JSON 输出: " + out[-300:]
    return json.loads(out[start:])


def test_c6_kpi_print_json():
    p = _run(["tools/c6_daily_kpi.py", "--print-only", "--days", "1"])
    assert p.returncode == 0, p.stderr[-400:]
    data = _json_stdout(p)
    m = data["metrics"]
    for key in ("period", "dev_share_per_week", "ownership_rate", "week_throughput"):
        assert key in m


def test_quality_scale_json():
    p = _run(["tools/quality_scale_check.py", "--json"])
    assert p.returncode == 0, p.stderr[-400:]
    data = _json_stdout(p)
    assert data["ok"] is True
    assert data["counts"]["none"] == 0


def test_extension_lint_json():
    p = _run(["tools/extension_lint.py", "--json"])
    assert p.returncode == 0, p.stderr[-400:]
    data = _json_stdout(p)
    assert data["ok"] is True
    assert data["manifests"] >= 5


def test_extension_manifest_audit_json():
    p = _run(["tools/extension_manifest_audit.py", "--json"])
    assert p.returncode == 0, p.stderr[-400:]
    data = _json_stdout(p)
    assert data["ok"] is True
    assert data["mapped"] >= 4
    assert data["mismatches"] == []


def test_mcp_security_check_json():
    p = _run(["tools/mcp_security_check.py", "--json"])
    assert p.returncode == 0, p.stderr[-400:]
    data = _json_stdout(p)
    assert data["errors"] == 0
