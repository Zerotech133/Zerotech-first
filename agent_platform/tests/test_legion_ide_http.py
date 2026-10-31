# -*- coding: utf-8 -*-
"""LDE 施工台 HTTP 端点回归（K-QUANT-20261030-LDETEST）。

用 tmp 仓 + 临时 git worktree + 端口 0 起真实 8103 服务；不碰生产 worktree。
覆盖：session/scope/tree/read/lock/write preview-confirm/git/exec/越权。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from legion_ide import serve as L  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    wt = repo / ".worktrees" / "T123"
    (wt / "agent_platform").mkdir(parents=True)
    (wt / "agent_platform" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    tasks = {"T123": {"status": "doing", "desc": "test",
                    "owner": "pytest", "created": "2026-10-30"}}
    (repo / ".worktrees" / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    _git(wt, "add", "agent_platform/foo.py")
    _git(wt, "commit", "-q", "-m", "init")
    return repo, wt


class Client:
    def __init__(self, port):
        self.base = "http://127.0.0.1:%d" % port
        self.owner = ""
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Origin": "http://127.0.0.1:8734"})
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


_TEST_LOCAL_TOKEN = "tok-http-tests"


def _serve(repo, monkeypatch):
    monkeypatch.setenv("IDE_RW", "on")
    monkeypatch.setenv("IDE_LOCAL_TOKEN", _TEST_LOCAL_TOKEN)
    # 审计修复 2026-10-31：测试隔离宿主 cap key（否则 write/preview 403）
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(Path(repo) / "no_such_cap.key"))
    httpd = L.create_server("127.0.0.1", 0, repo=repo,
                            portal_url="http://127.0.0.1:59999", bundle_path=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Client(httpd.server_port)
    with c.opener.open(c.base + "/rw?token=" + _TEST_LOCAL_TOKEN, timeout=5) as r:
        assert r.status == 200
    st, d = c.req("/api/ide/session")
    assert st == 200 and d["owner"]
    c.owner = d["owner"]
    return httpd, c


def test_session_scope_tree_read(tmp_path, monkeypatch):
    repo, _wt = _fixture(tmp_path)
    httpd, c = _serve(repo, monkeypatch)
    try:
        st, d = c.req("/api/ide/scope")
        assert st == 200 and d["items"][0]["task"] == "T123"
        st, d = c.req("/api/ide/tree?task=T123&path=.")
        assert st == 200 and any(x["name"] == "agent_platform" for x in d["items"])
        st, d = c.req("/api/ide/tree?task=T123&path=agent_platform")
        assert st == 200 and d["items"][0]["name"] == "foo.py"
        st, d = c.req("/api/ide/read", "POST",
                      {"task": "T123", "owner": c.owner, "path": "agent_platform/foo.py"})
        assert st == 200 and d["content"].replace("\r\n", "\n") == "x = 1\n"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_lock_visible_and_exclusive(tmp_path, monkeypatch):
    repo, _wt = _fixture(tmp_path)
    httpd, c = _serve(repo, monkeypatch)
    try:
        st, d = c.req("/api/ide/lock", "POST", {"task": "T123", "owner": c.owner})
        assert st == 200 and d["ok"]
        st, d = c.req("/api/ide/lock?task=T123")
        assert st == 200 and d["locked"] is True and d["owner"] == c.owner
        st, d = c.req("/api/ide/lock", "POST", {"task": "T123", "owner": "someone-else"})
        assert st == 403 and d["error"] == "owner_mismatch"
        st, d = c.req("/api/ide/lock?task=T123&owner=" + c.owner + "", "DELETE")
        assert st == 200 and d["ok"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_write_preview_confirm_and_git_exec(tmp_path, monkeypatch):
    repo, wt = _fixture(tmp_path)
    httpd, c = _serve(repo, monkeypatch)
    try:
        st, d = c.req("/api/ide/lock", "POST", {"task": "T123", "owner": c.owner})
        assert st == 200
        st, d = c.req("/api/ide/write/preview", "POST", {
            "task": "T123", "owner": c.owner, "path": "agent_platform/foo.py",
            "content": "x = 2\n"})
        assert st == 200 and d["preview_id"] and d["diff"]
        st, d = c.req("/api/ide/write/confirm", "POST", {
            "task": "T123", "owner": c.owner, "preview_id": d["preview_id"]})
        assert st == 200 and d["ok"]
        assert (wt / "agent_platform" / "foo.py").read_text(encoding="utf-8") == "x = 2\n"

        st, d = c.req("/api/ide/git", "POST", {
            "task": "T123", "owner": c.owner, "action": "diff",
            "path": "agent_platform/foo.py"})
        assert st == 200 and d["ok"]
        st, d = c.req("/api/ide/git", "POST", {
            "task": "T123", "owner": c.owner, "action": "add",
            "path": "agent_platform/foo.py"})
        assert st == 200 and d["ok"]
        st, d = c.req("/api/ide/git", "POST", {
            "task": "T123", "owner": c.owner, "action": "commit", "message": "test",
            "path": "agent_platform/foo.py"})
        assert st == 200 and d["ok"]

        st, d = c.req("/api/ide/exec", "POST", {
            "task": "T123", "owner": c.owner,
            "command": "git status -s -- agent_platform/foo.py"})
        assert st == 200 and d["success"] is True

        # P1 checkpoint/undo：撤销刚才的确认写入，恢复写入前内容
        st, d = c.req("/api/ide/undo", "POST", {
            "task": "T123", "owner": c.owner, "path": "agent_platform/foo.py"})
        assert st == 200 and d["undone"] is True
        assert (wt / "agent_platform" / "foo.py").read_text(
            encoding="utf-8").replace("\r\n", "\n") == "x = 1\n"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_security_rejects_no_session_bad_origin_and_escape(tmp_path, monkeypatch):
    repo, _wt = _fixture(tmp_path)
    httpd, c = _serve(repo, monkeypatch)
    try:
        st, d = c.req("/api/ide/tree?task=T123&path=..")
        assert st == 400 and d["ok"] is False
        st, d = c.req("/api/ide/lock", "POST", {"task": "T123", "owner": c.owner})
        assert st == 200
        st, d = c.req("/api/ide/write/preview", "POST", {
            "task": "T123", "owner": c.owner, "path": "../../outside.py",
            "content": "x"})
        assert st == 400
        # 错误 Origin 必须拒绝
        req = urllib.request.Request(
            c.base + "/api/ide/scope",
            headers={"Origin": "http://evil.example"})
        try:
            c.opener.open(req, timeout=5)
            raise AssertionError("bad origin 未被拒绝")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def _new_session_client(httpd):
    """为同一服务另建一个独立会话（独立 cookie jar / 独立 owner）。"""
    c = Client(httpd.server_port)
    with c.opener.open(c.base + "/rw?token=" + _TEST_LOCAL_TOKEN,
                       timeout=5) as r:
        assert r.status == 200
    st, d = c.req("/api/ide/session")
    assert st == 200 and d["owner"]
    c.owner = d["owner"]
    return c


def test_lock_delete_uses_session_owner_and_get_masks_others(tmp_path, monkeypatch):
    """M1（K-ORG-20261031-APIHARDEN）：释放锁 owner 取会话身份、忽略 query；
    GET 不再向他人泄露持锁 owner。"""
    repo, _wt = _fixture(tmp_path)
    httpd, c1 = _serve(repo, monkeypatch)
    try:
        c2 = _new_session_client(httpd)
        assert c1.owner and c2.owner and c1.owner != c2.owner

        st, d = c1.req("/api/ide/lock", "POST",
                       {"task": "T123", "owner": c1.owner})
        assert st == 200 and d["ok"]

        # 他人 GET：locked 可见，owner 必须掩码/为空（不得泄露 c1.owner）
        st, d = c2.req("/api/ide/lock?task=T123")
        assert st == 200 and d["locked"] is True
        assert d.get("owner") != c1.owner
        assert d.get("owner") == "" and d.get("owner_masked") is True

        # 他人伪造 query owner=c1.owner 释放：query 被忽略 → 释放失败、锁仍在
        st, d = c2.req("/api/ide/lock?task=T123&owner=" + c1.owner, "DELETE")
        assert st == 409 and d.get("ok") is False
        st, d = c1.req("/api/ide/lock?task=T123")
        assert st == 200 and d["locked"] is True and d["owner"] == c1.owner

        # 自己带无关 query owner 释放：owner 来自会话 → 正常释放
        st, d = c1.req("/api/ide/lock?task=T123&owner=someone-else", "DELETE")
        assert st == 200 and d["ok"]
        st, d = c1.req("/api/ide/lock?task=T123")
        assert st == 200 and d["locked"] is False

        # 无会话 DELETE：拒绝（不因 query 自报 owner 放行）
        bare = Client(httpd.server_port)
        st, d = bare.req("/api/ide/lock?task=T123&owner=" + c1.owner, "DELETE")
        assert st == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


