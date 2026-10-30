# -*- coding: utf-8 -*-
import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notification_decision as nd
import push_admin_server as admin


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(nd, "DB_PATH", str(tmp_path / "decisions.sqlite3"))
    monkeypatch.setattr(nd, "targets", lambda: [{"name": "测试群", "id": "oc_mock", "type": "chat_id"}])
    monkeypatch.setenv("PUSH_DECISION_KEY", "test-only-key")
    monkeypatch.setenv("PUSH_DECISION_CLIENT_KEY", "test-client-key")
    monkeypatch.setenv("PUSH_DECISION_HERMES_KEY", "test-hermes-key")
    monkeypatch.delenv("PUSH_DECISION_EXECUTE", raising=False)
    return {"id": str(uuid.uuid4()), "category": "alert", "target_name": "测试群",
            "send_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "reason": "人工风险判断", "content": "模拟预警", "agent_source": "human",
            "operator": "测试员", "basis": "依据模拟预警", "confirmed_target": "测试群"}


def test_preview_validation_and_record_idempotency(isolated):
    assert nd.preview(isolated)["ok"]
    assert not nd.preview({**isolated, "target_name": "未知群"})["ok"]
    assert not nd.preview({**isolated, "send_at": "2026-10-18T10:00:00"})["ok"]
    assert not nd.preview({**isolated, "agent_source": "Hermes"})["ok"]
    assert not nd.preview({**isolated, "operator": ""})["ok"]
    assert not nd.preview({**isolated, "basis": ""})["ok"]
    assert not nd.record(isolated)["ok"]
    assert not nd.record({**isolated, "confirmed_target": "其他群"}, caller="admin_ui")["ok"]
    assert nd.record(isolated, caller="admin_ui")["ok"]
    assert nd.record(isolated, caller="admin_ui")["duplicate"]
    assert not nd.record({**isolated, "content": "篡改"}, caller="admin_ui")["ok"]
    assert not nd.record(isolated, caller="trusted_client")["ok"]
    row = nd.recent()["decisions"][0]
    assert row["agent_source"] == "human" and row["caller"] == "admin_ui"
    assert "approval_state" not in row
    assert all(k not in row for k in ("content", "reason", "basis", "operator", "target_id", "target_name"))
    with nd._db() as db:
        saved = db.execute("SELECT operator,basis,reason FROM decisions").fetchone()
    assert tuple(saved) == ("测试员", "依据模拟预警", "人工风险判断")
    assert nd.caller_for_key("wrong") is None
    assert nd.caller_for_key("test-only-key") == "admin_ui"
    assert nd.caller_for_key("test-client-key") == "trusted_client"
    assert nd.caller_for_key("test-hermes-key") == "hermes_internal"
    from unittest.mock import patch
    with patch.dict(nd.os.environ, {"PUSH_DECISION_HERMES_KEY": "test-only-key"}):
        assert nd.caller_for_key("test-only-key") is None


def test_hermes_requires_distinct_caller_and_never_executes(isolated):
    hermes = {**isolated, "id": str(uuid.uuid4()), "agent_source": "hermes",
              "category": "os", "operator": "Hermes", "reason": "服务健康异常",
              "basis": "服务状态快照"}
    assert not nd.preview(hermes)["ok"]
    assert not nd.record(hermes, caller="admin_ui")["ok"]
    assert not nd.record(isolated, caller="hermes_internal")["ok"]
    assert nd.preview(hermes, caller="hermes_internal")["ok"]
    assert nd.record(hermes, caller="hermes_internal")["ok"]
    assert nd.record(hermes, caller="hermes_internal")["duplicate"]
    assert not nd.record({**hermes, "basis": "改写依据"}, caller="hermes_internal")["ok"]
    with nd._db() as db:
        row = db.execute("SELECT agent_source,caller,reason,basis,target_id FROM decisions WHERE id=?", (hermes["id"],)).fetchone()
    assert tuple(row) == ("hermes", "hermes_internal", "服务健康异常", "服务状态快照", "oc_mock")
    assert not nd.execute(hermes["id"], lambda _: pytest.fail("默认不得发送"))["ok"]


