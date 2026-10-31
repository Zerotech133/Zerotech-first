# -*- coding: utf-8 -*-
"""dashboard_api Phase 5-A 只读 API 单测（WO-STRATEGY-API-20261031）。

覆盖验收口径：
1) GET /strategy_health.json：无文件/缺目录 → 200 insufficient（不 500，字段
   status/rows/readonly/note/generated_at 齐全）；有周报 latest.json → 200 原样
   返回；坏 JSON / 顶层非 dict → 500 JSON；只读（周报文件字节不变、不落新文件）；
2) POST /api/strategy/validate_ir：合法 composition_ir/v1 IR → 200 只读摘要
   （readonly / side_effects / safety / execution.dry_run）；非法 IR → 400 + errors；
   坏 JSON body / 空 body / 超限 / 非对象 → 400；不执行、不下单、不写 ledger、
   不持久化、不写盘；
3) 旧路由回退：未知 GET 回落原 do_GET、未知 POST 回落原 do_POST（无原 do_POST →
   405）；既有只读路由 /pipeline_graph.json 等仍 200；
4) 不启动服务：Handler 替身直调 do_GET/do_POST，不监听端口、不连 PG。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re

from strategy_layer import dashboard_api as da

_NOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


# ── Handler 替身（记录状态/头/body；原 do_GET/do_POST 置 orig_called） ──
class _FakeHandler:
    _dashboard_api_wrapped = False

    def __init__(self, path, body=b""):
        self.path = path
        self.body = body
        self.status = None
        self.headers = {}
        self.written = b""
        self.orig_called = False
        self.rfile = io.BytesIO(body)

    def do_GET(self):  # 主仪表盘既有 do_GET 替身
        self.orig_called = True

    def do_POST(self):  # 主仪表盘既有 do_POST 替身（如 /api/trigger）
        self.orig_called = True

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[str(key).lower()] = str(value)

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.written += data


def _handler_cls(with_orig_get=True, with_orig_post=True):
    attrs = {}
    if with_orig_get:
        attrs["do_GET"] = _FakeHandler.do_GET
    if with_orig_post:
        attrs["do_POST"] = _FakeHandler.do_POST
    return type("Handler", (_FakeHandler,), attrs)


def _call_get(path, with_orig_get=True):
    cls = _handler_cls(with_orig_get=with_orig_get)
    da.register_routes(cls)
    handler = cls(path)
    handler.do_GET()
    return handler


def _call_post(path, payload, content_length=None):
    if isinstance(payload, (dict, list)):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = bytes(payload)
    cls = _handler_cls()
    da.register_routes(cls)
    handler = cls(path, raw)
    handler.headers["Content-Length"] = str(
        len(raw) if content_length is None else content_length
    )
    handler.do_POST()
    return handler


def _json_body(handler):
    return json.loads(handler.written.decode("utf-8"))


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _assert_json_headers(handler):
    assert handler.headers.get("content-type") == "application/json; charset=utf-8"
    assert handler.headers.get("cache-control") == "no-store"
    assert handler.headers.get("content-length") == str(len(handler.written))
    assert handler.headers.get("access-control-allow-origin") == "*"


# ── 合成 IR（最小合法：universe→strategy→allocator→execution） ───────
def _valid_ir():
    return {
        "schema": "composition_ir/v1",
        "pipeline_id": "pc-api-test-001",
        "version": "1.0.0",
        "owner": "quant-classic",
        "tenant": "official",
        "mode": "SHADOW",
        "nodes": [
            {"id": "u", "type": "universe", "params": {}},
            {
                "id": "s",
                "type": "strategy",
                "strategy_id": "chan_monitor",
                "params": {},
            },
            {"id": "a", "type": "allocator", "params": {}},
            {"id": "x", "type": "execution", "params": {"dry_run": True}},
        ],
        "edges": [
            {"from": "u", "to": "s", "port": "universe"},
            {"from": "s", "to": "a", "port": "signals"},
            {"from": "a", "to": "x", "port": "allocations"},
        ],
        "risk_policy": {"policy_id": "rp-api", "max_position_pct": 10.0},
        "publish": {"enabled": False},
    }


_WEEKLY_REPORT = {
    "schema": "strategy_health_weekly/v1",
    "status": "OK",
    "readonly": True,
    "side_effects": [],
    "generated_at": "2026-10-31T20:00:00",
    "period": {"days_requested": 7, "rows": 42},
    "strategies": [{"strategy_id": "chan_monitor", "health_level": "green"}],
    "bias_tickets": [],
    "notes": ["只读周报"],
}


# ── 1. GET /strategy_health.json ─────────────────────────────────────
class TestStrategyHealthRoute:
    def test_missing_dir_returns_insufficient_200(self, monkeypatch, tmp_path):
        missing = tmp_path / "automation_runs" / "strategy_health" / "latest.json"
        assert not missing.parent.exists()  # 缺目录也不 500
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(missing))
        handler = _call_get("/strategy_health.json")
        assert handler.status == 200
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["status"] == "insufficient"
        assert payload["rows"] == 0
        assert payload["readonly"] is True
        assert payload["note"] == "no weekly report yet"
        assert _NOW_RE.match(payload["generated_at"]), payload["generated_at"]
        assert handler.orig_called is False

    def test_missing_file_returns_insufficient_200(self, monkeypatch, tmp_path):
        missing = tmp_path / "latest.json"
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(missing))
        handler = _call_get("/strategy_health.json")
        assert handler.status == 200
        payload = _json_body(handler)
        assert payload["status"] == "insufficient"
        assert payload["rows"] == 0
        assert payload["readonly"] is True
        assert payload["note"] == "no weekly report yet"

    def test_existing_report_returned_verbatim(self, monkeypatch, tmp_path):
        report_file = tmp_path / "latest.json"
        report_file.write_text(
            json.dumps(_WEEKLY_REPORT, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(report_file))
        handler = _call_get("/strategy_health.json")
        assert handler.status == 200
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload == _WEEKLY_REPORT
        assert payload["readonly"] is True
        assert payload["status"] == "OK"
        assert payload["period"]["rows"] == 42
        assert handler.written.decode("utf-8") == json.dumps(
            _WEEKLY_REPORT, ensure_ascii=False
        )

    def test_bad_json_500(self, monkeypatch, tmp_path):
        bad = tmp_path / "latest.json"
        bad.write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(bad))
        handler = _call_get("/strategy_health.json")
        assert handler.status == 500
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "非法 JSON" in payload["error"]

    def test_non_dict_json_500(self, monkeypatch, tmp_path):
        weird = tmp_path / "latest.json"
        weird.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(weird))
        handler = _call_get("/strategy_health.json")
        assert handler.status == 500
        assert "顶层必须为 dict" in _json_body(handler)["error"]

    def test_payload_function_direct(self, monkeypatch, tmp_path):
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(tmp_path / "none.json"))
        payload, code = da.strategy_health_payload()
        assert code == 200 and payload["status"] == "insufficient"
        bad = tmp_path / "bad.json"
        bad.write_text("oops", encoding="utf-8")
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(bad))
        payload, code = da.strategy_health_payload()
        assert code == 500 and payload["ok"] is False

    def test_health_route_readonly(self, monkeypatch, tmp_path):
        report_file = tmp_path / "latest.json"
        report_file.write_text(
            json.dumps(_WEEKLY_REPORT, ensure_ascii=False), encoding="utf-8"
        )
        snapshot = report_file.read_bytes()
        monkeypatch.setattr(da, "STRATEGY_HEALTH_PATH", str(report_file))
        assert _call_get("/strategy_health.json").status == 200
        assert report_file.read_bytes() == snapshot
        assert sorted(item.name for item in tmp_path.iterdir()) == ["latest.json"]


# ── 2. POST /api/strategy/validate_ir ────────────────────────────────
class TestValidateIrRoute:
    def test_valid_ir_returns_readonly_summary(self):
        handler = _call_post("/api/strategy/validate_ir", _valid_ir())
        assert handler.status == 200
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is True
        assert payload["readonly"] is True
        assert payload["side_effects"] == []
        assert payload["pipeline"]["pipeline_id"] == "pc-api-test-001"
        assert payload["pipeline"]["mode"] == "SHADOW"
        compiled = payload["compiled"]
        assert compiled["schema"] == "compiled_pipeline/v1"
        assert compiled["readonly"] is True
        assert compiled["side_effects"] == []
        assert compiled["safety"] == {
            "executes": False,
            "places_orders": False,
            "writes_ledger": False,
            "publishes": False,
        }
        assert compiled["execution"]["enabled"] is False
        assert compiled["execution"]["dry_run"] is True
        assert compiled["execution"]["places_orders"] is False
        assert compiled["execution"]["writes_ledger"] is False
        assert compiled["nodes"] == 4
        assert compiled["edges"] == 3
        assert compiled["topological_order"] == ["u", "s", "a", "x"]
        assert compiled["strategies"] == ["chan_monitor"]
        assert handler.orig_called is False

    def test_unknown_strategy_400_errors(self):
        doc = _valid_ir()
        doc["nodes"][1]["strategy_id"] = "ghost_strategy"
        handler = _call_post("/api/strategy/validate_ir", doc)
        assert handler.status == 400
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert isinstance(payload["errors"], list) and payload["errors"]
        assert any("未知策略" in error for error in payload["errors"])

    def test_missing_required_field_400_errors(self):
        doc = _valid_ir()
        doc.pop("risk_policy")
        handler = _call_post("/api/strategy/validate_ir", doc)
        assert handler.status == 400
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert any("缺必填字段" in error for error in payload["errors"])

    def test_bad_json_body_400(self):
        handler = _call_post("/api/strategy/validate_ir", "{not-json")
        assert handler.status == 400
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "非法 JSON 请求体" in payload["error"]

    def test_oversize_body_400(self):
        raw = b"x" * (da.MAX_IR_BODY_BYTES + 1)
        handler = _call_post("/api/strategy/validate_ir", raw)
        assert handler.status == 400
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "超长" in payload["error"]

    def test_empty_body_400(self):
        handler = _call_post(
            "/api/strategy/validate_ir", b"", content_length=0
        )
        assert handler.status == 400
        assert "缺失或超长" in _json_body(handler)["error"]

    def test_non_object_body_400(self):
        handler = _call_post("/api/strategy/validate_ir", [1, 2, 3])
        assert handler.status == 400
        assert "必须为 JSON 对象" in _json_body(handler)["error"]

    def test_validate_ir_payload_does_not_write(self, monkeypatch, tmp_path):
        cards_path = os.path.join(
            os.path.dirname(os.path.abspath(da.__file__)), "strategy_cards.json"
        )
        before = _sha256(cards_path)
        monkeypatch.chdir(tmp_path)
        handler = _call_post("/api/strategy/validate_ir", _valid_ir())
        assert handler.status == 200
        assert _sha256(cards_path) == before
        assert list(tmp_path.iterdir()) == []  # 不落盘/不持久化

    def test_validate_ir_payload_direct(self):
        payload, code = da.validate_ir_payload(_valid_ir())
        assert code == 200 and payload["ok"] is True
        payload, code = da.validate_ir_payload([1, 2])
        assert code == 400 and payload["ok"] is False
        doc = _valid_ir()
        doc["mode"] = "LIVE"
        payload, code = da.validate_ir_payload(doc)
        assert code == 400 and payload["errors"]


# ── 3. 旧路由回退 / 既有只读路由不变 ─────────────────────────────────
class TestLegacyRoutes:
    def test_unknown_get_falls_back(self):
        for path in ("/api/overview", "/strategy_health.jsonx"):
            handler = _call_get(path)
            assert handler.orig_called is True, path
            assert handler.written == b"", path

    def test_pipeline_graph_route_still_served(self):
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 200
        assert _json_body(handler)["counts"]["cards"] == 10

    def test_unknown_post_falls_back(self):
        handler = _call_post("/api/trigger", b"{}")
        assert handler.orig_called is True
        assert handler.written == b""

    def test_unknown_post_no_orig_405(self):
        cls = _handler_cls(with_orig_get=False, with_orig_post=False)
        da.register_routes(cls)
        handler = cls("/api/trigger", b"{}")
        handler.headers["Content-Length"] = "2"
        handler.do_POST()
        assert handler.status == 405
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "只读服务" in payload["error"]

    def test_unknown_get_no_orig_404(self):
        handler = _call_get("/api/overview", with_orig_get=False)
        assert handler.status == 404
        assert "未知路由" in _json_body(handler)["error"]
