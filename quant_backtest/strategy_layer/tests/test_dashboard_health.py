# -*- coding: utf-8 -*-
"""/api/health 健康探针单测：全通 / 拒绝 / 超时三态 + 路由层恒 200。

探测函数（_probe_tcp/_probe_http）全部 monkeypatch，测试零网络 IO、
不触真实 127.0.0.1 端口。范式照抄 test_dashboard_api.py（FakeHandler
+ _send_json 捕获）。
"""

import io
import socket
import sys
import os


QUANT_BT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import dashboard_api  # noqa: E402


def _all_ok_probes(monkeypatch):
    """全通替身：HTTP 探 12ms、TCP 探 3ms。"""
    monkeypatch.setattr(dashboard_api, "_probe_http", lambda port, timeout: 12)
    monkeypatch.setattr(dashboard_api, "_probe_tcp", lambda port, timeout: 3)


# ── health_payload：结构契约 ─────────────────────────────────────────
class TestHealthPayload:
    def test_all_ok(self, monkeypatch):
        _all_ok_probes(monkeypatch)
        payload = dashboard_api.health_payload()
        assert payload["ok"] is True
        assert payload["ts"]
        names = [c["name"] for c in payload["checks"]]
        assert names == ["portal", "gateway", "quantapi", "ollama", "dashboard"]
        for c in payload["checks"]:
            assert set(c) == {"port", "name", "ok", "latency_ms", "err"}
            assert c["ok"] is True
            assert isinstance(c["latency_ms"], int)
            assert c["err"] is None

    def test_default_ports(self, monkeypatch):
        _all_ok_probes(monkeypatch)
        payload = dashboard_api.health_payload()
        by_name = {c["name"]: c for c in payload["checks"]}
        assert by_name["portal"]["port"] == 8091
        assert by_name["gateway"]["port"] == 8098
        assert by_name["quantapi"]["port"] == 8001
        assert by_name["ollama"]["port"] == 11434
        assert by_name["dashboard"]["port"] == 8791

    def test_self_check_no_probe(self, monkeypatch):
        """dashboard 项直接 ok:true，不触发任何探测函数（防递归）。"""
        calls = []

        def spy(port, timeout):
            calls.append(port)
            return 1

        monkeypatch.setattr(dashboard_api, "_probe_http", spy)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", spy)
        payload = dashboard_api.health_payload()
        # 8791 从未被探测（探测仅外部四项，且本替身中均走 TCP/HTTP 桩）
        assert 8791 not in calls
        self_entry = [c for c in payload["checks"] if c["name"] == "dashboard"][0]
        assert self_entry["ok"] is True
        assert self_entry["latency_ms"] == 0
        assert self_entry["err"] is None

    def test_portal_refused_isolated(self, monkeypatch):
        """8091 拒绝：该项 ok:false + err，其余项不受阻塞照常探测。"""

        def refused(port, timeout):
            raise ConnectionRefusedError(
                "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"
            )

        monkeypatch.setattr(dashboard_api, "_probe_http", refused)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", lambda port, timeout: 3)
        payload = dashboard_api.health_payload()
        assert payload["ok"] is True  # 端点自身恒 ok（靠 checks 表达健康）
        by_name = {c["name"]: c for c in payload["checks"]}
        p = by_name["portal"]
        assert p["ok"] is False
        assert p["latency_ms"] is None
        assert p["err"] and "拒绝" in p["err"]
        # 其余项照常存活
        assert by_name["gateway"]["ok"] is True
        assert by_name["quantapi"]["ok"] is True
        assert by_name["ollama"]["ok"] is True

    def test_tcp_timeout(self, monkeypatch):
        """TCP 探测超时：ok:false + err 含超时信息。"""

        def slow(port, timeout):
            raise socket.timeout("timed out")

        monkeypatch.setattr(dashboard_api, "_probe_http", lambda port, timeout: 5)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", slow)
        payload = dashboard_api.health_payload()
        by_name = {c["name"]: c for c in payload["checks"]}
        for name in ("gateway", "quantapi", "ollama"):
            c = by_name[name]
            assert c["ok"] is False, name
            assert c["latency_ms"] is None
            assert "timed out" in c["err"], name
        assert by_name["portal"]["ok"] is True  # HTTP 项不受影响

    def test_http_timeout(self, monkeypatch):
        def slow(port, timeout):
            raise socket.timeout("timed out")

        monkeypatch.setattr(dashboard_api, "_probe_http", slow)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", lambda port, timeout: 3)
        payload = dashboard_api.health_payload()
        p = [c for c in payload["checks"] if c["name"] == "portal"][0]
        assert p["ok"] is False and "timed out" in p["err"]

    def test_empty_message_err_falls_back_to_class_name(self, monkeypatch):
        def boom(port, timeout):
            raise ConnectionResetError()

        monkeypatch.setattr(dashboard_api, "_probe_http", boom)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", lambda port, timeout: 3)
        payload = dashboard_api.health_payload()
        p = [c for c in payload["checks"] if c["name"] == "portal"][0]
        assert p["ok"] is False
        assert p["err"] == "ConnectionResetError"

    def test_env_port_override(self, monkeypatch):
        monkeypatch.setenv("HEALTH_PORTAL_PORT", "19091")
        monkeypatch.setenv("HEALTH_QUANTAPI_PORT", "18001")
        _all_ok_probes(monkeypatch)
        payload = dashboard_api.health_payload()
        by_name = {c["name"]: c for c in payload["checks"]}
        assert by_name["portal"]["port"] == 19091
        assert by_name["quantapi"]["port"] == 18001
        assert by_name["gateway"]["port"] == 8098  # 未覆盖的保持默认

    def test_env_port_override_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("HEALTH_PORTAL_PORT", "not-a-port")
        _all_ok_probes(monkeypatch)
        payload = dashboard_api.health_payload()
        by_name = {c["name"]: c for c in payload["checks"]}
        assert by_name["portal"]["port"] == 8091

    def test_no_probe_failure_escapes(self, monkeypatch):
        """任意单项探测抛任意异常都不逃逸（恒有完整 checks 结构）。"""

        def boom(port, timeout):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(dashboard_api, "_probe_http", boom)
        monkeypatch.setattr(dashboard_api, "_probe_tcp", boom)
        payload = dashboard_api.health_payload()
        assert payload["ok"] is True
        assert len(payload["checks"]) == 5
        assert all(
            c["ok"] is False for c in payload["checks"] if c["name"] != "dashboard"
        )