def test_legacy_migration(isolated):
    with sqlite3.connect(nd.DB_PATH) as db:
        db.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, category TEXT, target_name TEXT, target_id TEXT, send_at TEXT, reason TEXT, content TEXT, created_at TEXT, status TEXT, executed_at TEXT, outcome TEXT)")
    assert nd.record(isolated, caller="admin_ui")["ok"]
    assert nd.recent()["decisions"][0]["agent_source"] == "human"


def test_announcement_requires_explicit_bound_approval(isolated, monkeypatch):
    announcement = {**isolated, "category": "announcement", "agent_source": "hermes",
                    "operator": "Hermes"}
    assert nd.record(announcement, caller="hermes_internal")["ok"]
    assert nd.recent()["decisions"][0]["approval_state"] == "pending"
    sent = []
    sender = lambda payload: sent.append(payload) or {"ok": True}
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    assert not nd.execute(announcement["id"], sender)["ok"] and not sent
    approval = {k: announcement[k] for k in ("id", "content", "target_name", "send_at")}
    approval.update(target_id="oc_mock", operator="人工审批员")
    assert not nd.approve(approval, caller="hermes_internal")["ok"]
    assert not nd.approve({**approval, "operator": " "}, caller="admin_ui")["ok"]
    for field, value in (("id", str(uuid.uuid4())), ("content", "篡改内容"),
                         ("target_name", "其他群"), ("target_id", "oc_other"),
                         ("send_at", "2000-01-01T00:00:00+00:00")):
        assert not nd.approve({**approval, field: value}, caller="admin_ui")["ok"]
    assert nd.approve(approval, caller="admin_ui")["ok"]
    with nd._db() as db:
        evidence = db.execute("SELECT * FROM announcement_approvals WHERE decision_id=?",
                              (announcement["id"],)).fetchone()
    assert evidence["operator"] == "人工审批员" and evidence["caller"] == "admin_ui"
    assert evidence["content"] == announcement["content"] and evidence["target_id"] == "oc_mock"
    assert nd.recent()["decisions"][0]["approval_state"] == "credential_approved"
    assert not sent
    assert nd.execute(announcement["id"], sender)["ok"] and len(sent) == 1
    assert sent[0]["kind"] == "decision:announcement"
    assert not nd.execute(announcement["id"], sender)["ok"] and len(sent) == 1


@pytest.mark.parametrize("field,value", [("content", "审批后修改"), ("target_id", "oc_other"),
                                        ("target_name", "其他群"),
                                        ("send_at", "2000-01-01T00:00:00+00:00")])
def test_announcement_approval_invalidated_by_change(isolated, monkeypatch, field, value):
    announcement = {**isolated, "category": "announcement"}
    assert nd.record(announcement, caller="admin_ui")["ok"]
    approval = {k: announcement[k] for k in ("id", "content", "target_name", "send_at")}
    assert nd.approve({**approval, "target_id": "oc_mock", "operator": "人工审批员"}, caller="admin_ui")["ok"]
    with nd._db() as db:
        db.execute("UPDATE decisions SET %s=? WHERE id=?" % field, (value, announcement["id"]))  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
    assert nd.recent()["decisions"][0]["approval_state"] == "pending"
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    assert not nd.execute(announcement["id"], lambda _: pytest.fail("审批失效不可发送"))["ok"]


def test_legacy_announcement_unapproved_after_migration(isolated, monkeypatch):
    with sqlite3.connect(nd.DB_PATH) as db:
        db.execute("""CREATE TABLE decisions (id TEXT PRIMARY KEY, category TEXT, target_name TEXT,
            target_id TEXT, send_at TEXT, reason TEXT, content TEXT, created_at TEXT,
            status TEXT, executed_at TEXT, outcome TEXT)""")
        db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (isolated["id"], "announcement", "测试群", "oc_mock", isolated["send_at"],
                    "旧理由", "旧公告", "2020-01-01", "recorded", None, None))
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    with nd._db() as db:
        row = db.execute("SELECT * FROM decisions WHERE id=?", (isolated["id"],)).fetchone()
        assert row["content"] == "旧公告" and row["caller"] == "legacy_unknown"
        assert db.execute("SELECT count(*) FROM announcement_approvals").fetchone()[0] == 0
    assert nd.recent()["decisions"][0]["approval_state"] == "pending"
    assert not nd.execute(isolated["id"], lambda _: pytest.fail("旧公告未经审批不可发送"))["ok"]


