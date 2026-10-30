# -*- coding: utf-8 -*-
"""P0-2 出网脱敏工具测试。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "agent_platform" / "legion_command" / "redact_outbound.py"


def _load():
    spec = importlib.util.spec_from_file_location("redact_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_text_passthrough():
    m = _load()
    r = m.redact("修复了登录接口的 302 跳转，测试 15 passed")
    assert r["level"] == "L0" and r["hits"] == [] and r["blocked"] is False
    assert "302" in r["text"]


def test_generic_secret_redacted_and_blocked():
    m = _load()
    r = m.redact("api_key = sk-abc1234567890XYZ 请勿外传", block=True)
    assert r["level"] == "L3" and r["blocked"] is True
    assert "sk-abc" not in r["text"] and "[REDACTED" in r["text"]
    assert any(h["kind"] == "credential_assign" for h in r["hits"])


def test_private_key_and_bearer():
    m = _load()
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\nAuthorization: Bearer abcdefghijklmnop"
    r = m.redact(text)
    assert r["level"] == "L3"
    assert "MIIabc" not in r["text"] and "abcdefghijklmnop" not in r["text"]
    kinds = {h["kind"] for h in r["hits"]}
    assert "private_key" in kinds and "bearer" in kinds


def test_phone_and_idcard_are_l2():
    m = _load()
    r = m.redact("联系人 13812345678，身份证 110101199003071234")
    assert r["level"] == "L2" and r["blocked"] is False
    assert "13812345678" not in r["text"] and "110101199003071234" not in r["text"]


def test_log_audit_no_plaintext(monkeypatch, tmp_path):
    m = _load()
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("OUTBOUND_AUDIT_PATH", str(audit))
    r = m.redact("token=supersecretvalue123")
    m.log_outbound("zhuge_review", "T1", "openclaw", r)
    raw = audit.read_text(encoding="utf-8")
    assert "supersecretvalue123" not in raw
    row = json.loads(raw.splitlines()[0])
    assert row["kind"] == "zhuge_review" and row["level"] == "L3"
