# -*- coding: utf-8 -*-
"""E2E-10 失败路径：可读告警断言（只读，不注入生产故障、不改服务）。"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402


def _run_json(args, timeout=120):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8"] + args, cwd=str(REPO),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout)
    assert p.returncode == 0, p.stdout[-300:] + p.stderr[-300:]
    out = p.stdout
    return json.loads(out[out.find("{"):])


def test_offline_registered_ports_are_readable():
    """离线端口必须出现在可读清单里（port/name/up），且 strict 不因离线判负。"""
    data = _run_json(["tools/check_ports.py", "--json"])
    assert "registered" in data and isinstance(data["registered"], list)
    offline = [r for r in data["registered"] if not r.get("up")]
    assert offline, "预期至少一个注册端口离线（若全部在线可调整断言）"
    for r in offline[:5]:
        assert isinstance(r.get("port"), int) and r.get("name"), r
    assert "summary" in data and "offline" in data["summary"]
    # strict 只拦未登记/冲突；离线仅提示，不判负
    p = subprocess.run([sys.executable, "-X", "utf8", "tools/check_ports.py", "--strict"],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    # strict 只拦未登记/冲突；离线仅提示，不判负。
    # 本机可能有并行会话的瞬态监听，不能用「当前环境无未登记」做前置条件；
    # 只校验 strict 退出码与 JSON 汇总的因果一致。
    expected_rc = 1 if (data["summary"].get("unregistered") or data["summary"].get("conflicts")) else 0
    assert p.returncode == expected_rc, p.stdout[-400:] + p.stderr[-400:]


class _Auth401(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"detail": "missing or invalid token"}, ensure_ascii=False).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静默
        pass


def test_http_401_body_is_readable():
    """401 必须是可读 JSON（不静默/不裸断），模拟本地 401 服务。"""
    srv = HTTPServer(("127.0.0.1", 0), _Auth401)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = "http://127.0.0.1:%d/protected" % srv.server_port
        with safe_urlopen(url, timeout=5) as r:  # pragma: no cover
            raise AssertionError("预期 401，实际 HTTP %s" % r.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        assert e.code == 401
        data = json.loads(body)
        assert "token" in json.dumps(data, ensure_ascii=False).lower() or data.get("detail")
    finally:
        srv.shutdown()


def test_8796_auth_failure_or_ok_is_readable():
    """8796 MCP 端点：无 token 时 200 可读 or 401 可读 JSON，二者其一，不许沉默。"""
    req = urllib.request.Request("http://127.0.0.1:8796/api/zhaoyun/mcp",
                                 headers={"Authorization": "Bearer e2e-invalid-token"})
    try:
        with safe_urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
            assert r.status == 200
            json.loads(body)  # 必须可读
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        assert e.code in (401, 403), "预期 200/401/403，实际 %s" % e.code
        if body.strip():
            json.loads(body)  # 401 响应体必须可读（不静默）
