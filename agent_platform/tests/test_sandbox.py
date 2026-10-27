# -*- coding: utf-8 -*-
"""沙箱验证测试（WO-SANDBOX-VERIFY）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sandbox_runner", str(_WT / "agent_platform" / "sandbox" / "runner.py")
)
SR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SR)


def _tmp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(SR, "SANDBOX", tmp_path / "sandbox")
    monkeypatch.setattr(SR, "RESULTS", tmp_path / "sandbox" / "results.jsonl")
    monkeypatch.setattr(SR, "SKILLS", tmp_path / "skills")


def _mk_skill(tmp_path, name, code):
    d = tmp_path / "skills" / name / "scripts"
    d.mkdir(parents=True)
    (d / "verify.py").write_text(code, encoding="utf-8")


def test_real_skill_verify_ok():
    rep = SR.verify_skill("health-card", timeout=120)
    assert rep["ok"] is True and rep["rc"] == 0
    assert "sandbox_dir" in rep


def test_bad_name_and_missing_script():
    assert SR.verify_skill("../etc/passwd")["error"] == "bad_skill_name"
    assert SR.verify_skill("no-such-skill")["error"] == "verify_script_not_found"


def test_env_secrets_stripped_and_cwd_isolated(tmp_path, monkeypatch):
    _tmp_env(tmp_path, monkeypatch)
    _mk_skill(
        tmp_path,
        "env-skill",
        (
            "import json, os, pathlib\n"
            "print(json.dumps({'has_key': 'DEEPSEEK_API_KEY' in os.environ,"
            " 'cwd': os.getcwd()}))\n"
        ),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "should-not-leak")
    rep = SR.verify_skill("env-skill")
    assert rep["ok"] is True
    payload = json.loads(rep["stdout"])
    assert payload["has_key"] is False
    assert "sandbox" in payload["cwd"] and "env-skill" in payload["cwd"]


def test_timeout(tmp_path, monkeypatch):
    _tmp_env(tmp_path, monkeypatch)
    _mk_skill(tmp_path, "slow-skill", "import time\ntime.sleep(10)\n")
    rep = SR.verify_skill("slow-skill", timeout=1)
    assert rep["ok"] is False and rep["error"] == "timeout"


def test_recent_records(tmp_path, monkeypatch):
    _tmp_env(tmp_path, monkeypatch)
    _mk_skill(tmp_path, "ok-skill", "print('ok')\n")
    SR.verify_skill("ok-skill")
    rep = SR.recent()
    assert rep["ok"] and rep["count"] >= 1
    assert rep["recent"][-1]["skill"] == "ok-skill"
