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

# ── 副本一致性（2026-11-07 批 5 补） ─────────────────────────
# 背景：回环豁免只加进 6 份 safe_urlopen.py，legion_command/safe_http.py 是第 7 份
# 独立实现、没跟上，导致该树 12 处打本机服务（11434/8770/8771/8787/8796）的请求
# 仍被交给代理；而扫描器按「调的是 safe_urlopen」判 GUARDED，看不见这个洞。
# 下面两条防的就是「再长出第 8 份」。
REPO = Path(__file__).resolve().parents[2]


def _tracked_copies():
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "-z", "*safe_urlopen.py"],
                             cwd=str(REPO), capture_output=True, check=True).stdout
    except Exception:  # pragma: no cover - 无 git 环境下跳过
        import pytest
        pytest.skip("git 不可用，跳过副本一致性检查")
    rels = [r for r in out.decode("utf-8").split("\0") if r.strip()]
    assert rels, "git 未列出任何 safe_urlopen.py 副本"
    return [REPO / r for r in rels]


def test_every_copy_has_loopback_exemption():
    """所有 safe_urlopen.py 副本都必须带回环禁代理，否则就是第 7 份那种漏网副本。"""
    bad = []
    for p in _tracked_copies():
        src = p.read_text(encoding="utf-8")
        if "_open_no_proxy" not in src or "is_loopback" not in src:
            bad.append(str(p.relative_to(REPO)))
    assert not bad, "以下副本缺回环豁免：%s" % bad


def test_legion_command_safe_http_delegates_to_shared_impl():
    """legion_command/safe_http.py 必须是委派，不得自带一份退化实现。"""
    import sys as _s
    p = REPO / "agent_platform" / "legion_command" / "safe_http.py"
    if not p.exists():  # pragma: no cover
        import pytest
        pytest.skip("safe_http.py 不存在")
    _s.path.insert(0, str(p.parent))
    try:
        spec = importlib.util.spec_from_file_location("_safe_http_probe", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        _s.path.pop(0)
    impl = Path(mod.safe_urlopen.__code__.co_filename).name
    assert impl == "safe_urlopen.py", "safe_http 未委派，实际实现来自 %s" % impl
