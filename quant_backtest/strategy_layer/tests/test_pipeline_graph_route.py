# -*- coding: utf-8 -*-
"""dashboard_api `/pipeline_graph.json` 只读路由单测（WO-PIPELINE-GRAPH-ROUTE-20261031）。

覆盖验收口径：
1) 200：返回同目录 pipeline_graph.json 原样文档；Content-Type =
   ``application/json; charset=utf-8``、Cache-Control = ``no-store``、
   Content-Length 精确；Graph counts = cards 10 / enabled 2 / shadow 8；
2) query string 兼容（``?t=...`` 不影响命中）；
3) 文件缺失 → 503 JSON 错误；坏 JSON / 顶层非 dict → 500 JSON 错误；
4) 未知路径回退原 do_GET 不变（无原 do_GET → 404 JSON 未知路由）；旧
   ``GET /pipeline`` 静态页路由不变；
5) 只读：Graph 文件字节不变、不落任何新文件、不启动服务（Handler 替身直调）。

全程离线：FakeHandler 直接调包装后的 do_GET，不监听端口、不连 PG、不写生产数据。
"""

from __future__ import annotations

import hashlib
import json
import os

from strategy_layer import dashboard_api as da

_GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(da.__file__)), "pipeline_graph.json"
)


# ── Handler 替身（记录状态/头/body，原 do_GET 置 orig_called） ─────────
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

    # _send_json 依赖的 BaseHTTPRequestHandler 接口
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
    """每个用例新的 Handler 类（register_routes 以类为幂等粒度，防跨用例污染）。"""
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


def _assert_json_headers(handler):
    assert handler.headers.get("content-type") == "application/json; charset=utf-8"
    assert handler.headers.get("cache-control") == "no-store"
    assert handler.headers.get("content-length") == str(len(handler.written))
    assert handler.headers.get("access-control-allow-origin") == "*"


# ── 1. 200：原样 Graph JSON + 头 + counts 10/2/8 ─────────────────────
class TestGraphJsonOk:
    def test_200_headers_and_counts(self):
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 200
        _assert_json_headers(handler)
        assert handler.orig_called is False  # 本模块接管，不回落

        doc = _json_body(handler)
        # 原样文档（不包 {"ok": true} 外壳），与 pipeline_map.html validator 同构
        assert "ok" not in doc
        assert isinstance(doc["nodes"], list) and doc["nodes"]
        assert isinstance(doc["edges"], list)
        assert isinstance(doc["meta"], dict)
        counts = doc["counts"]
        assert counts["cards"] == 10
        assert counts["enabled"] == 2
        assert counts["shadow"] == 8
        assert counts["shadow_candidates"] == 8
        assert len(doc["nodes"]) == counts["nodes"]
        assert len(doc["edges"]) == counts["edges"]
        assert doc["meta"]["wo"] == "WO-PIPELINE-GRAPH-20261031"
        assert doc["meta"]["readonly"] is True

    def test_query_string_compatible(self):
        handler = _call_get("/pipeline_graph.json?t=20261031&v=1")
        assert handler.status == 200
        _assert_json_headers(handler)
        doc = _json_body(handler)
        assert doc["counts"]["cards"] == 10
        assert doc["counts"]["enabled"] == 2
        assert doc["counts"]["shadow"] == 8

    def test_payload_function_direct(self):
        doc, code = da.pipeline_graph_payload()
        assert code == 200
        assert doc["counts"]["cards"] == 10
        assert doc["counts"]["enabled"] == 2
        assert doc["counts"]["shadow"] == 8

    def test_similar_path_not_matched(self):
        """精确匹配：/pipeline_graph.json.bak 不算本路由 → 回落原 do_GET。"""
        handler = _call_get("/pipeline_graph.json.bak")
        assert handler.orig_called is True
        assert handler.written == b""


# ── 2. 缺文件 503 / 坏 JSON 500（JSON 错误、不抛栈） ─────────────────
class TestGraphJsonErrors:
    def test_missing_file_503(self, monkeypatch, tmp_path):
        missing = tmp_path / "pipeline_graph.json"
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(missing))
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 503
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "不存在" in payload["error"]
        assert payload["path"] == str(missing)

    def test_bad_json_500(self, monkeypatch, tmp_path):
        bad = tmp_path / "pipeline_graph.json"
        bad.write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(bad))
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 500
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "非法 JSON" in payload["error"]

    def test_non_dict_json_500(self, monkeypatch, tmp_path):
        weird = tmp_path / "pipeline_graph.json"
        weird.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(weird))
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 500
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "顶层必须为 dict" in payload["error"]

    def test_unreadable_path_503(self, monkeypatch, tmp_path):
        """路径指向目录 → OSError 分支 503（不抛栈）。"""
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(tmp_path))
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 503
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "读取失败" in payload["error"]

    def test_payload_function_error_codes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(tmp_path / "gone.json"))
        payload, code = da.pipeline_graph_payload()
        assert code == 503 and payload["ok"] is False
        bad = tmp_path / "bad.json"
        bad.write_text("oops", encoding="utf-8")
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(bad))
        payload, code = da.pipeline_graph_payload()
        assert code == 500 and payload["ok"] is False


# ── 3. 未知路径回退 / 旧路由不变 ─────────────────────────────────────
class TestFallbackUnchanged:
    def test_unknown_path_falls_back_to_orig(self):
        handler = _call_get("/api/overview")
        assert handler.orig_called is True
        assert handler.written == b""
        assert handler.status is None

    def test_unknown_path_no_orig_get_404(self):
        handler = _call_get("/api/overview", with_orig_get=False)
        assert handler.status == 404
        _assert_json_headers(handler)
        payload = _json_body(handler)
        assert payload["ok"] is False
        assert "未知路由" in payload["error"]

    def test_legacy_pipeline_page_unchanged(self):
        handler = _call_get("/pipeline")
        assert handler.status == 200
        assert handler.headers.get("content-type") == "text/html; charset=utf-8"
        assert handler.headers.get("cache-control") == "no-store"
        assert handler.headers.get("content-length") == str(len(handler.written))
        assert b"<!DOCTYPE html>" in handler.written
        assert "pipeline".encode("utf-8") in handler.written
        assert handler.orig_called is False

    def test_root_pipeline_path_falls_back(self):
        """/pipeline_graph.json 之外的根级路径仍回落原 do_GET。"""
        handler = _call_get("/pipeline_graph")
        assert handler.orig_called is True


# ── 4. 只读：Graph 文件字节不变 / 不落新文件 ─────────────────────────
class TestReadOnly:
    def test_real_graph_file_untouched(self):
        before = _sha256(_GRAPH_PATH)
        for path in (
            "/pipeline_graph.json",
            "/pipeline_graph.json?t=1",
            "/pipeline_graph.json",
        ):
            handler = _call_get(path)
            assert handler.status == 200
        assert _sha256(_GRAPH_PATH) == before, "路由不得写/重建 Graph 文件"

    def test_route_writes_no_files(self, monkeypatch, tmp_path):
        graph = tmp_path / "pipeline_graph.json"
        graph.write_bytes(open(_GRAPH_PATH, "rb").read())
        snapshot = graph.read_bytes()
        monkeypatch.setattr(da, "PIPELINE_GRAPH_PATH", str(graph))
        handler = _call_get("/pipeline_graph.json")
        assert handler.status == 200
        assert graph.read_bytes() == snapshot
        # 目录内只有预先放入的 Graph 文件：路由不落盘
        assert sorted(item.name for item in tmp_path.iterdir()) == ["pipeline_graph.json"]
