# -*- coding: utf-8 -*-
"""dashboard_api Phase 4-A 只读路由单测（WO-PHASE4-ROUTES-20261031）。

覆盖验收口径：
1) GET /portal/static/tokens.css：200 + ``text/css; charset=utf-8`` + no-store +
   CORS + Content-Length 精确，返回 repo/portal/static/tokens.css 原字节；query 兼容；
   nav.js / shortscreen.js 可选白名单同规则；
2) 白名单外文件、路径穿越（../、反斜杠、%2e%2e、绝对路径片段、空文件名）→ 404
   JSON 错误且不泄露任何文件内容；白名单文件缺失 → 404 JSON；
3) GET /pipeline_templates.json：200 + ``application/json; charset=utf-8`` +
   no-store，模板计数 = 3（trend_pullback / trend_reversal / nine_dim_chan）；
   缺文件 → 503 JSON；坏 JSON / 顶层非 dict / templates 非 list → 500 JSON；
4) 旧路由零改动：/pipeline、/pipeline_graph.json、/canvas 仍各自 200；未知路径
   回落原 do_GET（无原 do_GET → 404 JSON 未知路由）；
5) 只读：tokens.css / pipeline_templates.json 字节不变；路由不落新文件；
   不启动服务（Handler 替身直调 do_GET，不监听端口、不连 PG）。
"""

from __future__ import annotations

import hashlib
import json
import os

from strategy_layer import dashboard_api as da

_STRATEGY_LAYER = os.path.dirname(os.path.abspath(da.__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_STRATEGY_LAYER))
_TOKENS_PATH = os.path.join(_REPO_ROOT, "portal", "static", "tokens.css")
_TEMPLATES_PATH = os.path.join(_STRATEGY_LAYER, "pipeline_templates.json")
_TEMPLATE_IDS = {"trend_pullback", "trend_reversal", "nine_dim_chan"}


# ── Handler 替身（记录状态/头/body；原 do_GET 置 orig_called） ─────────
class _FakeHandler:
    _dashboard_api_wrapped = False

    def __init__(self, path):
        self.path = path
        self.status = None
        self.headers = {}
        self.written = b""
        self.orig_called = False

    def do_GET(self):  # 主仪表盘既有 do_GET 替身（模拟 /api/overview）
        self.orig_called = True

    def do_POST(self):
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


def _handler_cls(with_orig_get=True):
    attrs = {"do_POST": _FakeHandler.do_POST}
    if with_orig_get:
        attrs["do_GET"] = _FakeHandler.do_GET
    return type("Handler", (_FakeHandler,), attrs)


def _call_get(path, with_orig_get=True):
    cls = _handler_cls(with_orig_get)
    da.register_routes(cls)
    handler = cls(path)
    handler.do_GET()
    return handler


def _json_body(handler):
    return json.loads(handler.written.decode("utf-8"))


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _assert_bytes_headers(handler, content_type):
    assert handler.headers.get("content-type") == content_type
    assert handler.headers.get("cache-control") == "no-store"
    assert handler.headers.get("content-length") == str(len(handler.written))
    assert handler.headers.get("access-control-allow-origin") == "*"


def _assert_json_headers(handler):
    _assert_bytes_headers(handler, "application/json; charset=utf-8")


# ── 1. tokens.css / 视觉三件套白名单 ─────────────────────────────────
class TestPortalStaticRoutes:
    def test_tokens_css_200(self):
        handler = _call_get("/portal/static/tokens.css")
        assert handler.status == 200
        _assert_bytes_headers(handler, "text/css; charset=utf-8")
        assert handler.orig_called is False
        assert handler.written == _read_bytes(_TOKENS_PATH)
        assert b":root" in handler.written

    def test_tokens_css_query_compatible(self):
        handler = _call_get("/portal/static/tokens.css?v=20261031")
        assert handler.status == 200
        _assert_bytes_headers(handler, "text/css; charset=utf-8")
        assert handler.written == _read_bytes(_TOKENS_PATH)

    def test_nav_and_shortscreen_whitelisted(self):
        for name in ("nav.js", "shortscreen.js"):
            handler = _call_get("/portal/static/" + name)
            assert handler.status == 200, name
            _assert_bytes_headers(handler, "application/javascript; charset=utf-8")
            assert handler.written == _read_bytes(
                os.path.join(_REPO_ROOT, "portal", "static", name)
            )
            assert handler.orig_called is False

    def test_payload_function_direct(self):
        body, ctype, error = da.portal_static_payload("tokens.css")
        assert error is None
        assert ctype == "text/css; charset=utf-8"
        assert body == _read_bytes(_TOKENS_PATH)
        body, ctype, error = da.portal_static_payload("../strategy_cards.json")
        assert body is None and ctype is None
        assert error is not None and error[1] == 404

    def test_unknown_static_file_404_json(self):
        handler = _call_get("/portal/static/secret.js")
        assert handler.status == 404
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "白名单" in payload["error"]
        assert "secret.js" in payload["error"]

    def test_path_traversal_rejected(self):
        traversal_paths = (
            "/portal/static/../strategy_layer/strategy_registry.json",
            "/portal/static/../../tools/check_ports.py",
            "/portal/static/..\\strategy_cards.json",
            "/portal/static/..%2fstrategy_cards.json",
            "/portal/static/%2e%2e/strategy_cards.json",
            "/portal/static/../../portal/static/tokens.css",
            "/portal/static/",
            "/portal/static//tokens.css",
        )
        for path in traversal_paths:
            handler = _call_get(path)
            assert handler.status == 404, path
            _assert_json_headers(handler)
            payload = _json_body(handler)
            assert payload["ok"] is False, path
            # 不得把目标文件内容带出来（registry/cards 特征串不出现）
            assert b"strategy_id" not in handler.written, path
            assert b"chan_monitor" not in handler.written, path

    def test_hit_traversal_target_content_not_leaked(self):
        """真实穿越目标（registry）返回 404 JSON，而非文件内容。"""
        handler = _call_get(
            "/portal/static/../strategy_layer/strategy_registry.json"
        )
        assert handler.status == 404
        assert "strategy_id".encode("utf-8") not in handler.written
        assert handler.orig_called is False

    def test_missing_whitelisted_file_404_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(da, "PORTAL_STATIC_DIR", str(tmp_path))
        handler = _call_get("/portal/static/tokens.css")
        assert handler.status == 404
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "不存在" in payload["error"]

    def test_static_route_does_not_write(self):
        before = _sha256(_TOKENS_PATH)
        for path in (
            "/portal/static/tokens.css",
            "/portal/static/nav.js",
            "/portal/static/../strategy_cards.json",
        ):
            _call_get(path)
        assert _sha256(_TOKENS_PATH) == before


