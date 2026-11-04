# -*- coding: utf-8 -*-
"""IFS G1/G2：D1 写接口幂等重放 + 403 error/v1 测试（WO-IFS-IDEMP-20261104）。

**离线**：全部 tmp 账本 / 内存缓存；不连真实服务、不写生产数据、不启服务。

覆盖：
1) 幂等重放：decision/handoff/meeting create 同键第二次返回首次结果 +
   ``idempotent_replay=true``、账本不重复写；键来源 Idempotent-Key / X-Request-Id；
2) 键缺失：保持现有行为（无 idempotent_replay、正常写账）；
3) 坏键 fail-visible 400；同键不同体 fail-visible 409（不覆盖首次结果）；
4) 首次/重放均带 IFS envelope（schema_version/request_id/source/data/warnings）；
5) 403 鉴权分支：error/v1 + request_id + error_detail，旧 error 字符串保留；
6) 只读边界：缓存模块无落盘/网络写；生产账本零改动（全部 tmp）。
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
for candidate in (ROOT, PORTAL):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import ifs_envelope as E  # noqa: E402
import ifs_idempotency as ID  # noqa: E402
from agent_platform.contracts import ifs  # noqa: E402

REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{32}$")
IDEMPOTENT_KEY = "decision-key-0001"


class FakeHandler:
    def __init__(self, path, obj=None, headers=None):
        body = json.dumps(obj or {}, ensure_ascii=False).encode("utf-8")
        self.path = path
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Length", str(len(body)))
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _decision_body(**overrides):
    body = {
        "code": "002445.SZ",
        "tier": "BUY",
        "confidence": 0.7,
        "rationale": "3买回抽不破中枢上沿，paper-only 观察",
        "evidence": ["bridge"],
        "paper_only": True,
    }
    body.update(overrides)
    return body


def _handoff_body(**overrides):
    body = {
        "action_name": "create",
        "code": "002445.SZ",
        "target": "WA1",
        "action": "watch",
        "rationale": "回踩观察",
        "buy_zone": [2.98, 3.05],
        "paper_only": True,
    }
    body.update(overrides)
    return body


def _meeting_body(**overrides):
    body = {
        "action": "create",
        "code": "002445.SZ",
        "topic": "测试会议",
        "asof": "2026-11-04",
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _clean_cache():
    ID.reset_cache()
    yield
    ID.reset_cache()


# ── 1. decision create 幂等重放 ─────────────────────────────────────
class TestDecisionIdempotency:
    @pytest.fixture()
    def decision_module(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        return A, D

    def test_replay_same_key_returns_first_result_without_write(self, decision_module):
        A, D = decision_module
        first = FakeHandler("/api/d1/decision", _decision_body(),
                            {"Idempotent-Key": IDEMPOTENT_KEY})
        A.handle_post(first)
        assert first.status == 200
        assert first.payload["ok"] is True
        assert "idempotent_replay" not in first.payload
        decision_id = first.payload["decision"]["decision_id"]
        first_request_id = first.payload["request_id"]
        assert REQUEST_ID_RE.match(first_request_id)
        assert ifs.validate_envelope(first.payload) == []
        assert len(D.read_decisions()) == 1

        second = FakeHandler("/api/d1/decision", _decision_body(),
                             {"Idempotent-Key": IDEMPOTENT_KEY})
        A.handle_post(second)
        assert second.status == 200
        assert second.payload["idempotent_replay"] is True
        assert second.payload["decision"]["decision_id"] == decision_id
        assert second.payload["idempotency_key"] == IDEMPOTENT_KEY
        assert second.payload["original_request_id"] == first_request_id
        assert second.payload["request_id"] != first_request_id  # 新请求新 request_id
        assert second.payload["data"]["idempotent_replay"] is True
        assert ifs.validate_envelope(second.payload) == []
        assert len(D.read_decisions()) == 1, "同键重放不得重复写账本"

    def test_x_request_id_as_idempotency_key(self, decision_module):
        A, D = decision_module
        request_id = "req-" + "a" * 32
        headers = {"X-Request-Id": request_id}
        first = FakeHandler("/api/d1/decision", _decision_body(), headers)
        A.handle_post(first)
        assert first.status == 200
        second = FakeHandler("/api/d1/decision", _decision_body(), headers)
        A.handle_post(second)
        assert second.status == 200
        assert second.payload["idempotent_replay"] is True
        assert second.payload["decision"]["decision_id"] == \
            first.payload["decision"]["decision_id"]
        assert len(D.read_decisions()) == 1

    def test_key_missing_keeps_legacy_behavior(self, decision_module):
        A, D = decision_module
        handler = FakeHandler("/api/d1/decision", _decision_body())
        A.handle_post(handler)
        assert handler.status == 200
        assert "idempotent_replay" not in handler.payload
        assert len(D.read_decisions()) == 1

    def test_bad_key_fail_visible_400(self, decision_module):
        A, D = decision_module
        handler = FakeHandler("/api/d1/decision", _decision_body(),
                              {"Idempotent-Key": "bad key!"})
        A.handle_post(handler)
        assert handler.status == 400
        assert handler.payload["ok"] is False
        assert handler.payload["error"] == "bad_idempotency_key"
        assert handler.payload["schema_version"] == "error/v1"
        assert handler.payload["error_detail"]["code"] == "invalid_parameter"
        assert D.read_decisions() == []

    def test_same_key_different_body_conflict_409(self, decision_module):
        A, D = decision_module
        first = FakeHandler("/api/d1/decision", _decision_body(),
                            {"Idempotent-Key": IDEMPOTENT_KEY})
        A.handle_post(first)
        assert first.status == 200
        second = FakeHandler(
            "/api/d1/decision",
            _decision_body(rationale="第二次请求体与首次不同（冲突）"),
            {"Idempotent-Key": IDEMPOTENT_KEY},
        )
        A.handle_post(second)
        assert second.status == 409
        assert second.payload["error"] == "idempotency_conflict"
        assert second.payload["schema_version"] == "error/v1"
        assert second.payload["error_detail"]["code"] == "conflict"
        assert len(D.read_decisions()) == 1

    def test_different_keys_write_two_rows(self, decision_module):
        A, D = decision_module
        for key in ("decision-key-0001", "decision-key-0002"):
            handler = FakeHandler("/api/d1/decision", _decision_body(),
                                  {"Idempotent-Key": key})
            A.handle_post(handler)
            assert handler.status == 200
        assert len(D.read_decisions()) == 2


# ── 2. handoff create 幂等重放 ──────────────────────────────────────
class TestHandoffIdempotency:
    @pytest.fixture()
    def handoff_module(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
        return A, H

    def test_replay_same_key_no_duplicate_receipt(self, handoff_module):
        A, H = handoff_module
        first = FakeHandler("/api/d1/handoff", _handoff_body(),
                            {"Idempotent-Key": "handoff-key-0001"})
        A.handle_post(first)
        assert first.status == 200
        receipt = first.payload["handoff"]["receipt_id"]
        assert len(H.read_handoffs()) == 1

        second = FakeHandler("/api/d1/handoff", _handoff_body(),
                             {"Idempotent-Key": "handoff-key-0001"})
        A.handle_post(second)
        assert second.status == 200
        assert second.payload["idempotent_replay"] is True
        assert second.payload["handoff"]["receipt_id"] == receipt
        assert len(H.read_handoffs()) == 1

    def test_key_missing_writes_twice(self, handoff_module):
        A, H = handoff_module
        for _ in range(2):
            handler = FakeHandler("/api/d1/handoff", _handoff_body())
            A.handle_post(handler)
            assert handler.status == 200
            assert "idempotent_replay" not in handler.payload
        assert len(H.read_handoffs()) == 2


# ── 3. meeting create 幂等重放 ──────────────────────────────────────
class TestMeetingIdempotency:
    @pytest.fixture()
    def meeting_module(self, tmp_path, monkeypatch):
        from agent_platform import d1_meeting as M
        import api_d1_meeting as A

        monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
        return A, M, tmp_path / "meetings"

    def test_replay_same_key_single_meeting(self, meeting_module):
        A, M, meetings_dir = meeting_module
        first = FakeHandler("/api/d1/meeting", _meeting_body(),
                            {"Idempotent-Key": "meeting-key-0001"})
        A.handle_post(first)
        assert first.status == 200
        meeting_id = first.payload["meeting"]["meeting_id"]
        files_after_first = sorted(p.name for p in meetings_dir.iterdir())

        second = FakeHandler("/api/d1/meeting", _meeting_body(),
                             {"Idempotent-Key": "meeting-key-0001"})
        A.handle_post(second)
        assert second.status == 200
        assert second.payload["idempotent_replay"] is True
        assert second.payload["meeting"]["meeting_id"] == meeting_id
        assert sorted(p.name for p in meetings_dir.iterdir()) == files_after_first

    def test_body_idempotency_key_supported(self, meeting_module):
        A, _M, _dir = meeting_module
        body = _meeting_body(idempotency_key="meeting-body-key-0001")
        first = FakeHandler("/api/d1/meeting", body)
        A.handle_post(first)
        assert first.status == 200
        second = FakeHandler("/api/d1/meeting", body)
        A.handle_post(second)
        assert second.status == 200
        assert second.payload["idempotent_replay"] is True
        assert second.payload["meeting"]["meeting_id"] == \
            first.payload["meeting"]["meeting_id"]


# ── 4. 幂等缓存单元（键解析/冲突/TTL/并发串行） ─────────────────────
class TestIdempotencyCache:
    def test_resolve_key_priority(self):
        handler = FakeHandler("/api/d1/decision", {},
                              {"Idempotent-Key": "idem-key-0001",
                               "X-Request-Id": "req-" + "b" * 32})
        assert ID.resolve_key(handler, {}) == ("idem-key-0001", "")

    def test_resolve_key_x_request_id(self):
        request_id = "req-" + "c" * 32
        handler = FakeHandler("/api/d1/decision", {}, {"X-Request-Id": request_id})
        assert ID.resolve_key(handler, {}) == (request_id, "")

    def test_resolve_key_invalid_header_fail_visible(self):
        handler = FakeHandler("/api/d1/decision", {}, {"Idempotent-Key": "x"})
        assert ID.resolve_key(handler, {}) == ("", "bad_idempotency_key")

    def test_resolve_key_absent(self):
        handler = FakeHandler("/api/d1/decision", {}, {})
        assert ID.resolve_key(handler, {}) == ("", "")

    def test_guard_remember_and_replay(self):
        with ID.replay_guard("d1.decision.create", "guard-key-0001",
                             None, {"a": 1}) as ctx:
            assert ctx.replay is None
            ctx.remember({"ok": True, "decision": {"decision_id": "D1-1"}},
                         "req-" + "d" * 32)
        with ID.replay_guard("d1.decision.create", "guard-key-0001",
                             None, {"a": 1}) as ctx:
            assert ctx.replay is not None
            assert ctx.replay["decision"]["decision_id"] == "D1-1"
            assert ctx.conflict() is False
            replayed = ctx.replayed_payload()
            assert replayed["idempotent_replay"] is True
            assert replayed["idempotency_key"] == "guard-key-0001"

    def test_guard_detects_conflict_fingerprint(self):
        with ID.replay_guard("d1.handoff.create", "guard-key-0002",
                             None, {"a": 1}) as ctx:
            ctx.remember({"ok": True})
        with ID.replay_guard("d1.handoff.create", "guard-key-0002",
                             None, {"a": 2}) as ctx:
            assert ctx.replay is not None
            assert ctx.conflict() is True

    def test_cache_ttl_expiry(self, monkeypatch):
        fake_now = {"t": 1000.0}
        monkeypatch.setattr(ID.time, "time", lambda: fake_now["t"])
        with ID.replay_guard("d1.meeting.create", "ttl-key-0001", None, {}) as ctx:
            ctx.remember({"ok": True})
        # TTL 内命中
        with ID.replay_guard("d1.meeting.create", "ttl-key-0001", None, {}) as ctx:
            assert ctx.replay is not None
        fake_now["t"] += ID.TTL_S + 1
        with ID.replay_guard("d1.meeting.create", "ttl-key-0001", None, {}) as ctx:
            assert ctx.replay is None

    def test_cache_bounded(self):
        for index in range(ID.MAX_ENTRIES + 20):
            with ID.replay_guard("d1.decision.create", "bounded-key-%04d" % index,
                                 None, {}) as ctx:
                ctx.remember({"ok": True, "i": index})
        assert ID.cache_stats()["entries"] <= ID.MAX_ENTRIES

    def test_cache_module_has_no_file_or_network_writes(self):
        source = (PORTAL / "ifs_idempotency.py").read_text(encoding="utf-8")
        for token in ("open(", "write_text(", "json.dump(", "urllib", "requests",
                      "socket", "subprocess"):
            assert token not in source, "幂等模块出现不该有的操作: %r" % token


# ── 5. 403 鉴权分支 error/v1 ────────────────────────────────────────
class TestAuthForbiddenEnvelope:
    def test_auth_error_envelope(self):
        handler = FakeHandler("/api/d1/decision", {}, {})
        payload = E.auth_error(handler, "POST")
        assert payload["ok"] is False
        assert payload["schema_version"] == "error/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["error"].startswith("forbidden: 缺失或不匹配的 X-Portal-Token")
        detail = payload["error_detail"]
        assert detail["code"] == "forbidden"
        assert detail["retryable"] is False
        assert detail["details"]["http_status"] == 403
        assert payload["source"]["interface_id"] == "d1.decision.create"
        assert getattr(handler, "_ifs_request_id", None) == payload["request_id"]

    def test_auth_error_long_message_preserved(self):
        message = ("forbidden: 缺失或不匹配的 X-Portal-Token"
                   "（本机浏览器经门户页面操作会自动携带；脚本调用请带"
                   " header X-Portal-Token，token 见 PORTAL_API_TOKEN"
                   " 或 C:\\Temp\\portal_api_token）")
        payload = E.auth_error(FakeHandler("/api/d1/handoff", {}, {}), "POST", message)
        assert payload["error"] == message  # 旧 error 字符串完整保留
        assert payload["error_detail"]["message"] == message
        assert payload["error_detail"]["code"] == "forbidden"

    def test_auth_error_unknown_path_has_no_source(self):
        payload = E.auth_error(FakeHandler("/api/other", {}, {}), "GET")
        assert payload["schema_version"] == "error/v1"
        assert payload.get("source") is None or \
            "interface_id" not in (payload.get("source") or {})

    def test_portal_api_gates_use_error_envelope(self):
        source = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
        assert source.count("ifs_envelope.auth_error(") == 3, "403 三处闸门均需接入"
        assert "_check_portal_token()" in source
        # 旧 error 文案保留（作为 auth_error 的 message 参数）
        assert "forbidden: 缺失或不匹配的 X-Portal-Token" in source

    def test_auth_error_http_status_mapping(self):
        # 400/409 幂等相关错误码映射回归
        assert E.ifs_error_code("bad_idempotency_key", 400) == "invalid_parameter"
        assert E.ifs_error_code("idempotency_conflict", 409) == "conflict"
        assert E.ifs_error_code("forbidden: 缺失或不匹配的 X-Portal-Token", 403) == "forbidden"
