# -*- coding: utf-8 -*-
"""Q2 契约补口：D1 envelope / auth / idempotency 契约测试（WO-Q2-AUTHCONTRACT-20261104）。

**离线 handler 级**：不启服务、不发真实网络、不写生产数据；ledger/meetings/episodes
全部指向 tmp。放 `portal/tests/`（fast gate 目录，不改 test_gate 清单）。

覆盖：
1) D1 读接口 envelope 契约：ok/schema_version/request_id/source/data/warnings +
   `ifs.validate_envelope` 全绿（research/watch-plans 序列化层，decisions/handoffs/
   meeting/training 走真实 handler + tmp）；
2) 错误契约：error/v1 + request_id + error_detail（code/message/retryable/details）
   且归一后可过 `interface_error_v1.schema.json`；旧 `error` 字符串兼容映射；
3) auth：D1 三个写接口（decision/meeting/handoff）缺 X-Portal-Token 时
   handler 级必须 401/403 且**不得进入业务 handler**（mock 记录，无写入）；
4) 幂等：同 `decision_id` 重复提交被拒且不重复落账（现有行为，已验）；
   IFS `idempotent_replay`（同 idempotency_key 返回原记录）**当前未实现** →
   xfail(strict) + 缺口记录，不伪造通过。

已知缺口（详见 docs/plans/2026-11-04_Q2_契约测试补口_v1.md）：
- 写接口 403 响应是 portal_api 裸 legacy dict（未经 ifs_envelope）→ xfail；
- 兼容期 `error` 仍为旧字符串（IFS-03 定稿要求 error 对象；F1c 切换）→ 兼容断言 +
  归一校验，不把差异写成已符合；
- D1 写接口无 idempotency_key/idempotent_replay 实现 → xfail。
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "portal"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import ifs_envelope as E  # noqa: E402
import portal_api  # noqa: E402
from agent_platform.contracts import ifs  # noqa: E402

REQUEST_ID_RE = re.compile(ifs.REQUEST_ID_PATTERN)
SCHEMA_PATH = ROOT / "docs" / "standards" / "schemas" / "interface_error_v1.schema.json"
D1_WRITE_PATHS = {
    "/api/d1/decision": "api_d1_decision",
    "/api/d1/meeting": "api_d1_meeting",
    "/api/d1/handoff": "api_d1_handoff",
}
DECISION_ID = "d1-20261104030000-abcdef"
DECISION_BODY = {
    "decision_id": DECISION_ID,
    "code": "002445.SZ",
    "tier": "BUY",
    "confidence": 0.7,
    "rationale": "3买回抽不破中枢上沿，paper-only 观察",
    "evidence": ["bridge"],
    "paper_only": True,
}
HANDOFF_BODY = {
    "action_name": "create",
    "code": "002445.SZ",
    "target": "WA1",
    "action": "watch",
    "rationale": "回踩观察",
    "buy_zone": [2.98, 3.05],
    "paper_only": True,
}
MEETING_BODY = {
    "action": "create",
    "code": "002445.SZ",
    "topic": "契约测试会",
    "asof": "2026-11-04",
}


class FakeHandler:
    """最小 handler 替身（_json 记录结果；可选 token 闸门 stub）。"""

    def __init__(self, path, body=b"{}", headers=None, token_ok=False):
        self.path = path
        self.command = "POST"
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Length", str(len(body)))
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None
        self.client_address = ("127.0.0.1", 54321)
        self._token_ok = token_ok

    def _check_portal_token(self):  # noqa: D401（与 portal_api.Handler 同名）
        return self._token_ok

    def _route_main(self, path, qs):
        """复用真实 GET 主分派；FakeHandler 只提供 _json/属性替身。"""
        return portal_api.Handler._route_main(self, path, qs)

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _post(path, obj, headers=None):
    return FakeHandler(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       headers=headers)


def _get(path, headers=None):
    handler = FakeHandler(path, b"", headers=headers)
    handler.command = "GET"  # 真实 GET 请求；meeting 路由需按方法区分 get/create
    return handler


def _assert_success(payload, interface_id, schema_version=None):
    assert ifs.validate_envelope(payload) == [], ifs.validate_envelope(payload)
    expected = schema_version or E.INTERFACES[interface_id]["schema_version"]
    assert payload["schema_version"] == expected
    assert REQUEST_ID_RE.match(payload["request_id"])
    assert payload["source"]["interface_id"] == interface_id
    assert payload["source"]["source_mode"] in ifs.SOURCE_MODES
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["data"], dict)
    assert payload["ok"] is True


def _error_schema_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(schema)


def _envelope_view_of_error(payload):
    """兼容期 payload（error=旧字符串 + error_detail）→ IFS error/v1 归一视图。"""
    return {
        "ok": False,
        "schema_version": payload["schema_version"],
        "request_id": payload["request_id"],
        "error": payload["error_detail"],
    }


# ══ 1. D1 读接口 envelope 契约 ═══════════════════════════════════════
class TestReadEnvelopeContract:
    def test_research_envelope(self, monkeypatch):
        import d1_data

        legacy = {"ok": True, "mode": "d1_research", "items": [],
                  "summary": {"total": 0}, "pool_id": "pool-1",
                  "generated_at": "2026-11-04T03:00:00"}
        monkeypatch.setattr(d1_data, "d1_snapshot", lambda: dict(legacy))
        h = _get("/api/d1/research")
        portal_api.Handler.do_GET(h)
        assert h.status == 200
        _assert_success(h.payload, "d1.research.get", "d1_research/v1")

    def test_watch_plans_envelope(self):
        legacy = {"ok": True, "schema_version": "watch_plan/v1", "count": 0,
                  "watch_plans": [], "readonly": True, "paper_only": True,
                  "executes": False}
        h = _get("/api/d1/watch-plans")
        E.send(h, legacy)
        _assert_success(h.payload, "d1.watch_plans.list", "watch_plan/v1")

    def test_decision_list_envelope(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        A.handle_post(_post("/api/d1/decision", DECISION_BODY))
        h = _get("/api/d1/decisions")
        A.handle_get(h, "")
        assert h.status == 200
        _assert_success(h.payload, "d1.decision.list", "d1_decision/v1")
        assert DECISION_ID in h.payload["latest_by_code"]["002445.SZ"]["decision_id"]

    def test_handoff_list_envelope(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
        A.handle_post(_post("/api/d1/handoff", HANDOFF_BODY))
        h = _get("/api/d1/handoffs")
        A.handle_get(h, "")
        assert h.status == 200
        _assert_success(h.payload, "d1.handoff.list", "d1_handoff/v2")

    def test_meeting_get_envelope(self, tmp_path, monkeypatch):
        from agent_platform import d1_meeting as M
        import api_d1_meeting as A

        monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
        A.handle_post(_post("/api/d1/meeting", MEETING_BODY))
        h = _get("/api/d1/meeting")
        A.handle_get(h, "")
        assert h.status == 200
        _assert_success(h.payload, "d1.meeting.get", "d1_meeting/v1")

    def test_training_episodes_envelope_compat(self, tmp_path, monkeypatch):
        """training 旧契约字段优先（兼容碰撞）：source 字符串 + source_detail 对象。"""
        import api_d1_training as A

        monkeypatch.setenv("D1_TRAINING_EPISODES_PATH", str(tmp_path / "episodes.jsonl"))
        h = _get("/api/d1/training/episodes")
        A.handle_get(h, "")
        assert h.status == 200
        payload = h.payload
        assert payload["ok"] is True
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["schema_version"] == "training_episode/v1"  # 旧契约优先
        assert isinstance(payload["source"], str)  # 旧 source 字符串保留
        detail = payload["source_detail"]
        assert detail["interface_id"] == "training.episode.get"
        assert detail["source_mode"] in ifs.SOURCE_MODES
        assert payload["data_unavailable"] is True  # 缺数据不造数（旧字段）

    def test_x_request_id_echoed_and_invalid_replaced(self):
        header = "req-0123456789abcdef0123456789abcdef"
        h = _get("/api/d1/research", headers={"X-Request-Id": header})
        payload = E.ok_payload(h, "d1.research.get", {"ok": True})
        assert payload["request_id"] == header
        assert getattr(h, "_ifs_request_id", None) == header

        bad = _get("/api/d1/research", headers={"X-Request-Id": "not-a-req-id"})
        payload2 = E.ok_payload(bad, "d1.research.get", {"ok": True})
        assert payload2["request_id"] != "not-a-req-id"
        assert REQUEST_ID_RE.match(payload2["request_id"])

    def test_request_ids_unique(self):
        first = E.ok_payload(_get("/api/d1/research"), "d1.research.get", {"ok": True})
        second = E.ok_payload(_get("/api/d1/research"), "d1.research.get", {"ok": True})
        assert first["request_id"] != second["request_id"]


# ══ 2. 错误契约：error/v1 + error_detail + 旧 error 兼容 ═══════════════
class TestErrorContract:
    @staticmethod
    def _assert_error(payload, legacy_code, http_status, mapped_code):
        assert payload["ok"] is False
        assert payload["schema_version"] == "error/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["error"] == legacy_code  # 旧字符串兼容保留
        detail = payload["error_detail"]
        assert detail["code"] == mapped_code
        assert detail["code"] in ifs.ERROR_CODES
        assert isinstance(detail["message"], str) and detail["message"]
        assert detail["retryable"] is ifs.is_retryable(mapped_code)
        assert detail["details"] == {"legacy_code": legacy_code,
                                     "http_status": http_status}
        assert isfs_error_status(mapped_code) == http_status

    def test_decision_bad_tier(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post("/api/d1/decision", {**DECISION_BODY, "tier": "WATCH"})
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, "bad_tier", 400, "invalid_parameter")

    def test_handoff_bad_target_action(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
        h = _post("/api/d1/handoff", {**HANDOFF_BODY, "action": "trade"})
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, "bad_target_action", 400, "bad_target_action")

    def test_meeting_bad_action(self, tmp_path, monkeypatch):
        from agent_platform import d1_meeting as M
        import api_d1_meeting as A

        monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
        h = _post("/api/d1/meeting", {"action": "nope"})
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, "bad_action", 400, "invalid_parameter")

    def test_training_episode_not_found(self, tmp_path, monkeypatch):
        import api_d1_training as A

        episodes = tmp_path / "episodes.jsonl"
        episodes.write_text("", encoding="utf-8")
        monkeypatch.setenv("D1_TRAINING_EPISODES_PATH", str(episodes))
        h = _get("/api/d1/training/episodes/ep-20261104-ABCDEF12")
        A.handle_get(h, "")
        assert h.status == 404
        self._assert_error(h.payload, "episode_not_found", 404, "not_found")

    def test_error_envelope_validates_against_schema(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post("/api/d1/decision", {**DECISION_BODY, "tier": "WATCH"})
        A.handle_post(h)
        _error_schema_validator().validate(_envelope_view_of_error(h.payload))

    def test_legacy_error_string_compat(self, tmp_path, monkeypatch):
        """旧页面/旧测试读 payload['error'] 字符串仍可用（兼容期）。"""
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post("/api/d1/decision", {**DECISION_BODY, "tier": "WATCH"})
        A.handle_post(h)
        assert h.payload["error"] == "bad_tier"
        assert h.payload["error_detail"]["details"]["legacy_code"] == "bad_tier"

    def test_legacy_code_mapping_table(self):
        cases = (
            ("bad_tier", 400, "invalid_parameter"),
            ("missing_code", 400, "invalid_parameter"),
            ("bad_target_action", 400, "bad_target_action"),
            ("not_paper", 403, "not_paper"),
            ("episode_not_found", 404, "not_found"),
            ("body_too_large", 413, "invalid_request"),
            ("weird_failure", 500, "internal_error"),
            ("too many requests: 写端点每分钟限 30 次", 429, "rate_limited"),
        )
        for legacy, status, expected in cases:
            assert E.ifs_error_code(legacy, status) == expected, legacy

    def test_compat_gap_error_field_is_legacy_string(self, tmp_path, monkeypatch):
        """已知兼容期差异（F1c 前）：payload['error'] 仍是字符串，非 error/v1 对象。

        归一视图可通过 schema；原始 payload 直接过严格 schema 会失败——
        这是 IFS-03 定稿前的兼容期形态，不写成已符合。
        """
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post("/api/d1/decision", {**DECISION_BODY, "tier": "WATCH"})
        A.handle_post(h)
        with pytest.raises(jsonschema.ValidationError):
            _error_schema_validator().validate(h.payload)


def isfs_error_status(code):
    """错误码 → 登记 HTTP 状态（与 ifs.error_http_status 同名但前缀避免误用）。"""
    return ifs.error_http_status(code)


# ══ 3. auth：D1 写接口 token 闸门（handler 级，不启服务） ═══════════════
class TestWriteAuthContract:
    @pytest.mark.parametrize("path", sorted(D1_WRITE_PATHS))
    def test_d1_write_paths_are_registered_mutation(self, path):
        assert path in portal_api._MUTATION_POSTS

    @pytest.mark.parametrize("path,module_name", sorted(D1_WRITE_PATHS.items()))
    def test_write_without_token_403_and_handler_not_called(
        self, path, module_name, monkeypatch
    ):
        module = __import__(module_name)
        called = {"n": 0}
        monkeypatch.setattr(module, "handle_post",
                            lambda handler: called.__setitem__("n", called["n"] + 1))
        h = FakeHandler(path, b"{}", token_ok=False)
        portal_api.Handler.do_POST(h)
        assert h.status in (401, 403), "未授权写接口应 401/403，实际 %r" % (h.status,)
        assert h.status == 403
        assert called["n"] == 0, "未授权请求不得进入业务 handler（防止无 token 写入）"
        assert str(h.payload.get("error", "")).startswith("forbidden")

    @pytest.mark.parametrize("path,module_name", sorted(D1_WRITE_PATHS.items()))
    def test_write_with_token_reaches_handler(self, path, module_name, monkeypatch):
        module = __import__(module_name)
        calls = []
        monkeypatch.setattr(module, "handle_post",
                            lambda handler: (calls.append(handler.path),
                                             handler._json({"ok": True}, 200)))
        h = FakeHandler(path, b"{}", token_ok=True)
        portal_api.Handler.do_POST(h)
        assert h.status == 200
        assert calls == [path]

    def test_write_requires_token_before_body_read(self, monkeypatch):
        """未授权时不应读取/解析 body（无副作用、无写盘路径）。"""
        module = __import__("api_d1_decision")
        monkeypatch.setattr(module, "handle_post",
                            lambda handler: pytest.fail("业务 handler 被调用"))
        h = FakeHandler("/api/d1/decision",
                        json.dumps(DECISION_BODY).encode("utf-8"), token_ok=False)
        h.rfile = io.BytesIO(b"not-json")  # 若读过 body 会 400；应先 403
        portal_api.Handler.do_POST(h)
        assert h.status == 403

    @pytest.mark.xfail(strict=True,
                       reason="缺口：写接口 403 响应是 portal_api 裸 legacy dict"
                              "（未经 ifs_envelope，无 error/v1/request_id）")
    def test_unauthorized_response_should_follow_error_v1(self):
        h = FakeHandler("/api/d1/decision", b"{}", token_ok=False)
        portal_api.Handler.do_POST(h)
        payload = h.payload
        assert payload["ok"] is False
        assert payload["schema_version"] == "error/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["error"]["code"] == "forbidden"
        _error_schema_validator().validate(payload)


# ══ 4. 幂等 / 重复写保护 ══════════════════════════════════════════════
class TestIdempotencyContract:
    def test_duplicate_decision_id_rejected_without_double_write(
        self, tmp_path, monkeypatch
    ):
        """现有行为：同 decision_id 重复提交 → 400 duplicate_decision，账本仅 1 行。"""
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        ledger = tmp_path / "d1.jsonl"
        monkeypatch.setattr(D, "DEFAULT_LEDGER", ledger)
        first = _post("/api/d1/decision", dict(DECISION_BODY))
        A.handle_post(first)
        assert first.status == 200
        second = _post("/api/d1/decision", dict(DECISION_BODY))
        A.handle_post(second)
        assert second.status == 400
        assert second.payload["error"] == "duplicate_decision"
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_idempotent_replay_code_registered_but_unused_by_d1(self):
        """IFS registry 已登记 idempotent_replay，但 D1 写模块未使用（缺口证据）。"""
        assert "idempotent_replay" in ifs.ERROR_CODES
        assert ifs.ERROR_CODES["idempotent_replay"]["http"] == 200
        for module_name in ("portal/api_d1_decision.py", "portal/api_d1_handoff.py",
                            "portal/api_d1_meeting.py"):
            source = (ROOT / module_name).read_text(encoding="utf-8")
            assert "idempotent_replay" not in source, module_name

    @pytest.mark.xfail(strict=True,
                       reason="缺口：D1 写接口无 idempotency_key/idempotent_replay"
                              "（当前同 decision_id 第二次提交 400 duplicate_decision，"
                              "未按 IFS 返回 200 原记录 + warning）")
    def test_idempotency_key_replay_contract(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        ledger = tmp_path / "d1.jsonl"
        monkeypatch.setattr(D, "DEFAULT_LEDGER", ledger)
        body = dict(DECISION_BODY, idempotency_key="idem-decision-001")
        first = _post("/api/d1/decision", body)
        A.handle_post(first)
        assert first.status == 200
        replay = _post("/api/d1/decision", body)
        A.handle_post(replay)
        assert replay.status == 200, "幂等重放应按 IFS 返回 200 原记录"
        assert replay.payload["decision"]["decision_id"] == DECISION_ID
        assert any("idempotent_replay" in str(w.get("code"))
                   for w in replay.payload.get("warnings") or [])
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
