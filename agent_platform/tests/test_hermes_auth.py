# -*- coding: utf-8 -*-
"""B1 Hermes 认证客户端测试（本地桩，不打真实 8787）。"""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "agent_platform" / "legion_command" / "hermes_auth.py"

LOGIN_COUNT = {"n": 0}
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in headers or []:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/auth/login":
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            LOGIN_COUNT["n"] += 1
            if data.get("password") == "pw-123":
                self._send(200, b'{"ok":true}',
                           [("Set-Cookie", "hermes_session=abc123; HttpOnly; Path=/")])
            else:
                self._send(401, b'{"error":"bad"}')
            return
        self._send(404)

    def do_GET(self):
        cookie = self.headers.get("Cookie") or ""
        SEEN.append(cookie)
        if "hermes_session=abc123" in cookie:
            self._send(200, b"ok")
        else:
            self._send(401, b"no")


def _load(monkeypatch, tmp_path, server, enabled=True):
    pw = tmp_path / "pw.txt"
    if enabled:
        pw.write_text("pw-123", encoding="utf-8")
    monkeypatch.setenv("HERMES_BASE", "http://127.0.0.1:%d" % server.server_port)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD_FILE", str(pw))
    monkeypatch.delenv("HERMES_WEBUI_COOKIE_NAME", raising=False)
    spec = importlib.util.spec_from_file_location("hauth_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_disabled_passthrough(monkeypatch, tmp_path):
    srv = _server()
    try:
        m = _load(monkeypatch, tmp_path, srv, enabled=False)
        import urllib.error
        try:
            m.auth_urlopen(srv.server_address and ("http://127.0.0.1:%d/" % srv.server_port), timeout=3)
            raise AssertionError("should 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert LOGIN_COUNT["n"] == 0
    finally:
        srv.shutdown()


def test_login_and_request_cookie(monkeypatch, tmp_path):
    LOGIN_COUNT["n"] = 0
    SEEN.clear()
    srv = _server()
    try:
        m = _load(monkeypatch, tmp_path, srv)
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/" % srv.server_port,
                                     headers={"Cookie": "hermes_profile=x"})
        with m.auth_urlopen(req, timeout=3) as r:
            assert r.status == 200
            assert r.read() == b"ok"
        assert LOGIN_COUNT["n"] >= 1
        assert any("hermes_profile=x" in c and "hermes_session=abc123" in c for c in SEEN)
        # 第二个请求应命中 cookie 缓存，不再登录
        n0 = LOGIN_COUNT["n"]
        with m.auth_urlopen("http://127.0.0.1:%d/" % srv.server_port, timeout=3) as r:
            assert r.status == 200
        assert LOGIN_COUNT["n"] == n0
    finally:
        srv.shutdown()
