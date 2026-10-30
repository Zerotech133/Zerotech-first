# -*- coding: utf-8 -*-
"""Phase C evidence artifact 测试。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "agent_platform" / "legion_command" / "evidence.py"


def _load():
    spec = importlib.util.spec_from_file_location("evidence_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_new_append_finalize(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGION_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.delenv("LEGION_CAP_KEY_FILE", raising=False)
    m = _load()
    m.new_run("run-x", task="T1", agent="A0")
    m.append("run-x", "hermes_plan", {"plan": "p"})
    m.finalize("run-x", mode="answer")
    data = m.get("run-x")
    assert data is not None
    assert data["run_id"] == "run-x" and data["task"] == "T1"
    assert data["mode"] == "answer"
    assert len(data["events"]) == 1
    assert data["events"][0]["event"] == "hermes_plan"


def test_signature_when_key(monkeypatch, tmp_path):
    key = tmp_path / "cap.key"
    key.write_bytes(b"k" * 32)
    monkeypatch.setenv("LEGION_EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(key))
    m = _load()
    m.new_run("run-y")
    rec = m.finalize("run-y", mode="answer")
    assert rec.get("signature") and len(rec["signature"]) == 64
