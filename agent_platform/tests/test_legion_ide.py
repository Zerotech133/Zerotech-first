"""军团 Agent IDE 独立形态测试（WO-LEGION-IDE-STANDALONE-20261026）。

覆盖：/api/health、/（ide.html）、/agent_ide_bundle.json、404 白名单、
目录穿越拒绝、POST /ollama/api/chat 门户不可达时 503 fail-soft。
用 tmp 仓 + 端口 0 起真实 HTTP 服务，不碰真实门户/8734。
"""
from __future__ import annotations

import json
import sys
import threading
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
    (portal / "ide.html").write_text("<!doctype html><title>Agent IDE</title>",
                                     encoding="utf-8")
    (portal / "agent_ide_bundle.json").write_text(
        json.dumps({"generated": "2026-10-26", "agents": []}), encoding="utf-8")
    return repo


def _serve(repo: Path, portal_url: str = "http://127.0.0.1:59999"):
    httpd = L.create_server("127.0.0.1", 0, repo=repo, portal_url=portal_url)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def _post(url: str, data: bytes):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
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
        httpd.shutdown(); httpd.server_close()


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
        httpd.shutdown(); httpd.server_close()


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
        httpd.shutdown(); httpd.server_close()


def test_chat_proxy_unavailable_is_fail_soft(tmp_path):
    repo = _fixture(tmp_path)
    httpd, _ = _serve(repo, portal_url="http://127.0.0.1:59999")
    try:
        status, body = _post("http://127.0.0.1:%d/ollama/api/chat" % httpd.server_port,
                             b'{"model":"x","messages":[]}')
        assert status == 503
        data = json.loads(body)
        assert data["error"] == "portal_proxy_unavailable"
    finally:
        httpd.shutdown(); httpd.server_close()


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
        httpd.shutdown(); httpd.server_close()


def test_port_registered_and_launcher_present():
    tool = _PLATFORM.parent / "tools" / "check_ports.py"
    src = tool.read_text(encoding="utf-8")
    assert "8103" in src and "legion_ide" in src
    ps1 = _PLATFORM.parent / "ops" / "start_legion_ide.ps1"
    text = ps1.read_text(encoding="utf-8-sig")
    assert "8103" in text and "legion_ide" in text and "-Action" in text
