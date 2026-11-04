# -*- coding: utf-8 -*-
"""IFS F1b：D1 读接口 envelope / request_id 接线测试（WO-IFS-ENVELOPE-20261104）。

**离线**：不连真实服务/网络/生产数据；用 FakeHandler 直调各 API 模块与
``portal/ifs_envelope.py``，ledger/meetings/episodes 全部指向 tmp。

覆盖：
1) interface 映射与 `INTERFACE_REGISTRY_v1.yaml` 对齐（interface_id/path/schema/真源）；
2) 成功响应：ok/schema_version/request_id/source/data/warnings + **旧顶层字段保留**；
3) 失败响应：error/v1 + request_id + error 旧字符串 + error_detail(code/retryable/details)；
4) request_id：沿用合法 X-Request-Id / 非法换新 / 非空唯一；portal_api 回写响应头；
5) training.episode.get 兼容碰撞：旧 schema_version/source 优先，IFS source 放 source_detail；
6) 只读：GET 无写副作用；无执行字段；未登记路径原样透传。
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "portal"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import ifs_envelope as E  # noqa: E402
from agent_platform.contracts import ifs  # noqa: E402

REGISTRY_PATH = ROOT / "docs" / "standards" / "INTERFACE_REGISTRY_v1.yaml"
REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{32}$")
WIRED_INTERFACES = (
    "d1.research.get",
    "d1.decision.create",
    "d1.decision.list",
    "d1.meeting.get",
    "d1.meeting.create",
    "d1.handoff.create",
    "d1.handoff.list",
    "d1.watch_plans.list",
    "training.episode.get",
)


class FakeHandler:
    """最小 Handler 替身：记录 _json(obj, code)，支持 headers/X-Request-Id。"""

    def __init__(self, path="/", body=b"{}", headers=None):
        self.path = path
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Length", str(len(body)))
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None
        self.command = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _post(path, obj):
    return FakeHandler(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _get(path, headers=None):
    return FakeHandler(path, b"", headers=headers)


@pytest.fixture(scope="module")
def registry():
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


# ── 1. interface 映射 / registry 对齐 ───────────────────────────────
class TestInterfaceMapping:
    def test_required_interfaces_wired(self):
        for interface_id in WIRED_INTERFACES:
            assert interface_id in E.INTERFACES, "未接线: %s" % interface_id
            meta = E.INTERFACES[interface_id]
            for field in ("path", "schema_version", "source_of_truth", "source_mode"):
                assert meta.get(field), "%s 缺 %s" % (interface_id, field)

    def test_mapping_matches_registry(self, registry):
        entries = {item["id"]: item for item in registry["interfaces"]}
        for interface_id, meta in E.INTERFACES.items():
            assert interface_id in entries, "registry 缺: %s" % interface_id
            entry = entries[interface_id]
            assert meta["path"] == entry["path"], interface_id
            assert meta["schema_version"] == entry["schema"], interface_id
            assert meta["source_of_truth"] == entry["source_of_truth"]["ref"], interface_id

    def test_path_inference(self):
        assert E.interface_for_path("/api/d1/research") == "d1.research.get"
        assert E.interface_for_path("/api/d1/decisions") == "d1.decision.list"
        assert E.interface_for_path("/api/d1/decision") == "d1.decision.create"
        assert E.interface_for_path("/api/d1/handoffs") == "d1.handoff.list"
        assert E.interface_for_path("/api/d1/handoff") == "d1.handoff.create"
        assert E.interface_for_path("/api/d1/watch-plans") == "d1.watch_plans.list"
        assert E.interface_for_path("/api/d1/meeting", "GET") == "d1.meeting.get"
        assert E.interface_for_path("/api/d1/meeting", "POST") == "d1.meeting.create"
        assert (
            E.interface_for_path("/api/d1/training/episodes/ep-20261104-ABCDEF12")
            == "training.episode.get"
        )
        assert E.interface_for_path("/api/other", fallback=None) is None


# ── 2. 成功响应 envelope + 旧字段兼容 ───────────────────────────────
class TestSuccessEnvelopes:
    def test_research_envelope_and_legacy_fields(self):
        legacy = {
            "ok": True,
            "mode": "d1_research",
            "items": [],
            "summary": {"total": 0},
            "team": [],
            "watch_methods": {},
            "generated_at": "2026-11-04T02:00:00",
        }
        h = _get("/api/d1/research")
        payload = E.ok_payload(h, "d1.research.get", legacy)
        assert payload["ok"] is True
        assert payload["schema_version"] == "d1_research/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["source"]["interface_id"] == "d1.research.get"
        assert payload["warnings"] == []
        assert payload["data"] == legacy
        for key in ("mode", "items", "summary", "team", "watch_methods", "generated_at"):
            assert key in payload, "旧字段丢失: %s" % key
        assert ifs.validate_envelope(payload) == []

    def test_decision_create_and_list(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        body = {
            "code": "002445.SZ",
            "tier": "BUY",
            "confidence": 0.7,
            "rationale": "3买回抽不破中枢上沿，paper-only 观察",
            "evidence": ["bridge"],
            "paper_only": True,
        }
        h = _post("/api/d1/decision", body)
        A.handle_post(h)
        assert h.status == 200
        payload = h.payload
        assert payload["schema_version"] == "d1_decision/v1"
        assert payload["source"]["interface_id"] == "d1.decision.create"
        assert payload["data"]["decision"]["tier"] == "BUY"
        assert payload["decision"]["tier"] == "BUY"  # 旧顶层字段
        assert payload["paper_only"] is True and payload["not_a_trade"] is True
        assert ifs.validate_envelope(payload) == []

        g = _get("/api/d1/decisions")
        A.handle_get(g, "")
        assert g.status == 200
        assert g.payload["schema_version"] == "d1_decision/v1"
        assert g.payload["source"]["interface_id"] == "d1.decision.list"
        assert "002445.SZ" in g.payload["latest_by_code"]  # 旧字段
        assert g.payload["data"]["latest_by_code"] == g.payload["latest_by_code"]

    def test_meeting_create_and_get(self, tmp_path, monkeypatch):
        from agent_platform import d1_meeting as M
        import api_d1_meeting as A

        monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
        h = _post(
            "/api/d1/meeting",
            {"action": "create", "code": "002445.SZ", "topic": "测试", "asof": "2026-11-04"},
        )
        A.handle_post(h)
        assert h.status == 200
        assert h.payload["source"]["interface_id"] == "d1.meeting.create"
        assert h.payload["schema_version"] == "d1_meeting/v1"
        assert h.payload["meeting"]["meeting_id"]  # 旧字段
        assert ifs.validate_envelope(h.payload) == []

        g = _get("/api/d1/meeting")
        A.handle_get(g, "")
        assert g.status == 200
        assert g.payload["source"]["interface_id"] == "d1.meeting.get"
        assert g.payload["summary"]["total"] == 1  # 旧字段
        assert ifs.validate_envelope(g.payload) == []

    def test_handoff_create_and_list(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
        body = {
            "action_name": "create",
            "code": "002445.SZ",
            "target": "WA1",
            "action": "watch",
            "rationale": "回踩观察",
            "buy_zone": [2.98, 3.05],
            "paper_only": True,
        }
        h = _post("/api/d1/handoff", body)
        A.handle_post(h)
        assert h.status == 200
        assert h.payload["source"]["interface_id"] == "d1.handoff.create"
        assert h.payload["schema_version"] == "d1_handoff/v2"
        assert h.payload["handoff"]["receipt_id"]  # 旧字段
        assert ifs.validate_envelope(h.payload) == []

        g = _get("/api/d1/handoffs")
        A.handle_get(g, "")
        assert g.status == 200
        assert g.payload["source"]["interface_id"] == "d1.handoff.list"
        assert g.payload["summary"]["total"] == 1  # 旧字段
        assert ifs.validate_envelope(g.payload) == []

    def test_watch_plans_envelope(self):
        legacy = {
            "ok": True,
            "schema_version": "watch_plan/v1",
            "count": 0,
            "watch_plans": [],
            "readonly": True,
            "paper_only": True,
            "executes": False,
        }
        h = _get("/api/d1/watch-plans")
        E.send(h, legacy)
        payload = h.payload
        assert payload["source"]["interface_id"] == "d1.watch_plans.list"
        assert payload["schema_version"] == "watch_plan/v1"
        assert payload["count"] == 0 and payload["executes"] is False  # 旧字段
        assert ifs.validate_envelope(payload) == []

    def test_training_envelope_collision_compat(self, tmp_path, monkeypatch):
        import api_d1_training as A

        monkeypatch.setenv("D1_TRAINING_EPISODES_PATH", str(tmp_path / "episodes.jsonl"))
        h = _get("/api/d1/training/episodes")
        A.handle_get(h, "")
        assert h.status == 200
        payload = h.payload
        assert payload["ok"] is True
        # 兼容碰撞：training 旧契约字段优先保留
        assert payload["schema_version"] == "training_episode/v1"
        assert isinstance(payload["source"], str) and "episodes.jsonl" in payload["source"]
        assert payload["source_detail"]["interface_id"] == "training.episode.get"
        assert payload["source_detail"]["source_of_truth"].endswith("episodes.jsonl")
        assert payload["data_unavailable"] is True  # 旧字段（缺数据不造数）
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["data"]["source"] == payload["source"]


# ── 3. 错误响应 error/v1 + 旧字段兼容 ───────────────────────────────
class TestErrorEnvelopes:
    @staticmethod
    def _assert_error(payload, *, legacy_error, mapped_code, http_status):
        assert payload["ok"] is False
        assert payload["schema_version"] == "error/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["error"] == legacy_error  # 旧字符串保留
        detail = payload["error_detail"]
        assert detail["code"] == mapped_code
        assert detail["retryable"] is ifs.is_retryable(mapped_code)
        assert detail["details"] == {
            "legacy_code": legacy_error,
            "http_status": http_status,
        }
        assert isinstance(detail["message"], str) and detail["message"]

    def test_decision_bad_tier(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post(
            "/api/d1/decision",
            {"code": "002445.SZ", "tier": "WATCH", "confidence": 0.7,
             "rationale": "回抽不破中枢上沿，paper-only 观察",
             "evidence": ["bridge"], "paper_only": True},
        )
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, legacy_error="bad_tier",
                           mapped_code="invalid_parameter", http_status=400)

    def test_handoff_bad_target(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
        h = _post(
            "/api/d1/handoff",
            {"action_name": "create", "code": "X", "target": "WA1",
             "action": "trade", "rationale": "bad", "paper_only": True},
        )
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, legacy_error="bad_target_action",
                           mapped_code="bad_target_action", http_status=400)

    def test_meeting_bad_action(self, tmp_path, monkeypatch):
        from agent_platform import d1_meeting as M
        import api_d1_meeting as A

        monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
        h = _post("/api/d1/meeting", {"action": "nope"})
        A.handle_post(h)
        assert h.status == 400
        self._assert_error(h.payload, legacy_error="bad_action",
                           mapped_code="invalid_parameter", http_status=400)

    def test_training_episode_not_found(self, tmp_path, monkeypatch):
        import api_d1_training as A

        episodes = tmp_path / "episodes.jsonl"
        episodes.write_text("", encoding="utf-8")
        monkeypatch.setenv("D1_TRAINING_EPISODES_PATH", str(episodes))
        h = _get("/api/d1/training/episodes/ep-20261104-ABCDEF12")
        A.handle_get(h, "")
        assert h.status == 404
        self._assert_error(h.payload, legacy_error="episode_not_found",
                           mapped_code="not_found", http_status=404)

    def test_wrong_path_not_found(self):
        import api_d1_decision as A

        h = _post("/api/d1/other", {})
        A.handle_post(h)
        assert h.status == 404
        assert h.payload["schema_version"] == "error/v1"
        assert h.payload["error"] == "not_found"

    def test_error_code_mapping(self):
        cases = (
            ("bad_tier", 400, "invalid_parameter"),
            ("missing_code", 400, "invalid_parameter"),
            ("bad_target_action", 400, "bad_target_action"),
            ("execution_field_forbidden", 400, "execution_field_forbidden"),
            ("not_paper", 403, "not_paper"),
            ("episode_not_found", 404, "not_found"),
            ("body_too_large", 413, "invalid_request"),
            ("weird_failure", 500, "internal_error"),
            ("too many requests: 写端点每分钟限 30 次", 429, "rate_limited"),
            ("receipt_not_found", 404, "not_found"),
        )
        for legacy, status, expected in cases:
            assert E.ifs_error_code(legacy, status) == expected, legacy


# ── 4. request_id / X-Request-Id ────────────────────────────────────
class TestRequestId:
    def test_valid_header_echoed(self):
        header = "req-0123456789abcdef0123456789abcdef"
        h = _get("/api/d1/research", headers={"X-Request-Id": header})
        payload = E.ok_payload(h, "d1.research.get", {"ok": True})
        assert payload["request_id"] == header
        assert getattr(h, "_ifs_request_id", None) == header

    def test_invalid_header_replaced(self):
        h = _get("/api/d1/research", headers={"X-Request-Id": "bad-id"})
        payload = E.ok_payload(h, "d1.research.get", {"ok": True})
        assert payload["request_id"] != "bad-id"
        assert REQUEST_ID_RE.match(payload["request_id"])

    def test_request_ids_unique(self):
        first = E.ok_payload(_get("/api/d1/research"), "d1.research.get", {"ok": True})
        second = E.ok_payload(_get("/api/d1/research"), "d1.research.get", {"ok": True})
        assert first["request_id"] != second["request_id"]

    def test_portal_api_writes_x_request_id_header(self):
        source = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
        assert "X-Request-Id" in source
        assert "_ifs_request_id" in source

    def test_error_request_id_present(self):
        h = _get("/api/d1/decision")
        payload = E.error_payload(h, "d1.decision.create", {"ok": False, "error": "bad_body"}, 400)
        assert REQUEST_ID_RE.match(payload["request_id"])


# ── 5. 只读 / 透传 / 无执行字段 ─────────────────────────────────────
class TestReadonlyAndPassthrough:
    def test_unregistered_path_passthrough(self):
        h = _get("/api/other")
        original = {"foo": 1}
        E.send(h, original, 200)
        assert h.payload == original
        assert "schema_version" not in h.payload

    def test_get_handoff_list_no_write(self, tmp_path, monkeypatch):
        from agent_platform import d1_handoff as H
        import api_d1_handoff as A

        ledger = tmp_path / "h.jsonl"
        monkeypatch.setattr(H, "DEFAULT_LEDGER", ledger)
        h = _get("/api/d1/handoffs")
        A.handle_get(h, "")
        assert not ledger.exists(), "GET 不得落账/写盘"
        assert h.status == 200

    def test_no_execution_fields_added(self, tmp_path, monkeypatch):
        from agent_platform import d1_decision as D
        import api_d1_decision as A

        monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
        h = _post(
            "/api/d1/decision",
            {"code": "002445.SZ", "tier": "BUY", "confidence": 0.6,
             "rationale": "回抽不破中枢上沿，paper-only 观察",
             "evidence": ["bridge"], "paper_only": True},
        )
        A.handle_post(h)
        for field in ("order", "qty", "quantity", "broker", "execute", "fill"):
            assert field not in h.payload
            assert field not in h.payload["data"]
        assert h.payload["not_a_trade"] is True
        assert h.payload["paper_only"] is True