def test_execution_fail_closed_and_at_most_once(isolated, monkeypatch):
    nd.record(isolated, caller="admin_ui")
    sent = []
    sender = lambda payload: sent.append(payload) or {"ok": True}
    assert not nd.execute(isolated["id"], sender)["ok"]
    assert not sent
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    monkeypatch.setattr(nd, "targets", lambda: [])
    assert not nd.execute(isolated["id"], sender)["ok"]
    monkeypatch.setattr(nd, "targets", lambda: [{"name": "测试群", "id": "oc_other", "type": "chat_id"}])
    assert not nd.execute(isolated["id"], sender)["ok"]
    monkeypatch.setattr(nd, "targets", lambda: [{"name": "测试群", "id": "oc_mock", "type": "chat_id"}])
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": False}]})
    assert not nd.execute(isolated["id"], sender)["ok"] and not sent
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    assert nd.execute(isolated["id"], sender)["ok"]
    assert sent[0]["receive_id_type"] == "chat_id"
    assert sent[0]["kind"] == "decision:alert"
    assert sent[0]["critical"] is False
    assert not nd.execute(isolated["id"], sender)["ok"]
    assert len(sent) == 1


def test_future_and_uncertain_send_never_retry(isolated, monkeypatch):
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    future = {**isolated, "send_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    nd.record(future, caller="admin_ui")
    assert not nd.execute(future["id"], lambda _: pytest.fail("未到时间不可发送"))["ok"]
    other = {**isolated, "id": str(uuid.uuid4())}
    nd.record(other, caller="admin_ui")
    def timeout(_):
        raise TimeoutError()
    result = nd.execute(other["id"], timeout)
    assert result["decision"]["status"] == "unknown_after_attempt"
    assert not nd.execute(other["id"], lambda _: pytest.fail("不可重发"))["ok"]


def test_group_info_uses_configured_chat_only_and_unknown_on_failure(isolated, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client
    monkeypatch.setattr(feishu_client, "load_app_creds", lambda: ("app", "secret"))
    monkeypatch.setattr(feishu_client, "get_tat", lambda *a: "private-token")
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"code": 0, "data": {"name": "飞书群", "user_count": 7}}).encode()
    class MembersResponse(Response):
        def read(self): return json.dumps({"code": 0, "data": {"member_total": 7}}).encode()
    def urlopen(req, timeout):
        assert req.get_header("Authorization") == "Bearer private-token"
        if req.full_url.endswith("/im/v1/chats/oc_mock"):
            return Response()
        assert req.full_url.endswith("/im/v1/chats/oc_mock/members?member_id_type=open_id&page_size=100")
        return MembersResponse()
    monkeypatch.setattr(nd.urllib.request, "urlopen", urlopen)
    group = nd.group_info()["groups"][0]
    assert group["name"] == "飞书群" and group["member_count"] == 7 and group["verified"]
    assert "private-token" not in str(group)
    class MismatchResponse(Response):
        def read(self): return json.dumps({"code": 0, "data": {"chat_id": "oc_other", "name": "别的群"}}).encode()
    monkeypatch.setattr(nd.urllib.request, "urlopen", lambda *a, **kw: MismatchResponse())
    assert not nd.group_info()["groups"][0]["verified"]
    monkeypatch.setattr(nd.urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("private-token")))
    group = nd.group_info()["groups"][0]
    assert group["name"] == "测试群" and group["member_count"] is None
    assert "private-token" not in str(group)


