# -*- coding: utf-8 -*-
"""hermes_health 单测：登录门识别 / 会话优先 / 失败软降级（2026-10-31）。"""
import sys
import urllib.error
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from legion_command import hermes_health as hh  # noqa: E402


class _Resp:
    def __init__(self, status, location=""):
        self.status = status
        self.headers = {"Location": location} if location else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_classify_200_alive():
    r = hh.classify(200)
    assert r["alive"] is True and r["auth_gated"] is False and r["status"] == 200


def test_classify_302_login_is_auth_gated_alive():
    r = hh.classify(302, "/login?next=/")
    assert r["alive"] is True
    assert r["auth_gated"] is True


def test_classify_401_is_auth_gated_alive():
    r = hh.classify(401)
    assert r["alive"] is True and r["auth_gated"] is True


def test_classify_500_and_error_are_down():
    assert hh.classify(500)["alive"] is False
    assert hh.classify(None, error=OSError("conn refused"))["alive"] is False


def test_probe_uses_injected_get_and_flags_login_gate(monkeypatch):
    monkeypatch.setattr(hh, "_auth_enabled", lambda: False)
    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _Resp(302, "/login?next=/")

    r = hh.probe("http://127.0.0.1:8787", timeout=1.0, http_get=fake_get)
    assert calls and calls[0][0].endswith("/")
    assert r["alive"] is True and r["auth_gated"] is True


def test_probe_connection_error_is_down(monkeypatch):
    monkeypatch.setattr(hh, "_auth_enabled", lambda: False)

    def fake_get(url, timeout):
        raise urllib.error.URLError("refused")

    r = hh.probe(http_get=fake_get)
    assert r["alive"] is False and r["auth_gated"] is False
