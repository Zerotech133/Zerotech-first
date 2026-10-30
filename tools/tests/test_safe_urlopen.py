# -*- coding: utf-8 -*-
"""safe_urlopen 单测：scheme allowlist + 透传。"""
import importlib.util
import urllib.request
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "safe_urlopen.py"


def _load():
    spec = importlib.util.spec_from_file_location("safe_urlopen_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_https_and_http_allowed(monkeypatch):
    m = _load()
    calls = []

    def fake(url, *a, **k):
        calls.append((url, a, k))
        return "ok"

    monkeypatch.setattr(m.urllib.request, "urlopen", fake)
    assert m.safe_urlopen("https://example.com/x") == "ok"
    assert m.safe_urlopen("http://127.0.0.1:8091/health", timeout=3) == "ok"
    req = urllib.request.Request("https://example.com/y")
    assert m.safe_urlopen(req, timeout=1) == "ok"
    assert len(calls) == 3


def test_file_scheme_rejected(monkeypatch):
    m = _load()
    monkeypatch.setattr(m.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")))
    for bad in ("file:///etc/passwd", "ftp://x/y", "gopher://x", "data:text/plain,hi"):
        try:
            m.safe_urlopen(bad)
            raise AssertionError("应拒绝: %s" % bad)
        except ValueError:
            pass


def test_urlsplit_import():
    m = _load()
    assert m.ALLOWED_SCHEMES == ("http", "https")