# ── register_routes：/api/health 路由层 ──────────────────────────────
class FakeHandler:
    """主仪表盘 Handler 替身（同 test_dashboard_api.py 范式）。"""

    _dashboard_api_wrapped = False

    def __init__(self, path, method="GET", body=None):
        self.path = path
        self.method = method
        self.body = body or b""
        self.sent = []
        self.orig_called = False

    def do_GET(self):
        self.orig_called = True

    def do_POST(self):
        self.orig_called = True

    def send_response(self, code):
        self._code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return io.BytesIO()


class TestHealthRoute:
    def setup_method(self):
        self.handler_cls = type(
            "Handler",
            (FakeHandler,),
            {
                "do_GET": FakeHandler.do_GET,
                "do_POST": FakeHandler.do_POST,
            },
        )

    def _send_capture(self, monkeypatch):
        captured = []

        def fake_send(handler, obj, code=200):
            captured.append((obj, code))

        monkeypatch.setattr(dashboard_api, "_send_json", fake_send)
        return captured

    def test_health_route_200_all_ok(self, monkeypatch):
        _all_ok_probes(monkeypatch)
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/health", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert [c["name"] for c in payload["checks"]] == [
            "portal",
            "gateway",
            "quantapi",
            "ollama",
            "dashboard",
        ]

    def test_health_route_never_500(self, monkeypatch):
        """health_payload 意外炸掉 → 仍 200 降级返回（探针不放大故障）。"""

        def boom():
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(dashboard_api, "health_payload", boom)
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/health", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert payload["checks"] == []
        assert "probe exploded" in payload["error"]

    def test_health_route_does_not_shadow_other_routes(self, monkeypatch):
        """/api/overview 等既有路由仍回落主仪表盘原 do_GET。"""
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/overview", "GET")
        h.do_GET()
        assert h.orig_called is True
        assert captured == []
