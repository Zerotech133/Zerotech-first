# -*- coding: utf-8 -*-
"""P0-B API 加固测试（K-ORG-20261031-APIHARDEN，审计 v2 H3/M3/M5）。

覆盖（stub handler，不起服务、不写生产账、不联网）：
- H3 `/api/analysis/submit|review` 必须过 token 闸门：api_analysis.handle_post
  首行 `_check_portal_token()`（未授权 403 / 合法 token 通过）；
  且 portal_api 的 analysis 分派已移到 `_MUTATION_POSTS` 闸门之后（源码顺序回归）。
- M3 JEV review resolve/reopen 需门户登录会话（`_authorized`）；
  reviewer 一律取会话身份，body 自报他人 → 403 reviewer_mismatch。
- M5 8091 `_is_local_req` 必须校验 client_address 回环，不再“缺 X-Portal-Proxy 即本机”。
"""
from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_analysis  # noqa: E402
import api_jev_review  # noqa: E402
import portal_api  # noqa: E402


class H:
    """最小 handler stub：记录 _json 响应；rfile 提供请求体。"""

    def __init__(self, body=b"{}", token_ok: bool = False, cookie: str = ""):
        self.headers = {"Content-Length": str(len(body))}
        if cookie:
            self.headers["Cookie"] = cookie
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None
        self._token_ok = token_ok

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj

    def _check_portal_token(self):
        return self._token_ok


# ---------------------------------------------------------------- H3 analysis
def _fake_analysis_package(monkeypatch, queue_submit=None, review_apply=None):
    """注入假 analysis 包，避免 api_analysis 惰性 import 落到真实产线模块。"""
    pkg = types.ModuleType("analysis")
    pkg.__path__ = []
    queue_mod = types.ModuleType("analysis.queue")
    review_mod = types.ModuleType("analysis.review")
    if queue_submit is not None:
        queue_mod.submit = queue_submit
    if review_apply is not None:
        review_mod.apply = review_apply
    pkg.queue = queue_mod
    pkg.review = review_mod
    monkeypatch.setitem(sys.modules, "analysis", pkg)
    monkeypatch.setitem(sys.modules, "analysis.queue", queue_mod)
    monkeypatch.setitem(sys.modules, "analysis.review", review_mod)


def test_analysis_submit_requires_token(monkeypatch):
    calls = []
    _fake_analysis_package(
        monkeypatch,
        queue_submit=lambda *a, **k: calls.append(k) or {"ok": True, "job_id": "x"},
    )
    body = json.dumps({"code": "600337.SH"}).encode()
    h = H(body, token_ok=False)
    api_analysis.handle_post(h, "/api/analysis/submit", "")
    assert h.status == 403, h.payload
    assert h.payload["ok"] is False
    assert calls == [], "未授权不得入队"


def test_analysis_review_requires_token(monkeypatch):
    calls = []
    _fake_analysis_package(
        monkeypatch,
        review_apply=lambda *a, **k: calls.append(k) or {"ok": True},
    )
    body = json.dumps({"event_id": "e1", "action": "approve"}).encode()
    h = H(body, token_ok=False)
    api_analysis.handle_post(h, "/api/analysis/review", "")
    assert h.status == 403 and h.payload["ok"] is False
    assert calls == [], "未授权不得改复核状态"


def test_analysis_submit_valid_token_enqueues(monkeypatch):
    seen = {}

    def fake_submit(code, llm=False, push=False):
        seen.update(code=code, llm=llm, push=push)
        return {"ok": True, "job_id": "job-1"}

    _fake_analysis_package(monkeypatch, queue_submit=fake_submit)
    body = json.dumps({"code": "600337.SH", "llm": False, "push": False}).encode()
    h = H(body, token_ok=True)
    api_analysis.handle_post(h, "/api/analysis/submit", "")
    assert h.status == 202, h.payload
    assert h.payload["ok"] is True and h.payload["job_id"] == "job-1"
    assert seen == {"code": "600337.SH", "llm": False, "push": False}


def test_analysis_review_valid_token_applies(monkeypatch):
    seen = {}

    def fake_apply(event_id, action, category=None, note=None, path=None):
        seen.update(event_id=event_id, action=action)
        return {"ok": True, "event_id": event_id}

    _fake_analysis_package(monkeypatch, review_apply=fake_apply)
    body = json.dumps({"event_id": "e1", "action": "approve"}).encode()
    h = H(body, token_ok=True)
    api_analysis.handle_post(h, "/api/analysis/review", "")
    assert h.status == 200, h.payload
    assert seen == {"event_id": "e1", "action": "approve"}


def test_portal_api_analysis_dispatch_is_after_token_gate():
    """H3 回归：analysis 分派不得再作为 token 闸门前的“早分支”出现。"""
    src = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    gate = src.index("if path in _MUTATION_POSTS and not self._check_portal_token():")
    dispatch = src.index("path.startswith('/api/analysis/')")
    assert gate < dispatch, "analysis 分支必须位于 _MUTATION_POSTS token 闸门之后"
    # 两条写路径仍在变更类清单内（双保险）
    assert '"/api/analysis/submit"' in src
    assert '"/api/analysis/review"' in src


