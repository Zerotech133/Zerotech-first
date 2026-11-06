# -*- coding: utf-8 -*-
"""safe_urlopen 单测：scheme allowlist + 透传 + 回环禁代理。

最后一条是 2026-11-06 补的回归：本机环境长期带 HTTP_PROXY，
urllib 对 127.0.0.1 不做豁免（proxy_bypass 返回 False），
若 safe_urlopen 不主动清空 ProxyHandler，探活结果会随代理状态漂移。
"""
import importlib.util
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "safe_urlopen.py"


def _load():
    spec = importlib.util.spec_from_file_location("safe_urlopen_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_external_still_uses_global_urlopen(monkeypatch):
    """非回环目标维持原路径：走全局 urlopen（保留代理与证书语义）。"""
    m = _load()
    calls = []

    def fake(url, *a, **k):
        calls.append((url, a, k))
        return "ok"

    monkeypatch.setattr(m.urllib.request, "urlopen", fake)
    assert m.safe_urlopen("https://example.com/x") == "ok"
    req = urllib.request.Request("https://example.com/y")
    assert m.safe_urlopen(req, timeout=1) == "ok"
    assert len(calls) == 2


def test_loopback_does_not_touch_global_urlopen(monkeypatch):
    """回环目标不得走全局 urlopen（否则被环境代理接管）。"""
    m = _load()
    monkeypatch.setattr(
        m.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("回环不应走全局 urlopen")),
    )
    seen = []

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(url, *a, **k):
        seen.append((getattr(url, "full_url", None) or str(url), a, k))
        return FakeResp()

    monkeypatch.setattr(m, "_open_no_proxy", fake_open)
    for u in ("http://127.0.0.1:8091/health",
              "http://localhost:8787/health",
              "http://[::1]:9101/health"):
        with m.safe_urlopen(u, timeout=3) as r:
            assert r.status == 200
    assert len(seen) == 3
    assert seen[0][2].get("timeout") == 3  # 参数原样透传


def test_loopback_matrix():
    m = _load()
    yes = ["127.0.0.1", "127.1.2.3", "localhost", "LOCALHOST", "::1", "[::1]",
           "0.0.0.0", "localhost.localdomain"]
    no = ["example.com", "10.0.0.5", "192.168.1.8", "8.8.8.8", "", None, "not-a-host"]
    for h in yes:
        assert m.is_loopback(h) is True, h
    for h in no:
        assert m.is_loopback(h) is False, h


def test_loopback_survives_dead_proxy_env(monkeypatch):
    """判决性回归：代理指向死端口时，回环调用必须仍然成功。

    这段真起一个本机 HTTP 服务、真打一次。若有人把回环分支删掉，
    urlopen 会按 HTTP_PROXY 建 opener 去连死端口 -> URLError -> 本用例红。
    """
    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dead = 59321  # 刻意选一个几乎不可能被占用的端口
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:%d" % dead)
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    # 强制重建全局 opener，让 env 真正生效（否则沿用进程内已冻结的 opener）
    monkeypatch.setattr(urllib.request, "_opener", None)

    m = _load()
    try:
        with m.safe_urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert r.status == 200
        assert body["ok"] is True
    finally:
        srv.shutdown()


def test_file_scheme_rejected(monkeypatch):
    m = _load()
    monkeypatch.setattr(m.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")))
    for bad in ("file:///etc/passwd", "ftp://x/y", "gopher://x", "data:text/plain,hi"):
        try:
            m.safe_urlopen(bad)
            raise AssertionError("应拒绝: %s" % bad)
        except ValueError:
            pass


def test_urlsplit_import():
    m = _load()
    assert m.ALLOWED_SCHEMES == ("http", "https")
