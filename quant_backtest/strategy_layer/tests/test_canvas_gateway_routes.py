# -*- coding: utf-8 -*-
"""Phase 3 路由收口测试：Canvas 静态页/JS + Paper Exchange 只读路由。

离线 Handler 替身直调包装后的 do_GET，不监听端口/不写生产数据。
"""

from __future__ import annotations

import json
import os

from strategy_layer import dashboard_api as da

_HERE = os.path.dirname(os.path.abspath(da.__file__))


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


def _call_get(path):
    cls = type("Handler", (_FakeHandler,), {"do_GET": _FakeHandler.do_GET,
                                             "do_POST": _FakeHandler.do_POST})
    da.register_routes(cls)
    h = cls(path)
    h.do_GET()
    return h


def test_canvas_html_route():
    h = _call_get("/canvas")
    assert h.status == 200
    assert h.headers["content-type"] == "text/html; charset=utf-8"
    assert h.headers["cache-control"] == "no-store"
    text = h.written.decode("utf-8")
    assert "strategy_canvas.js" in text
    assert h.orig_called is False


def test_canvas_alias_and_js():
    assert _call_get("/strategy/canvas").status == 200
    h = _call_get("/strategy_canvas.js")
    assert h.status == 200
    assert h.headers["content-type"] == "application/javascript; charset=utf-8"
    assert "fetch(" in h.written.decode("utf-8")
    assert _call_get("/strategy/strategy_canvas.js").status == 200


def test_paper_exchange_health_route():
    h = _call_get("/paper_exchange/health")
    assert h.status == 200
    assert h.headers["content-type"] == "application/json; charset=utf-8"
    data = json.loads(h.written.decode("utf-8"))
    assert isinstance(data, dict)
    # 只读边界应显式声明；不同数据环境只保证结构存在
    assert "readonly" in data or "data" in data


def test_paper_exchange_unknown_path_404():
    h = _call_get("/paper_exchange/not-a-route")
    assert h.status == 404
    assert h.headers["content-type"] == "application/json; charset=utf-8"


def test_unknown_path_falls_back():
    h = _call_get("/some/other/route")
    assert h.orig_called is True