# ── 2. pipeline_templates.json ───────────────────────────────────────
class TestPipelineTemplatesRoute:
    def test_templates_200_counts_three(self):
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 200
        _assert_json_headers(handler)
        assert handler.orig_called is False
        doc = _json_body(handler)
        assert "ok" not in doc  # 原样文档，不包 ok 外壳
        assert isinstance(doc["templates"], list)
        assert len(doc["templates"]) == 3
        assert {item["template_id"] for item in doc["templates"]} == _TEMPLATE_IDS
        assert isinstance(doc.get("_meta"), dict)
        assert doc["_meta"]["wo"] == "WO-PIPELINE-TEMPLATES-20261031"
        assert doc["_meta"]["schema"] == "pipeline_templates/v1"
        # 原样 JSON（_send_json 以 ensure_ascii=False 序列化）：可逐字复现
        assert handler.written.decode("utf-8") == json.dumps(doc, ensure_ascii=False)

    def test_templates_query_compatible(self):
        handler = _call_get("/pipeline_templates.json?v=1")
        assert handler.status == 200
        assert len(_json_body(handler)["templates"]) == 3

    def test_missing_templates_503(self, monkeypatch, tmp_path):
        missing = tmp_path / "pipeline_templates.json"
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(missing))
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 503
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "不存在" in payload["error"]
        assert payload["path"] == str(missing)

    def test_bad_json_500(self, monkeypatch, tmp_path):
        bad = tmp_path / "pipeline_templates.json"
        bad.write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(bad))
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 500
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "非法 JSON" in payload["error"]

    def test_non_dict_json_500(self, monkeypatch, tmp_path):
        weird = tmp_path / "pipeline_templates.json"
        weird.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(weird))
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 500
        assert "顶层必须为 dict" in _json_body(handler)["error"]

    def test_templates_not_list_500(self, monkeypatch, tmp_path):
        weird = tmp_path / "pipeline_templates.json"
        weird.write_text(json.dumps({"templates": "oops"}), encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(weird))
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 500
        assert "templates 必须为 list" in _json_body(handler)["error"]

    def test_payload_function_error_codes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(tmp_path / "nope.json"))
        payload, code = da.pipeline_templates_payload()
        assert code == 503 and payload["ok"] is False
        bad = tmp_path / "bad.json"
        bad.write_text("oops", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(bad))
        payload, code = da.pipeline_templates_payload()
        assert code == 500 and payload["ok"] is False

    def test_templates_route_does_not_write(self, monkeypatch, tmp_path):
        templates = tmp_path / "pipeline_templates.json"
        templates.write_bytes(_read_bytes(_TEMPLATES_PATH))
        snapshot = templates.read_bytes()
        monkeypatch.setattr(da, "PIPELINE_TEMPLATES_PATH", str(templates))
        handler = _call_get("/pipeline_templates.json")
        assert handler.status == 200
        assert templates.read_bytes() == snapshot
        assert sorted(item.name for item in tmp_path.iterdir()) == [
            "pipeline_templates.json"
        ]


# ── 3. 旧路由零改动 / 回退 ───────────────────────────────────────────
class TestLegacyRoutesUnchanged:
    def test_pipeline_page_still_served(self):
        handler = _call_get("/pipeline")
        assert handler.status == 200
        assert handler.headers.get("content-type") == "text/html; charset=utf-8"
        assert handler.headers.get("cache-control") == "no-store"
        assert b"<!DOCTYPE html>" in handler.written
        assert handler.orig_called is False

    def test_pipeline_graph_json_still_served(self):
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 200
        doc = _json_body(handler)
        assert doc["counts"]["cards"] == 10
        assert doc["counts"]["enabled"] == 2
        assert doc["counts"]["shadow"] == 8

    def test_canvas_route_still_served(self):
        handler = _call_get("/canvas")
        assert handler.status == 200
        assert handler.headers.get("content-type") == "text/html; charset=utf-8"
        assert b"<!DOCTYPE html>" in handler.written

    def test_unknown_path_falls_back_to_orig(self):
        for path in ("/api/overview", "/portal/static", "/portal/staticx/tokens.css"):
            handler = _call_get(path)
            assert handler.orig_called is True, path
            assert handler.written == b"", path
            assert handler.status is None, path

    def test_unknown_path_no_orig_get_404(self):
        handler = _call_get("/api/overview", with_orig_get=False)
        assert handler.status == 404
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "未知路由" in payload["error"]
