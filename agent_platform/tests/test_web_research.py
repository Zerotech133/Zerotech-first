# -*- coding: utf-8 -*-
"""只读 web 取证测试（WO-WEB-RESEARCH）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "research_web", str(_WT / "agent_platform" / "research" / "web.py"))
RW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RW)


def _sandbox(tmp_path, monkeypatch, domains=("example.com",), limit="2"):
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"domains": list(domains)}), encoding="utf-8")
    monkeypatch.setattr(RW, "ALLOWLIST", allow)
    monkeypatch.setattr(RW, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(RW, "RESEARCH", tmp_path / "research")
    monkeypatch.setattr(RW, "CITATIONS", tmp_path / "research" / "c.jsonl")
    monkeypatch.setattr(RW, "BUDGET", tmp_path / "research" / "b.json")
    monkeypatch.setenv("ZHAOYUN_RESEARCH_DAILY_LIMIT", limit)
    monkeypatch.setattr(RW.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))])


def test_validate_url_blocks(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    assert RW.validate_url("file:///etc/passwd")["error"] == "scheme_not_allowed"
    assert RW.validate_url("http://127.0.0.1/x")["error"] in (
        "host_not_allowed", "domain_not_allowlisted")
    assert RW.validate_url("http://localhost/x")["error"] == "host_not_allowed"
    assert RW.validate_url("http://evil.org/x")["error"] == "domain_not_allowlisted"
    ok = RW.validate_url("https://example.com/a")
    assert ok["ok"] is True and ok["host"] == "example.com"


def test_validate_url_blocks_private_resolution(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(RW.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 80))])
    rep = RW.validate_url("https://example.com/a")
    assert rep["ok"] is False and "private" in rep["error"]


def test_fetch_cache_and_budget(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        return ("<html><head><title>测试站点</title></head><body><h1>标题</h1>"
                "<p>这是一段正文内容。</p><script>x=1</script></body></html>")

    monkeypatch.setattr(RW, "_http_get", fake_get)
    first = RW.fetch("https://example.com/page")
    assert first["ok"] is True and first["cached"] is False
    assert first["title"] == "测试站点" and "正文内容" in first["text"]
    assert first["citation"]["sha256"] and first["citation"]["url"].startswith("https://")
    assert calls["n"] == 1
    second = RW.fetch("https://example.com/page")
    assert second["ok"] and second["cached"] is True and calls["n"] == 1
    third = RW.fetch("https://example.com/page", force=True)
    assert third["cached"] is False and calls["n"] == 2
    fourth = RW.fetch("https://example.com/page", force=True)
    assert fourth["ok"] is False and fourth["error"] == "budget_exceeded"


def test_citations_and_budget_report(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(RW, "_http_get", lambda url: "<title>t</title><p>body</p>")
    RW.fetch("https://example.com/x")
    rep = RW.recent_citations()
    assert rep["count"] == 1 and rep["items"][0]["title"] == "t"
    assert rep["budget"]["limit"] == 2 and rep["budget"]["remaining"] == 1
    assert "example.com" in rep["allowlist"]
