# -*- coding: utf-8 -*-
"""api_factor P1 契约测试：目录/健康/单因子/compute 校验与缓存。"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_factor  # noqa: E402


class _H:
    def __init__(self, body: bytes = b""):
        self.rfile = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}
        self.client_address = ("127.0.0.1", 12345)
        self.code = None
        self.payload = None

    def _json(self, obj, code=200):
        self.code = code
        self.payload = obj


def _post(h, obj):
    body = json.dumps(obj).encode("utf-8")
    h.rfile = io.BytesIO(body)
    h.headers = {"Content-Length": str(len(body))}
    api_factor.handle_post(h, "/api/factor/compute")


def test_catalog_and_health():
    h = _H()
    api_factor.handle_get(h, "/api/factor/health")
    assert h.code == 200
    assert h.payload["ok"] is True
    assert h.payload["total"] == 288
    assert h.payload["worker"] is True
    assert h.payload["catalog_version"]

    h = _H()
    api_factor.handle_get(h, "/api/factor/catalog")
    assert h.code == 200
    data = h.payload["data"]
    assert data["total"] == 288
    assert len(data["factors"]) == 288
    assert data["factors"][0]["status"] == "active"


def test_catalog_filters_and_single():
    h = _H()
    api_factor.handle_get(h, "/api/factor/catalog", "family=Tushare-动量&q=return_5d")
    assert h.code == 200
    rows = h.payload["data"]["factors"]
    assert any(r["name"] == "return_5d" for r in rows)

    h = _H()
    api_factor.handle_get(h, "/api/factor/catalog/return_5d")
    assert h.code == 200
    assert h.payload["data"]["factor"]["name"] == "return_5d"

    h = _H()
    api_factor.handle_get(h, "/api/factor/catalog/no_such_factor")
    assert h.code == 404


def test_compute_validation_and_cache(monkeypatch):
    api_factor._CACHE.clear()
    calls = []

    def fake_worker(payload):
        calls.append(payload)
        return {
            "ok": True,
            "factor_lib_version": "1.2.0",
            "rows": [["20260106", "000001.SZ", "return_5d", 0.01]],
            "count": 1,
            "truncated": False,
        }

    monkeypatch.setattr(api_factor, "_run_worker", fake_worker)
    req = {
        "factors": ["return_5d"],
        "start": "20260105",
        "end": "20260106",
        "codes": ["000001.SZ"],
        "limit": 10,
    }
    h = _H()
    _post(h, req)
    assert h.code == 200, h.payload
    assert h.payload["data"]["count"] == 1
    assert h.payload["data"]["cache_hit"] is False

    h2 = _H()
    _post(h2, req)
    assert h2.code == 200
    assert h2.payload["data"]["cache_hit"] is True
    assert len(calls) == 1

    bad = _H()
    _post(bad, {"factors": ["no_such_factor"], "start": "20260105", "end": "20260106"})
    assert bad.code == 400

    bad_date = _H()
    _post(bad_date, {"factors": ["return_5d"], "start": "20260106", "end": "20260105"})
    assert bad_date.code == 400
