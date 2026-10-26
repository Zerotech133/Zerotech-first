"""test_os_council.py — 会议厅契约测试（S-1b）。

纪律：真执行渲染函数（最小 DOM stub，WO-OS-30 判例）；数据生成器不碰真实
计划任务盘面（probe_seats 单测用 monkeypatch）。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_council_data as gcd
import council_ops as council


def test_status_light_mapping():
    assert gcd._status_light("修订已接受，门禁口径已定") == "green"
    assert gcd._status_light("分歧中") == "red"
    assert gcd._status_light("") == "yellow"  # 有交版权当修订中


def test_seat_of_rules():
    assert gcd._seat_of("", "互审单001_赵云侧回执2_2026-10-22.md") == "zhaoyun"
    assert gcd._seat_of("", "互审单001_回执3_与多方会议页设计.md") == "tianfeng"
    assert gcd._seat_of("", "评审_OS统一调度方案_2026-10-22.md") == "zhaoyun"
    assert gcd._seat_of("", "毫不相关文档.md") == "unknown"


def test_scan_excludes_non_council(tmp_path, monkeypatch):
    """P1 到点验证批等交付回执不得混入会议发言流。"""
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path / "d"))
    monkeypatch.setattr(gcd, "REPO", str(tmp_path / "d" / ".."))
    d = tmp_path / "d"; d.mkdir()
    (d / "2026-10-22_互审单001_回执3_x.md").write_text("# 回执3", encoding="utf-8")
    (d / "2026-10-22_P1到点验证批_回执.md").write_text("# P1", encoding="utf-8")
    (d / "2026-10-22_普通交付.md").write_text("# 普通", encoding="utf-8")
    topics = gcd.scan_topics()
    files = [s["file"] for t in topics for s in t["speeches"]]
    assert any("互审单001_回执3" in f for f in files)
    assert not any("P1" in f for f in files)
    assert not any("普通" in f for f in files)


def test_probe_seats_honest_unverifiable(monkeypatch):
    """不可测席位诚实标 unknowable，不得假装在线。"""
    def fake_run(*a, **k):
        class R: returncode = 0; stdout = b"True"
        return R()
    monkeypatch.setattr(gcd.subprocess, "run", fake_run)
    seats = gcd.probe_seats()
    zhuge = [s for s in seats if s["seat"] == "zhuge"][0]
    assert zhuge["verifiable"] is False and zhuge["online"] is False
    tf = [s for s in seats if s["name"].startswith("田丰")][0]
    assert tf["verifiable"] is True and tf["online"] is True


def test_payload_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path))
    monkeypatch.setattr(gcd, "REPO", str(tmp_path))
    monkeypatch.setattr(gcd, "OUT_PATH", str(tmp_path / "out" / "council.json"))
    monkeypatch.setattr(gcd, "scan_topics", lambda: [])
    monkeypatch.setattr(gcd, "probe_seats", lambda: [])
    assert gcd.main() == 0
    payload = json.load(open(tmp_path / "out" / "council.json", encoding="utf-8"))
    assert payload["schema"] == "os_council/v2"
    assert "generated_at" in payload and "note" in payload
    assert "stage_view" in payload and "route_suggestions" in payload  # v2 新增


def test_stage_view_and_routes(tmp_path, monkeypatch):
    """六阶段视图与路由建议联动（v2 核心）。"""
    monkeypatch.setattr(gcd, "TOPIC_REGISTRY", [
        {"topic": "试点任务", "tags": ["dev"], "stage": "表态",
         "originator": "laotian", "summary": "SOP 试点"}])
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path))
    view = gcd.build_stage_view()
    assert view[0]["stage_index"] == 1 and view[0]["stages"][0] == "立题"
    # dev 标签应产出路由建议（lead=workbuddy）
    routes = gcd.route_suggestions()
    assert routes and routes[0]["route"]["lead"] == "workbuddy"


@pytest.fixture
def isolated_council(tmp_path, monkeypatch):
    """仅在临时数据根启动动态 loopback 端口，不启动 main() 的每日调度。"""
    data = tmp_path / "council_data"
    data.mkdir()
    paths = {
        "DATA_DIR": data,
        "REGISTRY_SNIPPET": data / "council_new_topics.json",
        "PREFLIGHT": data / "council_preflight.json",
        "MESSAGES": data / "council_messages.jsonl",
        "SCHEDULED": data / "council_scheduled.jsonl",
        "UPLOAD_DIR": data / "council_uploads",
        "ARCHIVE_ROOT": data / "council_meetings",
        "IDEA_POOL": data / "council_ideas.jsonl",
        "THEME_PATH": data / "council_theme.json",
        "OUTBOX": data / "council_outbox.jsonl",
    }
    for name, path in paths.items():
        assert path.is_relative_to(tmp_path)
        monkeypatch.setattr(council, name, str(path))
    monkeypatch.setattr(gcd, "build_stage_view", lambda: [])
    origin = "http://127.0.0.1:8080"
    password = "isolated-council-test-password"
    server = council.CouncilServer(("127.0.0.1", 0), password, frozenset({origin}))
    assert server.server_address[0] == "127.0.0.1" and server.server_address[1] != 8798
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            request_headers = dict(headers or {})
            if payload is not None:
                request_headers.setdefault("Content-Type", "application/json")
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, response.headers
        finally:
            connection.close()

    try:
        yield request, paths, origin, password
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


def test_council_peer_guard_rejects_non_loopback():
    """socket 只绑定 loopback；用轻量 Handler stub 验证伪造的远端 peer。"""
    handler = object.__new__(council.Handler)
    handler.client_address = ("192.0.2.1", 12345)
    replies = []
    handler._json = lambda body, status=200: replies.append((status, body))
    assert handler._local_peer() is False
    assert replies == [(403, {"error": "forbidden"})]
    handler.client_address = ("127.0.0.1", 12345)
    assert handler._local_peer() is True
    assert len(replies) == 1


def test_council_auth_origin_and_confirm_isolated(isolated_council):
    request, paths, origin, password = isolated_council
    login = "/api/council/auth/login"
    confirm = "/api/council/preflight/confirm"
    config = {"request_id": str(uuid.uuid4()), "title": "隔离安全议题", "scope": "仅测临时数据",
              "mode": "线上", "goal": "验证配置", "humans": "测试用户",
              "candidates": ["zhaoyun"], "confirmed": True}
    allowed = {"Origin": origin}

    for path in ("/api/council/state", "/api/council/search?q=secret"):
        status, body, _ = request("GET", path)
        assert (status, body) == (401, {"error": "unauthorized"})
    for path, payload in ((confirm, config), ("/api/council/new", {"title": "绕过确认"}),
                          ("/api/council/message", {"text": "未授权写入"})):
        status, body, _ = request("POST", path, payload, allowed)
        assert (status, body) == (401, {"error": "unauthorized"})

    forged = {"Origin": "http://evil.example:8080", "Referer": origin + "/os_council.html"}
    for headers in (forged, {"Referer": origin + "/os_council.html"}):
        for path, payload in ((login, {"password": password}), (confirm, config)):
            status, body, response_headers = request("POST", path, payload, headers)
            assert (status, body) == (403, {"error": "forbidden origin"})
            assert response_headers.get("Set-Cookie") is None
            assert response_headers.get("Access-Control-Allow-Origin") is None
        status, body, _ = request("OPTIONS", confirm, headers=headers)
        assert (status, body) == (403, {"error": "forbidden origin"})
    status, body, headers = request("OPTIONS", confirm, headers=allowed)
    assert status == 204 and body is None
    assert headers.get("Access-Control-Allow-Origin") == origin
    assert headers.get("Access-Control-Allow-Credentials") == "true"

    status, body, headers = request("POST", login, {"password": "wrong-password"}, allowed)
    assert (status, body) == (401, {"error": "invalid credentials"})
    assert headers.get("Set-Cookie") is None
    status, body, headers = request("POST", login, {"password": password}, allowed)
    assert (status, body) == (200, {"ok": True})
    cookie = headers.get("Set-Cookie")
    assert cookie and cookie.startswith(council.SESSION_COOKIE + "=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    session = {"Origin": origin, "Cookie": cookie.split(";", 1)[0]}
    for headers in ({**forged, "Cookie": session["Cookie"]},
                    {"Referer": origin + "/os_council.html", "Cookie": session["Cookie"]}):
        status, body, _ = request("POST", confirm, config, headers)
        assert (status, body) == (403, {"error": "forbidden origin"})
    status, body, _ = request("GET", "/api/council/state", headers={"Cookie": session["Cookie"]})
    assert status == 200 and body["extra_topics"] == [] and body["messages"] == []
    assert body["archives"] == [] and body["tasks"] == []

    status, body, _ = request("POST", confirm, {**config, "confirmed": "true"}, session)
    assert status == 400 and "本人确认" in body["error"]
    status, body, _ = request("POST", confirm, config, session)
    assert status == 200 and body["ok"] is True and body["config_recorded"] is True
    assert body.get("duplicate") is not True
    created = body["topic"]
    assert created["request_id"] == config["request_id"]

    status, body, _ = request("GET", "/api/council/state", headers={"Cookie": session["Cookie"]})
    assert status == 200 and body["extra_topics"] == [created]
    assert len(body["messages"]) == 1 and body["messages"][0]["kind"] == "config"

    status, body, _ = request("POST", confirm, config, session)
    assert status == 200 and body["duplicate"] is True and body["topic"] == created
    assert body["config_recorded"] is True
    status, body, _ = request("POST", confirm, {**config, "scope": "偷偷变更范围"}, session)
    assert status == 409 and "配置已变更" in body["error"]
    for payload in ({**config, "request_id": str(uuid.uuid4()), "meeting_kind": "schedule"},
                    {**config, "request_id": str(uuid.uuid4()), "scheduled_for": "2030-01-01 10:00"}):
        status, body, _ = request("POST", confirm, payload, session)
        assert status == 409 and "预约" in body["error"]
    for path, payload in (("/api/council/new", {"title": "旧入口"}),
                          ("/api/council/schedule", {"title": "旧预约", "at": "2030-01-01"}),
                          ("/api/council/idea_promote", {"idea": "旧升级"})):
        status, body, _ = request("POST", path, payload, session)
        assert status == 409 and "error" in body

    assert json.loads(paths["REGISTRY_SNIPPET"].read_text(encoding="utf-8")) == [created]
    ledger = json.loads(paths["PREFLIGHT"].read_text(encoding="utf-8"))
    assert list(ledger) == [config["request_id"]]
    assert ledger[config["request_id"]]["topic"] == created
    assert ledger[config["request_id"]]["config_recorded"] is True
    messages = [json.loads(line) for line in paths["MESSAGES"].read_text(encoding="utf-8").splitlines()]
    assert len(messages) == 1 and messages[0]["kind"] == "config"
    assert messages[0]["request_id"] == config["request_id"]
    assert not paths["SCHEDULED"].exists()
    assert not paths["IDEA_POOL"].exists()
    assert not paths["UPLOAD_DIR"].exists()
    assert not paths["ARCHIVE_ROOT"].exists()
    assert set(paths["DATA_DIR"].iterdir()) == {
        paths["REGISTRY_SNIPPET"], paths["PREFLIGHT"], paths["MESSAGES"]}
