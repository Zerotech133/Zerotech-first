# -*- coding: utf-8 -*-
"""C1 OH broker 准入测试。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "agent_platform" / "legion_command" / "oh_auth.py"
CAP = ROOT / "agent_platform" / "legion_ide" / "cap_token.py"


def _load():
    spec = importlib.util.spec_from_file_location("oh_auth_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cap(key_path):
    spec = importlib.util.spec_from_file_location("cap_for_oh", CAP)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_disabled_allows(monkeypatch):
    monkeypatch.setenv("OH_BROKER_AUTH", "0")
    m = _load()
    assert m.authorize({}) is None


def test_no_auth_denied(monkeypatch):
    monkeypatch.delenv("OH_BROKER_AUTH", raising=False)
    m = _load()
    err = m.authorize({})
    assert err and err["error"] == "oh_auth_required"


def test_valid_cap_allows(monkeypatch, tmp_path):
    monkeypatch.delenv("OH_BROKER_AUTH", raising=False)
    key = tmp_path / "cap.key"
    key.write_bytes(b"k" * 32)
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(key))
    m = _load()
    tok = _cap(key).issue(["run:execute"], path=key)
    assert m.authorize({"X-ZB-Cap": tok}) is None


def test_portal_identity_monkeypatched(monkeypatch):
    monkeypatch.delenv("OH_BROKER_AUTH", raising=False)
    m = _load()
    monkeypatch.setattr(m, "_portal_ok", lambda headers: True)
    assert m.authorize({}) is None
