# -*- coding: utf-8 -*-
"""8096 dispatch：HTTPError 400 必须保留 8093 错误体，不再误报“不可达”。"""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))

import api_legion_ops_v2 as m  # noqa: E402


def test_http_400_body_is_preserved(monkeypatch):
    events = []
    monkeypatch.setattr(m, "heartbeat", lambda *a, **k: events.append(a))

    def boom(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"ok":false,"error":"desc must not be empty"}'))

    monkeypatch.setattr(m, "safe_urlopen", boom)
    res = m.dispatch({"agent": "D1", "desc": "x"})
    assert res["ok"] is False
    assert "8093 HTTP 400" in res["err"]
    assert "desc must not be empty" in res["err"]
    assert not any("不可达" in str(x) for x in events)
