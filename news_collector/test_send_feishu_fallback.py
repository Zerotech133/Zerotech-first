# -*- coding: utf-8 -*-
"""send_feishu 群兜底单测（W3）：openapi 失败后 lark-cli chat 兜底、失败如实留证。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import send_feishu as sf  # noqa: E402


class _Run:
    def __init__(self, stdout="", stderr="", rc=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = rc


def test_group_fallback_success(monkeypatch, capsys):
    monkeypatch.setattr(sf, "_send_openapi",
                        lambda *a, **kw: (False, "openapi_fallback: err HTTP 400 bad"))
    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    monkeypatch.setattr(sf, "_flow_record", lambda ok, target: None)
    monkeypatch.setattr(sf.subprocess, "run", lambda *a, **kw: _Run(
        stdout=json.dumps({"ok": True, "data": {"message_id": "om_fb"}})))
    ok = sf.send_content_to("oc_x", "chat_id", '{"text":"hi"}', "text")
    assert ok is True
    assert "SEND_OK" in capsys.readouterr().out


def test_group_fallback_failure_reports_both(monkeypatch, capsys):
    monkeypatch.setattr(sf, "_send_openapi",
                        lambda *a, **kw: (False, "openapi_fallback: err HTTP 400 bad"))
    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    monkeypatch.setattr(sf, "_flow_record", lambda ok, target: None)
    monkeypatch.setattr(sf.subprocess, "run", lambda *a, **kw: _Run(
        stdout="", stderr="cli denied", rc=1))
    ok = sf.send_content_to("oc_x", "chat_id", '{"text":"hi"}', "text")
    assert ok is False
    out = capsys.readouterr().out
    assert "SEND_FAIL" in out and "lark-cli" in out


def test_openid_target_has_no_cli_fallback(monkeypatch, capsys):
    called = {"run": 0}
    monkeypatch.setattr(sf, "_send_openapi",
                        lambda *a, **kw: (False, "openapi_fallback: err"))
    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")

    def _run(*a, **kw):
        called["run"] += 1
        return _Run(stdout=json.dumps({"ok": True}))
    monkeypatch.setattr(sf.subprocess, "run", _run)
    monkeypatch.setattr(sf, "_flow_record", lambda ok, target: None)
    ok = sf.send_content_to("ou_x", "open_id", '{"text":"hi"}', "text")
    assert ok is False and called["run"] == 0


def test_lark_cli_send_chat_parses_notice_and_json(monkeypatch):
    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    out = 'WARN some notice\n{\n  "ok": true,\n  "message_id": "om_9"\n}'
    monkeypatch.setattr(sf.subprocess, "run", lambda *a, **kw: _Run(stdout=out))
    ok, detail = sf._lark_cli_send_chat('{"text":"hi"}', "text", "oc_x")
    assert ok is True and detail == "om_9"


def test_lark_cli_send_chat_no_binary(monkeypatch):
    monkeypatch.setattr(sf, "find_lark_cli", lambda: None)
    ok, detail = sf._lark_cli_send_chat('{"text":"hi"}', "text", "oc_x")
    assert ok is False and "no lark-cli" in detail


# ── 2026-10-25 收口：lark-cli 网络类失败重试一次 ─────────────
_TRANSIENT = ('{\n  "ok": false,\n  "identity": "bot",\n  "error": {\n'
              '    "type": "network",\n    "subtype": "transport",\n'
              '    "message": "API transport error"\n  }\n}')
_OK_OUT = '{\n  "ok": true,\n  "message_id": "om_retry"\n}'


def test_send_content_retries_transient_network(monkeypatch, capsys):
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        return _Run(stdout=_TRANSIENT if calls["n"] == 1 else _OK_OUT)

    monkeypatch.setattr(sf, "_send_openapi",
                        lambda *a, **kw: (False, "openapi_fallback: err"))
    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    monkeypatch.setattr(sf.subprocess, "run", fake_run)
    monkeypatch.setattr(sf.time, "sleep", lambda s: None)
    monkeypatch.setattr(sf, "_flow_record", lambda ok, target: None)
    ok = sf.send_content('{"text":"hi"}', "text")
    assert ok is True and calls["n"] == 2  # 第一次 network，重试成功


def test_lark_cli_chat_retries_transient_network(monkeypatch):
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        return _Run(stdout=_TRANSIENT if calls["n"] == 1 else _OK_OUT)

    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    monkeypatch.setattr(sf.subprocess, "run", fake_run)
    monkeypatch.setattr(sf.time, "sleep", lambda s: None)
    ok, detail = sf._lark_cli_send_chat('{"text":"hi"}', "text", "oc_x")
    assert ok is True and detail == "om_retry" and calls["n"] == 2


def test_lark_cli_retry_exhausted_returns_last(monkeypatch):
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        return _Run(stdout=_TRANSIENT)

    monkeypatch.setattr(sf, "find_lark_cli", lambda: r"C:\fake\lark-cli.exe")
    monkeypatch.setattr(sf.subprocess, "run", fake_run)
    monkeypatch.setattr(sf.time, "sleep", lambda s: None)
    ok, detail = sf._lark_cli_send_chat('{"text":"hi"}', "text", "oc_x")
    assert ok is False and calls["n"] == 2
