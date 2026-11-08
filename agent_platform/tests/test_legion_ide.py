"""军团 Agent IDE 独立形态测试（WO-LEGION-IDE-STANDALONE-20261026）。

覆盖：/api/health、/（ide.html）、/agent_ide_bundle.json、404 白名单、
目录穿越拒绝、POST /ollama/api/chat 门户不可达时 503 fail-soft。
用 tmp 仓 + 端口 0 起真实 HTTP 服务，不碰真实门户/8734。
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from legion_ide import serve as L  # noqa: E402


def _fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    portal = repo / "portal"
    portal.mkdir(parents=True, exist_ok=True)
    (portal / "ide.html").write_text(
        "<!doctype html><title>Agent IDE</title>", encoding="utf-8"
    )
    (portal / "agent_ide_bundle.json").write_text(
        json.dumps({"generated": "2026-10-26", "agents": []}), encoding="utf-8"
    )
    return repo


def _serve(
    repo: Path,
    portal_url: str = "http://127.0.0.1:59999",
    bundle_path: Path | None = None,
):
    httpd = L.create_server(
        "127.0.0.1", 0, repo=repo, portal_url=portal_url, bundle_path=bundle_path
    )
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


def _get(url: str):
    # RemoteDisconnected 属连接级竞态：_serve() 起线程后立即返回，2 核 CI 上线程
    # 尚未进入 accept 循环时，连接被接受但服务端未就绪即关闭（run #35 实测）。
    # 只对连接级错误重试一次；响应内容错误仍立即失败，不掩盖真问题。
    for _attempt in (0, 1):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except http.client.RemoteDisconnected:
            if _attempt:
                raise
            time.sleep(0.2)


def _post(url: str, data: bytes):
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_health_reports_resources(tmp_path):
    repo = _fixture(tmp_path)
    httpd, _ = _serve(repo, portal_url="http://127.0.0.1:59999")
    try:
        status, ctype, body = _get("http://127.0.0.1:%d/api/health" % httpd.server_port)
        assert status == 200 and "application/json" in ctype
        data = json.loads(body)
        assert data["ok"] is True and data["service"] == "legion-ide"
        assert data["ide_html"]["exists"] is True
        assert data["bundle"]["exists"] is True
        assert data["portal_proxy"]["reachable"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_serves_ide_and_bundle(tmp_path):
    repo = _fixture(tmp_path)
    httpd, _ = _serve(repo)
    try:
        base = "http://127.0.0.1:%d" % httpd.server_port
        status, ctype, body = _get(base + "/")
        assert status == 200 and "text/html" in ctype
        assert b"Agent IDE" in body
        status, ctype, body = _get(base + "/agent_ide_bundle.json")
        assert status == 200 and "application/json" in ctype
        assert json.loads(body)["generated"] == "2026-10-26"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unknown_path_and_traversal_rejected(tmp_path):
    repo = _fixture(tmp_path)
    httpd, _ = _serve(repo)
    try:
        base = "http://127.0.0.1:%d" % httpd.server_port
        for path in ("/nope", "/../portal/ide.html", "/static/../../etc/passwd"):
            status, _ctype, body = _get(base + path)
            assert status == 404, path
            assert json.loads(body)["error"] == "not_found"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_proxy_unavailable_is_fail_soft(tmp_path):
    repo = _fixture(tmp_path)
    httpd, _ = _serve(repo, portal_url="http://127.0.0.1:59999")
    try:
        status, body = _post(
            "http://127.0.0.1:%d/ollama/api/chat" % httpd.server_port,
            b'{"model":"x","messages":[]}',
        )
        assert status == 503
        data = json.loads(body)
        assert data["error"] == "portal_proxy_unavailable"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_missing_resources_503(tmp_path):
    repo = tmp_path / "empty"
    httpd, _ = _serve(repo)
    try:
        base = "http://127.0.0.1:%d" % httpd.server_port
        status, _c, body = _get(base + "/")
        assert status == 503 and json.loads(body)["error"] == "ide_html_missing"
        status, _c, body = _get(base + "/agent_ide_bundle.json")
        assert status == 503 and json.loads(body)["error"] == "bundle_missing"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_port_registered_and_launcher_present():
    tool = _PLATFORM.parent / "tools" / "check_ports.py"
    src = tool.read_text(encoding="utf-8")
    assert "8103" in src and "legion_ide" in src
    ps1 = _PLATFORM.parent / "ops" / "start_legion_ide.ps1"
    text = ps1.read_text(encoding="utf-8-sig")
    assert "8103" in text and "legion_ide" in text and "-Action" in text


def test_bundle_override_served(tmp_path):
    """--bundle 部署副本优先：服务应从显式 bundle 读取，health 标注来源。"""
    repo = _fixture(tmp_path)
    custom = tmp_path / "deploy" / "agent_ide_bundle.json"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps({"generated": "DEPLOY-2026-10-26", "agents": []}), encoding="utf-8"
    )
    httpd, _t = _serve(repo, bundle_path=custom)
    try:
        base = "http://127.0.0.1:%d" % httpd.server_port
        status, _c, body = _get(base + "/agent_ide_bundle.json")
        assert status == 200
        assert json.loads(body)["generated"] == "DEPLOY-2026-10-26"
        status, _c, body = _get(base + "/api/health")
        assert json.loads(body)["bundle_source"] == str(custom)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ops_scripts_present():
    ops = _PLATFORM.parent / "ops"
    for name, tokens in (
        ("keepalive_legion_ide.ps1", ("8103", "start_legion_ide", "OK legion-ide")),
        (
            "rebake_ide_bundle.ps1",
            ("build_agent_ide_bundle", "agent_ide_bundle.json", "checkout"),
        ),
        ("install_git_hooks.ps1", ("post-merge", "rebake_ide_bundle")),
        ("create_legion_ide_shortcut.ps1", ("WScript.Shell", "-Action app", "8103")),
    ):
        text = (ops / name).read_text(encoding="utf-8-sig")
        for tok in tokens:
            assert tok in text, (name, tok)
