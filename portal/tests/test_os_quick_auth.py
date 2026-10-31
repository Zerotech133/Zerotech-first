# -*- coding: utf-8 -*-
"""H1（K-ORG-20261031-PORTALAUTH，审计 v2）：/api/os/* 认证 + 预览防 XSS 回归。

审计实证：旧 `is_local_page_request` 信任可伪造的 `X-OS-Page` / 私网 Origin /
X-Real-IP，匿名即可 POST /api/os/*；预览返回 text/html → 存储型 XSS。
修复后只认：peer 回环直连 / X-Portal-Token / 有效 zb_session 登录会话；
预览一律 text/plain + nosniff。
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import auth_gateway as G  # noqa: E402
import os_goal_store as S  # noqa: E402


class _FakeHandler(A.Handler):
    """最小 handler（继承 api_os_goal.Handler mixin，复用 _os_goal_post/_os_goal_get）。"""

    def __init__(self, store, headers=None, peer="172.17.0.1", body=None,
                 token_ok=False):
        self.headers = dict(headers or {})
        self.client_address = (peer, 51234)
        self._token_ok = token_ok
        self._store = store
        raw = json.dumps(body).encode("utf-8") if body is not None else b"{}"
        self.rfile = io.BytesIO(raw)
        self.headers["Content-Length"] = str(len(raw))
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = []
        self.sent_headers = []

    def _check_portal_token(self):
        return self._token_ok

    def _os_goal_store(self):
        return self._store

    def _json(self, payload, code=200):
        self.sent.append((code, payload))

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def _goal_body():
    return {
        "statement": "认证收敛验证",
        "target_user": "老田",
        "assumptions": [{"id": "A1", "text": "攻击面收敛", "status": "hypothesis"}],
        "success_signals": ["伪造头不再通过"],
    }


def _session(monkeypatch, sub="u-sess-1"):
    """让 auth_gateway.STORE 只认 sid-good。"""
    def fake_get(sid):
        return {"sub": sub, "session_id": sid} if sid == "sid-good" else None
    monkeypatch.setattr(G.STORE, "get", fake_get)


# ── 认证：旧伪造口径全部拒绝 ────────────────────────────────────────────────

def test_forged_proxy_headers_rejected(store):
    """审计原文 PoC：X-Portal-Proxy + X-Real-IP + Origin + X-OS-Page 全伪造 → 403。"""
    h = _FakeHandler(store, headers={
        "X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.50",
        "Origin": "http://192.168.1.50:8734", "X-OS-Page": "quick",
    }, body=_goal_body())
    h._os_goal_post("/api/os/goal")
    assert h.sent and h.sent[-1][0] == 403, h.sent
    assert store.list_goals() == []  # 未写账本


def test_public_ip_forged_headers_rejected(store):
    h = _FakeHandler(store, headers={
        "X-Portal-Proxy": "1", "X-Real-IP": "8.8.8.8",
        "Origin": "http://127.0.0.1:8734", "X-OS-Page": "quick",
    }, body=_goal_body())
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 403


def test_loopback_peer_allowed_without_credentials(store):
    """直连 8091 的本机进程（peer 回环）保留放行（既有本机语义）。"""
    h = _FakeHandler(store, headers={}, peer="127.0.0.1", body=_goal_body())
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 201, h.sent
    assert len(store.list_goals()) == 1


def test_valid_portal_token_allowed(store):
    h = _FakeHandler(store, headers={}, peer="172.17.0.1", body=_goal_body(),
                     token_ok=True)
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 201


def test_wrong_or_missing_token_rejected(store):
    h = _FakeHandler(store, headers={}, peer="172.17.0.1", body=_goal_body(),
                     token_ok=False)
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 403


def test_valid_zb_session_allowed(store, monkeypatch):
    _session(monkeypatch, sub="u-sess-1")
    h = _FakeHandler(store, headers={"Cookie": "a=b; zb_session=sid-good"},
                     peer="172.17.0.1", body=_goal_body())
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 201, h.sent


def test_invalid_zb_session_rejected(store, monkeypatch):
    _session(monkeypatch)
    h = _FakeHandler(store, headers={"Cookie": "zb_session=forged-sid"},
                     peer="172.17.0.1", body=_goal_body())
    h._os_goal_post("/api/os/goal")
    assert h.sent[-1][0] == 403
    assert store.list_goals() == []


def test_session_sub_helper(monkeypatch):
    _session(monkeypatch)
    assert A._zb_session_sub({"Cookie": "zb_session=sid-good"}) == "u-sess-1"
    assert A._zb_session_sub({"Cookie": "zb_session=bad"}) == ""
    assert A._zb_session_sub({}) == ""
    assert A._zb_session_sub({"Cookie": "other=1"}) == ""


def test_deprecated_header_helpers_are_false():
    """旧认证辅助函数仅为 mixin 基线兼容保留，必须恒 False（禁止再作为凭据）。"""
    forged = {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.50",
              "Origin": "http://192.168.1.50:8734", "X-OS-Page": "quick"}
    assert A.is_local_page_request(forged) is False
    assert A.is_local_page_request({}) is False
    assert A._origin_is_local_entry("http://127.0.0.1:8734") is False


# ── 预览：强制 text/plain + nosniff（防存储型 XSS）──────────────────────────

def _make_item_with_html(store):
    g = store.create_goal("XSS 回归", "老田",
                          [{"id": "A1", "text": "h", "status": "hypothesis"}],
                          ["预览不执行"])
    iid = store.create_item(g["payload"]["goal_id"], "恶意产物", "now",
                            "software")["payload"]["item_id"]
    ws = os.path.join(store.base_dir, "items", iid, "workspace")
    os.makedirs(ws, exist_ok=True)
    with open(os.path.join(ws, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<script>alert(document.cookie)</script>")
    return iid


def test_workspace_preview_is_text_plain(store):
    iid = _make_item_with_html(store)
    status, ctype, body = A.read_workspace_file(store, iid, "index.html")
    assert status == 200
    assert ctype == "text/plain; charset=utf-8"
    assert b"<script>" in body  # 原文可读但不以 HTML 执行


def test_workspace_preview_sets_nosniff(store):
    iid = _make_item_with_html(store)
    h = _FakeHandler(store, headers={}, peer="127.0.0.1")
    h._os_goal_get("/api/os/item/%s/workspace/index.html" % iid)
    assert h.status == 200
    hdrs = dict((k.lower(), v) for k, v in h.sent_headers)
    assert hdrs.get("content-type") == "text/plain; charset=utf-8"
    assert hdrs.get("x-content-type-options") == "nosniff"


def test_preview_disallowed_file_404(store):
    iid = _make_item_with_html(store)
    status, ctype, _ = A.read_workspace_file(store, iid, "evil.exe")
    assert status == 404 and ctype.startswith("text/plain")
