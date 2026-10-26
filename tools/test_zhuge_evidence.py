# -*- coding: utf-8 -*-
"""zhuge_evidence 单测（P0-2/P0-3）。

全部离线：SSRF/DNS 与 urllib 均用桩；不联网、不写文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_evidence as e  # noqa: E402


def test_source_tier():
    assert e.source_tier("https://www.gov.cn/a")["tier"] == 1
    assert e.source_tier("https://github.com/x")["tier"] == 2
    assert e.source_tier("https://arxiv.org/abs/1")["tier"] == 2
    assert e.source_tier("https://www.reuters.com/a")["tier"] == 3
    assert e.source_tier("https://example.org/a")["tier"] == 4
    assert e.source_tier("not-a-url")["tier"] == 5


def test_url_is_safe_denies_local_and_bad_scheme(monkeypatch):
    assert e.url_is_safe("http://127.0.0.1/x")[0] is False
    assert e.url_is_safe("http://192.168.1.1/x")[0] is False
    assert e.url_is_safe("http://localhost:8091/x")[0] is False
    assert e.url_is_safe("file:///C:/secret.txt")[0] is False
    assert e.url_is_safe("http://user:pass@example.com/x")[0] is False
    # 公网域名用桩解析
    monkeypatch.setattr(e, "_host_ips", lambda host: ["93.184.216.34"])
    ok, reason = e.url_is_safe("https://example.com/a")
    assert ok is True and reason == "ok"


def test_url_is_safe_denies_public_ip_resolving_private(monkeypatch):
    monkeypatch.setattr(e, "_host_ips", lambda host: ["10.0.0.5"])
    ok, reason = e.url_is_safe("https://evil.example/a")
    assert ok is False and reason == "non_public_ip_denied"


class FakeResp:
    status = 200

    def __init__(self, body: bytes, url: str = "https://www.gov.cn/a",
                 content_type: str = "text/html; charset=utf-8"):
        self._body = body
        self._url = url
        self.headers = {"Content-Type": content_type}

    def read(self, n=-1):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_html_parses_and_marks_official(monkeypatch):
    monkeypatch.setattr(e, "url_is_safe", lambda url: (True, "ok"))
    html = ("<html><head><title>官方通告</title><style>.x{}</style></head>"
            "<body><h1>标题</h1><p>正文内容 123</p><script>alert(1)</script></body></html>")
    monkeypatch.setattr(e.urllib.request, "urlopen",
                        lambda req, timeout=10: FakeResp(html.encode("utf-8")))
    res = e.fetch_url("https://www.gov.cn/a", max_chars=500)
    assert res["ok"] is True
    assert res["title"] == "官方通告"
    assert "正文内容 123" in res["text"]
    assert "alert(1)" not in res["text"]
    assert res["source_tier"]["tier"] == 1
    assert "抓取" in res["fetched_at"] or ":" in res["fetched_at"]
    assert res["read_only"] is True


def test_fetch_plain_text_and_truncation(monkeypatch):
    monkeypatch.setattr(e, "url_is_safe", lambda url: (True, "ok"))
    text = "A" * 1000
    monkeypatch.setattr(e.urllib.request, "urlopen",
                        lambda req, timeout=10: FakeResp(
                            text.encode("utf-8"), content_type="text/plain; charset=utf-8"))
    res = e.fetch_url("https://example.com/a", max_chars=300)
    assert res["ok"] is True and res["truncated"] is True
    assert len(res["text"]) == 300


def test_fetch_denied_and_failure(monkeypatch):
    monkeypatch.setattr(e, "url_is_safe", lambda url: (False, "private_ip_denied"))
    res = e.fetch_url("http://127.0.0.1/x")
    assert res["ok"] is False and res["error"]["code"] == "url_unsafe"

    monkeypatch.setattr(e, "url_is_safe", lambda url: (True, "ok"))

    def boom(req, timeout=10):
        raise OSError("network down")

    monkeypatch.setattr(e.urllib.request, "urlopen", boom)
    res2 = e.fetch_url("https://example.com/a")
    assert res2["ok"] is False and res2["error"]["code"] == "fetch_failed"


def test_cite_source_format():
    src = {"title": "官方通告", "url": "https://www.gov.cn/a",
           "final_url": "https://www.gov.cn/a", "fetched_at": "2026-10-27 06:40:00",
           "source_tier": {"label": "official"}}
    cite = e.cite_source(src, "S1")
    assert cite.startswith("[S1] 官方通告")
    assert "https://www.gov.cn/a" in cite and "抓取 2026-10-27" in cite
    assert "official" in cite


def test_validate_claims_requires_sources():
    bad = e.validate_claims([{"text": "事实主张", "source_ids": [], "confidence": "high"}],
                            [{"id": "S1"}])
    assert bad["ok"] is False
    assert any(i["code"] == "claim_without_source" for i in bad["issues"])
    good = e.validate_claims(
        [{"text": "事实主张", "source_ids": ["S1"], "confidence": "high"}],
        [{"id": "S1"}])
    assert good["ok"] is True


def test_validate_claims_unknown_id_and_bad_confidence():
    res = e.validate_claims(
        [{"text": "x", "source_ids": ["S9"], "confidence": "very-high"}],
        [{"id": "S1"}])
    codes = {i["code"] for i in res["issues"]}
    assert "source_id_missing" in codes and "confidence_invalid" in codes


def test_build_answer_citations_and_warnings():
    src = {"ok": True, "title": "官方通告", "url": "https://www.gov.cn/a",
           "fetched_at": "2026-10-27 06:40:00",
           "source_tier": {"label": "official"}}
    out = e.build_answer("结论", claims=[{"text": "x", "source_ids": [], "confidence": "high"}],
                         sources=[src])
    assert out["citations"] and "官方通告" in out["citations"][0]
    assert out["evidence_check"]["ok"] is False
    assert any("证据校验未通过" in w for w in out["warnings"])
    assert out["read_only"] is True
