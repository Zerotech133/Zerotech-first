# -*- coding: utf-8 -*-
"""Phase A 能力令牌测试。"""
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "agent_platform" / "legion_ide" / "cap_token.py"


def _load():
    spec = importlib.util.spec_from_file_location("cap_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _key(tmp_path):
    p = tmp_path / "cap.key"
    p.write_bytes(b"k" * 32)
    return p


def test_issue_verify_and_scope(tmp_path):
    m = _load()
    k = _key(tmp_path)
    tok = m.issue(["ide:write:T1", "ide:read"], task="T1", path=k)
    assert m.verify(tok, required_scope="ide:write:T1", task="T1", path=k)["ok"] is True
    assert m.verify(tok, path=k)["ok"] is True
    bad = m.verify(tok, required_scope="ide:write:T2", task="T2", path=k)
    assert bad["ok"] is False and bad["error"] == "cap_scope"
    wrong_task = m.verify(tok, task="T2", path=k)
    assert wrong_task["error"] == "cap_task"


def test_expired_and_tampered(tmp_path):
    m = _load()
    k = _key(tmp_path)
    tok = m.issue(["ide:read"], ttl=30, path=k)
    real = time.time
    try:
        m.time.time = lambda: real() + 31
        assert m.verify(tok, path=k)["error"] == "cap_expired"
    finally:
        m.time.time = real
    body, sig = tok.rsplit(".", 1)
    assert m.verify(body + "." + ("0" * len(sig)), path=k)["error"] == "cap_invalid"
    assert m.verify("garbage", path=k)["error"] == "cap_required"


def test_disabled_without_key(tmp_path):
    m = _load()
    assert m.enabled(tmp_path / "missing.key") is False
    assert m.verify("anything", path=tmp_path / "missing.key")["ok"] is True
