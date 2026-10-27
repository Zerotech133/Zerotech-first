# -*- coding: utf-8 -*-
"""E2E-01/02/03：服务冒烟（只读 GET；pytest importlib 模式下不跨模块 import）。"""
import json
import urllib.request


def _status(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:  # noqa: BLE001
        return None


def _json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def test_portal_8734_entry():
    assert _status("http://127.0.0.1:8734/") == 200


def test_portal_api_8091_health():
    assert _status("http://127.0.0.1:8091/health") == 200


def test_legion_8796_mcp_servers():
    data = _json("http://127.0.0.1:8796/api/zhaoyun/mcp")
    assert data.get("ok") is True
    names = [s.get("name") for s in data.get("servers") or []]
    assert "tushare-http" in names
    assert len(names) >= 4