def test_page_and_proxy_contract():
    assert 'id="sec-decision"' in admin.PAGE
    assert '/static/tokens.css' not in admin.PAGE
    assert '/static/nav.js' not in admin.PAGE
    assert '/static/shortscreen.js' not in admin.PAGE
    assert 'id="dec-key" type="password"' in admin.PAGE
    assert "fetch('api/decisions',{headers})" in admin.PAGE
    assert "fetch('api/decision-groups',{headers})" in admin.PAGE
    assert "decisionRequest('record',body,key)" in admin.PAGE
    assert 'api/decisions/execute' not in admin.PAGE
    assert "date?new Date(date):null" in admin.PAGE
    assert "群目标二次确认不一致" in admin.PAGE
    assert "记录接口失败：" in admin.PAGE
    assert "<th>理由</th>" not in admin.PAGE.split('async function loadDecisions()')[1].split('function show(s)')[0]
    conf = (Path(__file__).parent / "nginx.conf").read_text(encoding="utf-8")
    assert "location ^~ /push/ {" in conf
    assert "location = /push { return 302 /push/; }" in conf
    assert "return 403" not in conf.split("location ^~ /push/ {")[1].split("}")[0]


def test_decision_http_reads_and_preview_require_key(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(nd, "group_info", lambda: calls.append("groups") or {"ok": True, "groups": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:%d" % server.server_port
        for path in ("/api/decisions", "/api/decision-groups", "/api/decisions/preview"):
            for key in (None, "wrong"):
                headers = {"X-Decision-Key": key} if key else {}
                body = json.dumps(isolated).encode() if path.endswith("preview") else None
                req = urllib.request.Request(base + path, data=body, headers=headers)
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req, timeout=2)
                assert error.value.code == 403
                error.value.close()
        assert not calls
        for key in ("test-only-key", "test-client-key", "test-hermes-key"):
            for path in ("/api/decisions", "/api/decision-groups"):
                req = urllib.request.Request(base + path, headers={"X-Decision-Key": key})
                with urllib.request.urlopen(req, timeout=2) as response:
                    assert json.load(response)["ok"]
        req = urllib.request.Request(base + "/api/decisions/preview",
                                     data=json.dumps(isolated).encode(),
                                     headers={"X-Decision-Key": "test-only-key"})
        with urllib.request.urlopen(req, timeout=2) as response:
            assert json.load(response)["ok"]
        assert len(calls) == 3
        hermes = {**isolated, "id": str(uuid.uuid4()), "agent_source": "hermes", "operator": "Hermes"}
        for key in ("test-only-key", "test-client-key"):
            req = urllib.request.Request(base + "/api/decisions/record", data=json.dumps(hermes).encode(), headers={"X-Decision-Key": key})
            with urllib.request.urlopen(req, timeout=2) as response:
                assert not json.load(response)["ok"]
        req = urllib.request.Request(base + "/api/decisions/record", data=json.dumps(hermes).encode(), headers={"X-Decision-Key": "test-hermes-key"})
        with urllib.request.urlopen(req, timeout=2) as response:
            assert json.load(response)["decision"]["agent_source"] == "hermes"
        req = urllib.request.Request(base + "/api/decisions/execute", data=json.dumps({"id": hermes["id"]}).encode(), headers={"X-Decision-Key": "test-hermes-key"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=2)
        assert error.value.code == 403
        error.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_announcement_http_approve_requires_admin_key(isolated, monkeypatch):
    announcement = {**isolated, "category": "announcement", "agent_source": "hermes",
                    "operator": "Hermes"}
    assert nd.record(announcement, caller="hermes_internal")["ok"]
    approval = {k: announcement[k] for k in ("id", "content", "target_name", "send_at")}
    approval.update(target_id="oc_mock", operator="人工审批员")
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info", lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    sent = []
    monkeypatch.setattr(admin, "api_send", lambda payload: sent.append(payload) or {"ok": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:%d" % server.server_port
        def post(path, body, key):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"X-Decision-Key": key})
            try:
                with urllib.request.urlopen(req, timeout=2) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)
        for key in ("test-hermes-key", "test-client-key", "wrong"):
            status, result = post("/api/decisions/approve", approval, key)
            assert status == 403 and not result["ok"]
        assert not post("/api/decisions/execute", {"id": announcement["id"]}, "test-only-key")[1]["ok"]
        assert not sent
        status, result = post("/api/decisions/approve", approval, "test-only-key")
        assert status == 200 and result["ok"] and not sent
        assert post("/api/decisions/execute", {"id": announcement["id"]}, "test-only-key")[1]["ok"]
        assert len(sent) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sensitive_endpoints_and_open_stats_isolation(isolated, monkeypatch):
    monkeypatch.setenv("PUSH_ADMIN_KEY", "admin-secret")
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    monkeypatch.setattr(admin, "tasks_snapshot", lambda *a, **kw: {})
    monkeypatch.setattr(admin, "source_health", lambda: [])
    monkeypatch.setattr(admin, "load_subs", lambda: [])
    monkeypatch.setattr(admin, "load_receivers", lambda: [])
    monkeypatch.setattr(admin, "news_targets", lambda: [])
    monkeypatch.setattr(admin, "sub_state", lambda: {})
    monkeypatch.setattr(admin, "flow_matrix", lambda: {})
    monkeypatch.setattr(admin, "load_schedule", lambda: {})
    monkeypatch.setattr(admin, "_shadow_stats", lambda: None)
    monkeypatch.setattr(admin, "_in_silent_window", lambda **kw: False)
    monkeypatch.setattr(admin, "_actions_stats", lambda: {})
    monkeypatch.setattr(admin, "km_today_sent", lambda: 0)
    original_dispatch = admin.dispatch
    monkeypatch.setattr(admin, "dispatch", lambda req: original_dispatch(req) if req["action"] in ("set_biz", "regen_apikey") else {"ok": True, "action": req["action"]})
    saved_biz = {"tenant": "test", "plan": "internal", "quota_daily": 0, "apikey": "commercial-secret"}
    monkeypatch.setattr(admin, "load_biz", lambda: saved_biz.copy())
    monkeypatch.setattr(admin, "save_biz", lambda value: saved_biz.update(value))
    sent = []
    monkeypatch.setattr(admin, "api_send", lambda req: sent.append(req) or {"ok": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:%d" % server.server_port
        for path, body in (("/api/state", None), ("/api/act", {"action": "run_task"}),
                           ("/api/send", {"content": "do not send", "receive_id": "oc_mock"})):
            for headers in ({}, {"X-API-Key": "commercial-secret"}, {"X-Push-Admin-Key": "wrong"},
                            {"X-Push-Hub-Key": "wrong"}):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None, headers=headers)
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req, timeout=2)
                assert error.value.code == 403
                error.value.close()
        assert not sent
        for path in ("/api/state?key=admin-secret", "/api/act?key=admin-secret", "/api/send?key=hub-secret"):
            req = urllib.request.Request(base + path, data=b'{}' if path.startswith(("/api/act", "/api/send")) else None)
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=2)
            assert error.value.code == 403
            error.value.close()
        req = urllib.request.Request(base + "/api/state", headers={"X-Push-Admin-Key": "admin-secret"})
        with urllib.request.urlopen(req, timeout=2) as response:
            state = json.load(response)
        assert "commercial-secret" not in json.dumps(state)
        for action in ("set_biz", "regen_apikey"):
            req = urllib.request.Request(base + "/api/act", data=json.dumps({"action": action}).encode(),
                                         headers={"X-Push-Admin-Key": "admin-secret"})
            with urllib.request.urlopen(req, timeout=2) as response:
                result = json.load(response)
            assert result["ok"] and "apikey" not in result["biz"]
            assert saved_biz["apikey"] not in json.dumps(result)
        req = urllib.request.Request(base + "/api/act", data=b'{"action":"probe"}', headers={"X-Push-Admin-Key": "admin-secret"})
        with urllib.request.urlopen(req, timeout=2) as response:
            assert json.load(response)["action"] == "probe"
        req = urllib.request.Request(base + "/api/send", data=b'{"content":"test","receive_id":"oc_mock"}', headers={"X-Push-Hub-Key": "hub-secret"})
        with urllib.request.urlopen(req, timeout=2) as response:
            assert json.load(response)["ok"]
        assert len(sent) == 1
        req = urllib.request.Request(base + "/api/open/stats", headers={"X-Push-Admin-Key": "admin-secret"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=2)
        assert error.value.code == 403
        error.value.close()
        req = urllib.request.Request(base + "/api/open/stats", headers={"X-API-Key": saved_biz["apikey"]})
        with urllib.request.urlopen(req, timeout=2) as response:
            assert json.load(response)["ok"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hub_client_never_falls_back_on_auth_failure(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client as client
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    def forbidden_direct(*args, **kwargs):
        pytest.fail("鉴权失败不应直发")
    monkeypatch.setattr(client, "load_app_creds", forbidden_direct)
    for status in (401, 403):
        def rejected(req, timeout):
            assert req.get_header("X-push-hub-key") == "hub-secret" or req.get_header("X-Push-Hub-Key") == "hub-secret"
            assert "hub-secret" not in req.full_url
            raise urllib.error.HTTPError(req.full_url, status, "denied", {}, None)
        monkeypatch.setattr(client.urllib.request, "urlopen", rejected)
        ok, why = client.send_msg("sample", "text", "oc_mock")
        assert not ok and "authorization" in why
    monkeypatch.delenv("PUSH_HUB_KEY")
    # K-PUSH-012 文件兜底隔离：屏蔽 worktree 真实 push_secrets.json，
    # 防止该用例经文件兜底拿到真钥发起真实 HTTP（供钥链 P0 修复配套）。
    monkeypatch.setattr(client, "HUB_SECRETS_FILE", str(tmp_path / "nope.json"))
    client._HUB_KEY_CACHE.update(ts=0.0, key="")
    assert not client.send_msg("sample", "text", "oc_mock")[0]


def test_hub_key_env_first_then_file_fallback(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client as client
    sec = tmp_path / "push_secrets.json"
    sec.write_text(json.dumps({"push_hub_key": "file-key"}), encoding="utf-8")
    monkeypatch.setattr(client, "HUB_SECRETS_FILE", str(sec))
    monkeypatch.delenv("PUSH_HUB_KEY", raising=False)
    client._HUB_KEY_CACHE.update(ts=0.0, key="")
    assert client._hub_key() == "file-key"      # 文件兜底生效
    monkeypatch.setenv("PUSH_HUB_KEY", "env-key")
    assert client._hub_key() == "env-key"       # env 优先
    bad = tmp_path / "broken.json"
    bad.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(client, "HUB_SECRETS_FILE", str(bad))
    monkeypatch.delenv("PUSH_HUB_KEY", raising=False)
    client._HUB_KEY_CACHE.update(ts=0.0, key="")
    assert client._hub_key() == ""              # 坏 JSON 降级空串不炸


@pytest.mark.parametrize("failure", [OSError("network down"), TimeoutError("hub timeout")])
@pytest.mark.parametrize("bypass", [False, True])
def test_hub_unavailable_fails_closed_without_direct_send(monkeypatch, failure, bypass):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client as client
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    if bypass:
        monkeypatch.setenv("HUB_BYPASS", "1")
    else:
        monkeypatch.delenv("HUB_BYPASS", raising=False)
    monkeypatch.setattr(client, "load_app_creds", lambda: pytest.fail("不可获取直发凭证"))
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: pytest.fail("不可获取直发 token"))
    requests = []
    def unavailable(req, timeout):
        requests.append(req.full_url)
        assert req.full_url == client.HUB_SEND_URL
        raise failure
    monkeypatch.setattr(client.urllib.request, "urlopen", unavailable)
    ok, why = client.send_msg("sample", "text", "oc_mock")
    assert ok is False and "hub" in why
    assert requests == [client.HUB_SEND_URL]


@pytest.mark.parametrize("response", [b'not-json', b'{"ok":false,"error":"silent window"}'])
def test_hub_bad_response_or_gate_denial_never_falls_back(monkeypatch, response):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client as client
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    monkeypatch.setattr(client, "load_app_creds", lambda: pytest.fail("闸门失败不应直发"))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return response
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **kw: Response())
    assert client.send_msg("sample", "text", "oc_mock")[0] is False


def test_execute_legacy_send_at_compat(isolated, monkeypatch):
    """审查 P2 修复：legacy 无时区行按 UTC 补齐可执行；坏字段拒绝不炸。"""
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info",
                        lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    nd.record(isolated, caller="admin_ui")
    sent = []
    sender = lambda payload: sent.append(payload) or {"ok": True}
    with nd._db() as db:
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        db.execute("UPDATE decisions SET send_at=? WHERE id=?",
                   (naive, isolated["id"]))
    assert nd.execute(isolated["id"], sender)["ok"] and len(sent) == 1
    other = {**isolated, "id": str(uuid.uuid4())}
    nd.record(other, caller="admin_ui")
    with nd._db() as db:
        db.execute("UPDATE decisions SET send_at='garbage' WHERE id=?",
                   (other["id"],))
    r = nd.execute(other["id"], sender)
    assert not r["ok"] and "损坏" in r["error"] and not sent[1:]


def test_hub_internal_via_hub_false_can_send(monkeypatch):
    """K-ORG-204：via_hub=False（hub 闸内直发）已改为 Agent webhook 推送，
    不再调 OpenAPI /im/v1/messages。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "news_collector"))
    import feishu_client as client
    monkeypatch.setattr(client, "_hub_send", lambda *a, **kw: pytest.fail("闸内直发不可递归调用 hub"))
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    monkeypatch.setattr(client, "_agent_webhook",
                        lambda: "https://agent.local/webhook")
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok": true, "why": "agent ok", "message_id": "om_agent1"}'
    def webhook_send(req, timeout):
        assert req.full_url == "https://agent.local/webhook"
        return Response()
    monkeypatch.setattr(client.urllib.request, "urlopen", webhook_send)
    assert client.send_msg("sample", "text", "oc_mock", via_hub=False)[0] is True


def test_hub_critical_defaults_false(monkeypatch):
    monkeypatch.setattr(admin, "_in_silent_window", lambda **kw: True)
    monkeypatch.setattr(admin, "_flow_gate_deny", lambda *a: None)
    assert admin.api_send({"content": "x", "receive_id": "oc_mock", "kind": "decision:alert"})["error"] == "silent window"


# ---------- K-ORG-203：决策文档引用（原型「文档引用」视图遗留#1） ----------

DOC_URL = "https://lf3-example.feishu.cn/docx/AbCd1234?para=1"


def test_doc_reference_recorded_and_idempotent(isolated):
    ok = {**isolated, "doc_url": DOC_URL, "doc_title": "决策依据文档"}
    assert nd.preview(ok)["ok"]
    r = nd.record(ok, caller="admin_ui")
    assert r["ok"]
    assert r["decision"]["doc_url"] == DOC_URL
    assert r["decision"]["doc_title"] == "决策依据文档"
    assert nd.record({**ok}, caller="admin_ui")["duplicate"]      # 同引用幂等
    assert not nd.record({**ok, "doc_title": "另一份"},
                         caller="admin_ui")["ok"]                 # 改引用即不同决策
    plain = {**isolated, "id": str(uuid.uuid4())}                 # 未提供 → 兼容旧口径
    assert nd.record(plain, caller="admin_ui")["ok"]
    row = next(d for d in nd.recent()["decisions"] if d["id"] == plain["id"])
    assert row["doc_url"] == "" and row["doc_title"] == ""


def test_doc_reference_normalizes_host(isolated):
    up = {**isolated, "id": str(uuid.uuid4()),
          "doc_url": "https://Example.Feishu.CN/docx/Xy9Z", "doc_title": "大写域"}
    r = nd.record(up, caller="admin_ui")
    assert r["ok"] and r["decision"]["doc_url"] == "https://example.feishu.cn/docx/Xy9Z"


@pytest.mark.parametrize("raw", [
    "http://example.feishu.cn/docx/x",        # 非 https
    "https://evil.com/docx/x",                # 域名白名单外
    "https://feishu.cn.evil.com/docx/x",      # 后缀拼接域
    "https://example.feishu.cn/page/123",     # 路径不在 /docx|/docs|/wiki
    "https://example.feishu.cn/docx/",        # 空文档 id
    "not a url",
])
def test_doc_reference_rejects_non_feishu_links(isolated, raw):
    assert not nd.preview({**isolated, "doc_url": raw,
                           "doc_title": "t"})["ok"]


def test_doc_reference_requires_title(isolated):
    assert not nd.preview({**isolated, "doc_url": DOC_URL})["ok"]            # 缺标题
    assert not nd.preview({**isolated, "doc_url": DOC_URL, "doc_title": " "})["ok"]
    assert not nd.preview({**isolated, "doc_title": "只有标题"})["ok"]        # 缺 URL
    assert not nd.preview({**isolated, "doc_url": DOC_URL,
                           "doc_title": "长" * 201})["ok"]                   # 超长