# ---------------------------------------------------------------- M3 JEV review
class _FakeStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, sid):
        return self._sessions.get(sid)


def _fake_portal_session(monkeypatch, sub="老田", sid="sid-ok"):
    import auth_gateway

    monkeypatch.setattr(auth_gateway, "STORE",
                        _FakeStore({sid: {"sub": sub}}))
    return "zb_session=%s" % sid


def test_jev_review_unauthenticated_403(monkeypatch):
    calls = []
    monkeypatch.setattr(api_jev_review, "resolve",
                        lambda *a, **k: calls.append(a) or {"ok": True})
    h = H(json.dumps({"id": "q1", "decision": "correct",
                      "reviewer": "老田"}).encode())
    api_jev_review.handle_post(h, "/api/jev/review/resolve")
    assert h.status == 403 and h.payload["error"] == "unauthorized"
    assert calls == []


def test_jev_review_forged_reviewer_rejected(monkeypatch):
    cookie = _fake_portal_session(monkeypatch)
    calls = []
    monkeypatch.setattr(api_jev_review, "resolve",
                        lambda *a, **k: calls.append(a) or {"ok": True})
    h = H(json.dumps({"id": "q1", "decision": "correct",
                      "reviewer": "冒名者"}).encode(), cookie=cookie)
    api_jev_review.handle_post(h, "/api/jev/review/resolve")
    assert h.status == 403 and h.payload["error"] == "reviewer_mismatch"
    assert calls == [], "伪造 reviewer 不得落库"


def test_jev_review_uses_session_reviewer(monkeypatch):
    cookie = _fake_portal_session(monkeypatch, sub="老田")
    seen = {}

    def fake_resolve(qid, decision, reviewer, note=""):
        seen.update(id=qid, decision=decision, reviewer=reviewer, note=note)
        return {"ok": True, "id": qid}

    monkeypatch.setattr(api_jev_review, "resolve", fake_resolve)
    h = H(json.dumps({"id": "q1", "decision": "correct",
                      "note": "n"}).encode(), cookie=cookie)
    api_jev_review.handle_post(h, "/api/jev/review/resolve")
    assert h.status == 200, h.payload
    assert seen["reviewer"] == "老田", "reviewer 必须取会话身份而非 body"
    assert seen["id"] == "q1" and seen["decision"] == "correct"


def test_jev_reopen_uses_session_actor(monkeypatch):
    cookie = _fake_portal_session(monkeypatch, sub="laotian")
    seen = {}

    def fake_reopen(qid, reviewer, note=""):
        seen.update(id=qid, reviewer=reviewer, note=note)
        return {"ok": True, "id": qid}

    monkeypatch.setattr(api_jev_review, "reopen", fake_reopen)
    h = H(json.dumps({"id": "q2", "note": "重开"}).encode(), cookie=cookie)
    api_jev_review.handle_post(h, "/api/jev/review/reopen")
    assert h.status == 200, h.payload
    assert seen == {"id": "q2", "reviewer": "laotian", "note": "重开"}


# ---------------------------------------------------------------- M5 8091 peer
def _local_handler(peer, headers=None):
    h = object.__new__(portal_api.Handler)
    h.headers = dict(headers or {})
    h.client_address = peer
    return h


def test_is_local_req_requires_loopback_client_address():
    # M5 回归：旧逻辑“缺 X-Portal-Proxy 即本机”会对远端返回 True
    assert _local_handler(("127.0.0.1", 1))._is_local_req() is True
    assert _local_handler(("::1", 1))._is_local_req() is True
    assert _local_handler(("192.168.1.50", 1))._is_local_req() is False
    assert _local_handler(("10.0.0.2", 1))._is_local_req() is False
    assert _local_handler(())._is_local_req() is False  # 无 peer 不可信


def test_is_local_req_proxy_headers_need_loopback_peer_and_real_ip():
    h = _local_handler(("127.0.0.1", 1),
                       {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"})
    assert h._is_local_req() is True
    h = _local_handler(("127.0.0.1", 1),
                       {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.50"})
    assert h._is_local_req() is False
    # 远端 peer + 伪造 loopback 头仍然不可信（peer 才是真相）
    h = _local_handler(("192.168.1.50", 1),
                       {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"})
    assert h._is_local_req() is False


def test_check_portal_token_remote_without_token_denied(monkeypatch):
    import cainiao_auth

    class _Denied:
        ok = False

    monkeypatch.setattr(cainiao_auth, "verify_request",
                        lambda headers, ip, token: _Denied())
    h = _local_handler(("192.168.1.50", 1))
    assert h._check_portal_token() is False


def test_check_portal_token_remote_with_valid_token_allowed(monkeypatch):
    import cainiao_auth

    class _Denied:
        ok = False

    monkeypatch.setattr(cainiao_auth, "verify_request",
                        lambda headers, ip, token: _Denied())
    monkeypatch.setattr(portal_api, "_PORTAL_TOKEN", "tok-p0b-test")
    h = _local_handler(("192.168.1.50", 1),
                       {"X-Portal-Token": "tok-p0b-test"})
    assert h._check_portal_token() is True
