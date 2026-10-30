# -*- coding: utf-8 -*-
"""F1 写接口防护：同源校验 + 浏览器写 token（CSRF 回归）。"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402
from legion_command.serve import build_server  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    inbox_gateway.set_store(tmp_path)
    yield tmp_path
    inbox_gateway._DEFAULT_STORE = None


@pytest.fixture()
def server(store):
    srv = build_server(port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(base, path, obj, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"),
        headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _token(base, origin=None):
    h = {"Origin": origin} if origin else {}
    req = urllib.request.Request(base + "/api/zhaoyun/write-token", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_token_endpoint_rejects_cross_origin(server):
    st, body = _token(server, origin="http://evil.example")
    assert st == 403 and body["error"] == "forbidden_origin"


def test_token_endpoint_serves_same_origin(server):
    origin = server  # 与 Host 同源
    st, body = _token(server, origin=origin)
    assert st == 200 and body["ok"] is True and len(body["token"]) >= 20


def test_dispatch_rejects_cross_origin(server):
    st, body = _post(server, "/api/dispatch", {"type": "work", "title": "x"},
                     {"Origin": "http://evil.example"})
    assert st == 403 and body["error"] == "forbidden_origin"


def test_dispatch_browser_requires_token(server):
    st, body = _post(server, "/api/dispatch", {"type": "work", "title": "x"},
                     {"Origin": server})
    assert st == 403 and body["error"] == "invalid_write_token"


def test_dispatch_same_origin_with_token_ok(server, store):
    _, tok = _token(server, origin=server)
    st, body = _post(server, "/api/dispatch", {"type": "work", "title": "F1 回归"},
                     {"Origin": server, "X-DSH-Token": tok["token"]})
    assert st == 200 and body["ok"] is True
    assert list((store / "inbox").glob("*.json"))


def test_dispatch_non_browser_still_works(server, store):
    """无 Origin 的本地客户端（curl/测试）保持兼容。"""
    st, body = _post(server, "/api/dispatch", {"type": "study", "title": "no-origin"})
    assert st == 200 and body["ok"] is True
