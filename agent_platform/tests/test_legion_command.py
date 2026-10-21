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
from legion_command.snapshot import build_snapshot  # noqa: E402
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
    assert data["schema_version"] == "1.1"  # S29 起 activity 结构升版
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


# ---------------------------------------------------------------- 会议室真模型通道

def test_http_chat_live_ok(server, monkeypatch):
    from gateway import llm_gateway

    class Stub:
        def chat(self, system, user, model=None):
            return "结论：先修心跳。", "normal"

    captured = {}

    def fake_chat(binding=None, system="", user=""):
        captured["binding"] = binding
        captured["user"] = user
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "结论：先修心跳。", "status": "normal"})

    monkeypatch.setattr(llm_gateway, "chat", fake_chat)
    env = _post(server, "/api/chat",
                {"agent": "A0", "system": "你是田丰", "user": "今日优先级？"})
    assert env["ok"] is True
    assert env["agent"] == "A0"
    assert env["binding"] == "glm/glm-5.3"
    assert captured["binding"] == "glm/glm-5.3"
    assert "今日优先级" in captured["user"]


def test_http_chat_degraded_and_failure_passthrough(server, monkeypatch):
    from gateway import llm_gateway

    def degraded(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=True, degraded=True,
                                         data={"text": "本地兜底", "status": "degraded"})

    monkeypatch.setattr(llm_gateway, "chat", degraded)
    env = _post(server, "/api/chat", {"agent": "R2", "system": "s", "user": "u"})
    assert env["ok"] is True and env["degraded"] is True
    assert env["binding"] == "ollama/qwen3.5:9b"

    def dead(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                         error={"code": "llm_error", "message": "key missing"})

    monkeypatch.setattr(llm_gateway, "chat", dead)
    env2 = _post(server, "/api/chat", {"agent": "AI1", "system": "s", "user": "u"})
    assert env2["ok"] is False and env2["binding"] == "openai/gpt-6.0"


def test_http_chat_bad_agent(server):
    try:
        _post(server, "/api/chat", {"agent": "../etc", "system": "", "user": "x"})
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 400
    assert raised


def test_http_chat_unknown_agent_default_binding(server, monkeypatch):
    from gateway import llm_gateway
    seen = {}

    def fake(binding=None, system="", user=""):
        seen["b"] = binding
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "t", "status": "normal"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "DEV7", "system": "", "user": "u"})
    assert env["ok"] is True
    assert seen["b"] == "ollama/qwen3.5:9b"


def test_http_chat_glm_quota_fallback_to_local(server, monkeypatch):
    """glm 限额/故障 → 会议通道显式降级本地 9b（degraded + fallback 原因，不静默）。"""
    from gateway import llm_gateway
    calls = []

    def fake(binding=None, system="", user=""):
        calls.append(binding)
        if binding.startswith("glm/"):
            return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                             error={"code": "llm_error",
                                                    "message": "HTTP 429 限额"})
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "本地兜底答复",
                                               "status": "normal",
                                               "binding_resolved": binding})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "A0", "system": "s", "user": "u"})
    assert env["ok"] is True
    assert env["degraded"] is True
    assert env["fallback_from"] == "glm/glm-5.3"
    assert "429" in env["fallback_reason"]
    assert env["binding"] == "ollama/qwen3.5:9b"
    assert calls == ["glm/glm-5.3", "ollama/qwen3.5:9b"]


def test_http_chat_local_failure_no_fallback(server, monkeypatch):
    """本地档失败不伪造降级（fail-visible 原样透传）。"""
    from gateway import llm_gateway

    def fake(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                         error={"code": "llm_error", "message": "重试耗尽"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "R2", "system": "s", "user": "u"})
    assert env["ok"] is False
    assert env["error"]["code"] == "llm_error"


def test_http_chat_openai_channel_not_ready(server, monkeypatch):
    """openai 绑定未接 → 明确 channel_not_ready，不打 Ollama 兜底（避免误导 404）。"""
    from gateway import llm_gateway
    calls = []

    def fake(binding=None, system="", user=""):
        calls.append(binding)
        return llm_gateway.make_envelope(kind="llm_chat", ok=True, data={"text": "x"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "AI1", "system": "s", "user": "u"})
    assert env["ok"] is False
    assert env["error"]["code"] == "channel_not_ready"
    assert "Q12" in env["error"]["message"]
    assert calls == []


# ---------------------------------------------------------------- 在途归属映射（S29）

def test_activity_mapping_covers_real_tasks():
    """真实 tasks.json 的 doing 应全部可归属到部门（0 unmapped）。"""
    data = build_snapshot()
    act = data["activity"]
    assert act["rules_version"] == "1.0"
    if act["doing"]:
        assert act["unmapped"] == [], "存在未归属在途单：%s" % act["unmapped"]
        assert sum(len(v) for v in act["by_dept"].values()) == len(act["doing"])


def test_activity_maps_known_prefixes():
    data = build_snapshot()
    act = data["activity"]
    if not act["doing"]:
        pytest.skip("当前无在途单")
    for t in act["doing"]:
        assert t["dept"], "任务 %s 未归属" % t["key"]
    if any(t["key"].startswith("K-INTEL") for t in act["doing"]):
        assert "intel" in act["by_dept"]


def test_activity_mapping_does_not_touch_tasks_json():
    from pathlib import Path as _P
    p = _P(__file__).resolve().parents[2] / ".worktrees" / "tasks.json"
    before = p.read_bytes() if p.exists() else None
    build_snapshot()
    after = p.read_bytes() if p.exists() else None
    assert before == after


def test_map_task_rules():
    from legion_command.snapshot import _map_task
    assert _map_task("legion-1021093009", "盘前 weak 池三线扫描") == ("invest", "D1")
    assert _map_task("x", "每日复盘三问汇总") == ("ops", "A0A")
    assert _map_task("K-INTEL-20261018-RO", "") == ("intel", "")
    assert _map_task("K-ORG-180", "") == ("ops", "")
    assert _map_task("random-task", "无关键词") == ("", "")
