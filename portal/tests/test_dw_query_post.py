# -*- coding: utf-8 -*-
"""POST /api/dw/query 接线回归（2026-11-02 修复 2/3 参数不匹配）。"""
import io
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

import api_dw_registry  # noqa: E402


def test_dispatch_passes_body_three_args():
    src = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    block = src.split("path.startswith('/api/dw/')")[1][:600]
    assert "_read_dw_body(self)" in block
    assert "api_dw_registry.handle_post(self, path, _raw)" in block


class _FakeHandler:
    def __init__(self, body: bytes):
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.out = []

    def _json(self, obj, status=200):
        self.out.append((status, obj))


def test_handle_post_sqlite_query_ok():
    body = json.dumps({"id": "ts_daily", "limit": 2, "count": True}).encode()
    h = _FakeHandler(body)
    api_dw_registry.handle_post(h, "/api/dw/query", h.rfile.read(int(h.headers["Content-Length"])))
    assert h.out, "no response"
    status, obj = h.out[-1]
    assert status == 200, (status, obj)
    assert obj.get("state") == "ok" or "rows" in obj


def test_handle_post_unknown_path_404():
    h = _FakeHandler(b"{}")
    api_dw_registry.handle_post(h, "/api/dw/other", b"{}")
    assert h.out[-1][0] == 404

def test_read_dw_body_limit_and_negative():
    import io
    import portal_api

    class H:
        def __init__(self, body):
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)

    ok, body, err = portal_api._read_dw_body(H(b'{"id":"ts_daily"}'))
    assert ok and body.startswith(b'{"id"') and err == ""

    class H2:
        headers = {"Content-Length": str(portal_api._DW_BODY_MAX + 1)}
        rfile = io.BytesIO(b"")

    ok2, body2, err2 = portal_api._read_dw_body(H2())
    assert not ok2 and err2 == "body too large"

    class H3:
        headers = {"Content-Length": "-5"}
        rfile = io.BytesIO(b"")

    ok3, body3, err3 = portal_api._read_dw_body(H3())
    assert not ok3 and err3 == "body too large"
