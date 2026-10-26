"""quality_gate 测试（WO-SEAT-REAL-B2D）。"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "quality_gate.py"
_spec = importlib.util.spec_from_file_location("quality_gate", str(_TOOL))
QG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(QG)


class _P:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def test_run_checks_all_ok(monkeypatch):
    monkeypatch.setattr(QG, "_run", lambda argv, timeout=QG.TIMEOUT: {"ok": True, "rc": 0})
    res = QG.run_checks()
    assert res["ok"] is True and set(res["checks"]) == {"compileall", "ports", "secrets"}


def test_run_checks_one_fail(monkeypatch):
    calls = {"n": 0}

    def fake(argv, timeout=QG.TIMEOUT):
        calls["n"] += 1
        return {"ok": calls["n"] != 2, "rc": 0 if calls["n"] != 2 else 1}

    monkeypatch.setattr(QG, "_run", fake)
    res = QG.run_checks()
    assert res["ok"] is False
    assert res["checks"]["ports"]["ok"] is False


def test_main_emits_per_seat_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(QG, "run_checks", lambda: {"ok": True, "checks": {}, "generated_at": "x"})
    calls = []
    monkeypatch.setattr(QG, "emit_audit", lambda s, r: calls.append(s) or {"ok": True})
    monkeypatch.delenv(QG.KILL_ENV, raising=False)
    rc = QG.main(["--seats", "QA1,SEC1", "--status-dir", str(tmp_path), "--quiet"])
    assert rc == 0
    assert calls == ["QA1", "SEC1"]
    assert (tmp_path / "status.json").is_file()
    assert (tmp_path / "QA1" / "status.json").is_file()


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(QG.KILL_ENV, "off")
    rc = QG.main(["--seats", "QA1", "--status-dir", str(tmp_path / "st"), "--quiet"])
    assert rc == 0 and not (tmp_path / "st").exists()


def test_run_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(QG.subprocess, "run", boom)
    out = QG._run(["x"])
    assert out["ok"] is False and out["error"] == "timeout"


def test_env_with_git_adds_git_path():
    import os
    env = QG._env_with_git()
    git_dirs = [d for d in (r"C:\Program Files\Git\cmd", r"C:\Program Files\Git\bin",
                            r"C:\Program Files (x86)\Git\cmd") if os.path.isdir(d)]
    if not git_dirs:
        import pytest
        pytest.skip("本机无 Git 安装目录")
    assert any(d in env["PATH"] for d in git_dirs)


def test_run_checks_passes_env_to_secrets(monkeypatch):
    seen = []

    def fake(argv, timeout=QG.TIMEOUT, env=None):
        seen.append((argv, env))
        return {"ok": True, "rc": 0}

    monkeypatch.setattr(QG, "_run", fake)
    res = QG.run_checks()
    assert res["ok"] is True
    secrets = [env for argv, env in seen
               if any("scan_secrets" in str(x) for x in argv)]
    assert secrets and secrets[0] and "Git" in secrets[0].get("PATH", "")
