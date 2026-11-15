# -*- coding: utf-8 -*-
"""I1b §3.3 新域 4 页：路由契约 + 判定诚实性 + 转义 + 降级路径。

验收替代说明：本机 8080/8091 无监听，`4 个 URL 均 200` 无法走 HTTP 实测，
本测试用 FakeHandler 直接驱动 handle_get，断言同样的状态码与正文契约。
**它不等于线上 200 验收**，该项仍列为交付报告的验收缺口。
"""
import io
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_chanlun_domain as CD  # noqa: E402


class _Fake:
    def __init__(self):
        self.code = None
        self.headers = {}
        self.buf = io.BytesIO()

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self.buf


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, d):
        if d.strip():
            self.parts.append(d.strip())


def _get(path):
    h = _Fake()
    CD.handle_get(h, path)
    return h, h.buf.getvalue().decode("utf-8", "replace")


def _text(html):
    p = _Text()
    p.feed(html)
    return " ".join(p.parts)


@pytest.mark.parametrize("path", ["/chanlun/", "/chanlun/radar",
                                  "/chanlun/action", "/chanlun/learn"])
def test_four_routes_return_200_with_domain_shell(path):
    h, html = _get(path)
    assert h.code == 200, path
    assert h.headers["Content-Type"].startswith("text/html")
    assert h.headers["Cache-Control"] == "no-store"
    assert int(h.headers["Content-Length"]) == len(h.buf.getvalue())
    assert "<script src=\"/static/nav.js\"" in html  # pill_nav 提供
    assert "/static/tokens.css" in html
    assert "🧭 缠论" in _text(html)
    assert "举证不裁决" in _text(html)


def test_bare_domain_redirects_and_unknown_path_404():
    h, _ = _get("/chanlun")
    assert h.code == 302
    assert h.headers["Location"] == "/chanlun/"
    evil = "/chanlun/<script>alert(1)</script>"
    h2, html2 = _get(evil)
    assert h2.code == 404
    assert "<script>alert(1)</script>" not in html2
    assert "&lt;script&gt;" in html2


def test_two_pages_with_real_pipeline_verdict_show_conclusion():
    """today / radar 有判定：必须出现「一句话结论」+ asof 徽章。"""
    for path in ("/chanlun/", "/chanlun/radar"):
        _h, html = _get(path)
        assert "一句话结论" in _text(html), path
        assert "ui-conc-text" in html, path
        assert "ui-asof" in html, path


@pytest.mark.parametrize("path", ["/chanlun/action", "/chanlun/learn"])
def test_two_pages_without_verdict_do_not_fake_a_conclusion(path):
    """闸门纪律：缺判定的两页不许硬凑一句话结论，必须如实写明「无管线判定」。"""
    _h, html = _get(path)
    assert "一句话结论" not in _text(html), path
    assert "本页无管线判定，不产出结论" in _text(html), path
    assert "数据状态与边界" in _text(html), path


def test_empty_pool_uses_honest_path(monkeypatch):
    monkeypatch.setattr(CD, "_load_pool", lambda: [])
    monkeypatch.setattr(CD, "_load_paper", lambda: {})
    monkeypatch.setattr(CD, "_load_brief_rows", lambda: (None, None))
    _h, html = _get("/chanlun/")
    txt = _text(html)
    assert "池文件为空或缺失" in txt
    assert "数据日未知" in txt


def test_read_failure_does_not_invent_data(monkeypatch):
    def boom():
        raise OSError("fixture read failure")

    monkeypatch.setattr(CD, "_load_paper", boom)
    _h, html = _get("/chanlun/action")
    assert "本页不编造结论" in _text(html)
    assert "数据日未知" in _text(html)


def test_dynamic_values_are_escaped(monkeypatch):
    bad = [{"detect_date": "X", "code": "<img src=x onerror=alert(1)>",
            "name": "<script>bad()</script>", "type": "1卖", "tbe": True,
            "tradable": False}]
    monkeypatch.setattr(CD, "_load_pool", lambda: bad)
    monkeypatch.setattr(CD, "_load_paper", lambda: {})
    monkeypatch.setattr(CD, "_load_brief_rows", lambda: (None, None))
    _h, html = _get("/chanlun/radar")
    assert "<script>bad()</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_render_page_propagates_real_errors():
    """handle_get 会降级吞异常；直接调 render_page 不能吞，避免假绿。"""
    for key in ("cl-today", "cl-radar", "cl-action", "cl-learn"):
        body = CD.render_page(key)
        assert isinstance(body, bytes) and len(body) > 500, key
    with pytest.raises(KeyError):
        CD.render_page("not-a-key")


def test_learn_page_asof_comes_from_data_not_mtime(monkeypatch):
    """账本行内无日期字段 → 数据日未知，绝不拿文件 mtime 顶替。"""
    monkeypatch.setattr(CD, "_load_learn", lambda: [
        {"file": "ledger.jsonl", "rows": 3, "day": None},
        {"file": "czsc_ledger.jsonl", "rows": 1, "day": None},
        {"file": "verify.jsonl", "rows": 2, "day": None}])
    _h, html = _get("/chanlun/learn")
    assert "数据日未知" in _text(html)
    assert "ui-asof--unknown" in html
