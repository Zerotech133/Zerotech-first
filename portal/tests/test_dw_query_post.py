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
    block = src.split("path.startswith('/api/dw/')")[1][:400]
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
