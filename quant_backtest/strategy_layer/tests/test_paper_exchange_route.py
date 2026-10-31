# -*- coding: utf-8 -*-
"""Phase4 Paper Exchange 页面路由测试（离线 Handler 替身）。"""
from __future__ import annotations

import json

from strategy_layer import dashboard_api as da


class _FakeHandler:
    _dashboard_api_wrapped = False

    def __init__(self, path):
        self.path = path
        self.status = None
        self.headers = {}
        self.written = b""
        self.orig_called = False

    def do_GET(self):
        self.orig_called = True

    def do_POST(self):
        self.orig_called = True

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[str(key).lower()] = str(value)

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.written += data


def _get(path):
    cls = type("Handler", (_FakeHandler,), {"do_GET": _FakeHandler.do_GET,
                                             "do_POST": _FakeHandler.do_POST})
    da.register_routes(cls)
    h = cls(path)
    h.do_GET()
    return h


def test_page_and_js():
    h = _get("/paper_exchange_card")
    assert h.status == 200
    assert h.headers["content-type"] == "text/html; charset=utf-8"
    assert "paper_exchange.js" in h.written.decode("utf-8")
    assert h.orig_called is False

    h = _get("/paper_exchange.js")
    assert h.status == 200
    assert h.headers["content-type"] == "application/javascript; charset=utf-8"
    assert "paper_exchange" in h.written.decode("utf-8").lower()
    assert h.orig_called is False


def test_gateway_still_works():
    h = _get("/paper_exchange/health")
    assert h.status == 200
    assert h.headers["content-type"] == "application/json; charset=utf-8"
    data = json.loads(h.written.decode("utf-8"))
    assert isinstance(data, dict)


def test_unknown_falls_back():
    h = _get("/some/other")
    assert h.orig_called is True
