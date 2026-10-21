# -*- coding: utf-8 -*-
"""WO-LEGION-S22 军团指挥界面套件测试：快照聚合 + HTTP 端点 + dispatch 走门面。

承重：快照只读（不写任何真源）；dispatch 唯一写路径只落 bridge_inbox inbox/
（tmp 目录注入）；静态页路由白名单校验。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402
from legion_command import snapshot as snap  # noqa: E402
from legion_command.serve import build_server  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    inbox_gateway.set_store(tmp_path)
    yield tmp_path
    inbox_gateway._DEFAULT_STORE = None


# ---------------------------------------------------------------- 快照聚合

def test_snapshot_real_org(tmp_path):
    data = snap.build_snapshot()
    assert data["ok"] is True
    assert data["schema_version"] == "1.0"
    s = data["summary"]
    assert s["departments"] >= 13
    assert s["agents"] >= 25  # org 在编 29（hr 55 档含编外，org.json 为真源）
    names = [d["name"] for d in data["departments"]]
    assert any("研发" in n for n in names)
    assert data["chief"]["agent_id"] == "A0"


def test_snapshot_missing_org_fail_visible(tmp_path):
    data = snap.build_snapshot(platform_dir=tmp_path)
    assert data["ok"] is False
    assert "org.json" in data["error"]


def test_snapshot_readonly(tmp_path):
    before = (_PLATFORM / "registry" / "org.json").read_bytes()
    snap.build_snapshot()
    after = (_PLATFORM / "registry" / "org.json").read_bytes()
    assert before == after


# ---------------------------------------------------------------- HTTP 端点

@pytest.fixture()
def server(store):
    srv = build_server(port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()


def _post(base, path, obj):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def test_http_health_and_snapshot(server):
    st, body = _get(server, "/api/health")
    assert st == 200 and json.loads(body)["ok"] is True
    st, body = _get(server, "/api/snapshot")
    data = json.loads(body)
    assert data["ok"] is True and data["summary"]["departments"] >= 13


def test_http_index_and_static(server):
    st, body = _get(server, "/")
    assert st == 200 and b"\xe6\x8c\x87\xe6\x8c\xa5" in body  # 「指挥」UTF-8
    st, body = _get(server, "/static/map.html")
    assert st == 200 and len(body) > 1000
    st, body = _get(server, "/static/meeting.html")
    assert st == 200


def test_http_static_whitelist_blocks_traversal(server):
    try:
        urllib.request.urlopen(server + "/static/..%2Fserve.py", timeout=10)
        blocked = False
    except urllib.error.HTTPError as e:
        blocked = e.code == 404
    assert blocked


def test_http_dispatch_via_gateway_inbox(server, store):
    env = _post(server, "/api/dispatch",
                {"type": "work", "title": "Z5 对照实验",
                 "attendees": ["A0", "R2"], "note": "会终指令测试"})
    assert env["ok"] is True
    msg_id = env["data"]["id"]
    files = list((store / "inbox").glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["kind"] == "task_request"
    assert rec["source"] == "warroom"
    assert rec["payload"]["order_type"] == "work"
    assert rec["payload"]["attendees"] == ["A0", "R2"]


def test_http_dispatch_idempotent(server, store):
    body = {"type": "study", "title": "同题重发", "attendees": []}
    e1 = _post(server, "/api/dispatch", body)
    e2 = _post(server, "/api/dispatch", body)
    assert e1["data"]["id"] == e2["data"]["id"]
    assert e2["data"]["replayed"] is True


def test_http_dispatch_bad_type(server, store):
    try:
        _post(server, "/api/dispatch", {"type": "root", "title": "x"})
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 400
    assert raised
    assert not list((store / "inbox").glob("*.json"))
