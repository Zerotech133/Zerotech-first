# -*- coding: utf-8 -*-
"""S1（2026-11-03）8091 会话持久化单元测试。

覆盖（≥6）：
1. 默认内存行为、关闭持久化不产生 DB 文件；
2. 持久化会话可被新 store（模拟 8091 重启）读取；
3. 过期清理（get 触发 purge）；
4. 登出删除 DB 行；
5. 损坏 DB fail-closed（读=无会话；写=SessionPersistenceError，不降级内存）；
6. **无明文 IdP token 落盘**；
7. login/me/guard/logout 跨重启兼容（换 STORE 模拟重启）；
8. 持久化不可用时登录返回 503、不下发 cookie；
9. 并发线程安全。

不依赖网络/真实凭据/8091 进程。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))
import auth_gateway as gw  # noqa: E402


def _ctx(sub: str = "zero_bound:owner"):
    return {
        "sub": sub,
        "actor_type": "member",
        "roles": ["ADMIN"],
        "display_name": "老田",
        "source": "zero_bound",
        "source_ids": {"zero_bound_actor": "owner"},
    }


def _db_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT session_id, sub FROM auth_sessions").fetchall()
    finally:
        conn.close()


class _H:
    """最小 Handler 桩（同 portal/tests/test_auth_gateway.py 风格）。"""

    def __init__(self, body=None, cookie="", csrf=""):
        raw = json.dumps(body).encode() if isinstance(body, dict) else (body or b"")
        self.headers = {"Content-Length": str(len(raw)), "Cookie": cookie}
        if csrf:
            self.headers["X-CSRF-Token"] = csrf
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.status = None
        self.headers_out = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers_out.append((key, value))

    def end_headers(self):
        pass

    @property
    def payload(self):
        self.wfile.seek(0)
        return json.loads(self.wfile.read().decode("utf-8"))

    def cookie(self):
        for k, v in self.headers_out:
            if k.lower() == "set-cookie":
                return v
        return ""


# ── 1. 默认内存 ──────────────────────────────────────────────────────────────
def test_default_is_memory_and_never_touches_db(tmp_path):
    db = tmp_path / "mem.db"
    store = gw.SessionStore(persist=False, db_path=str(db))
    assert store.persist is False
    sid = store.create("zero_bound:owner", _ctx(), "tok")
    assert store.get(sid) is not None
    assert store.delete(sid) is True
    assert store.get(sid) is None
    assert not db.exists(), "关闭持久化时不得创建 DB 文件"
    assert gw.SessionStore().persist == gw.SESSION_PERSIST_ENABLED
    if (os.environ.get("ZB_SESSION_PERSIST", "0") or "0").strip().lower() in ("", "0"):
        assert gw.SESSION_PERSIST_ENABLED is False, "默认必须关闭（激活属 L2）"


# ── 2. 持久化后新 store 可读（模拟重启）────────────────────────────────────
def test_persisted_session_survives_new_store(tmp_path):
    db = tmp_path / "s1.db"
    s1 = gw.SessionStore(persist=True, db_path=str(db))
    sid = s1.create("zero_bound:owner", _ctx(), "idp-token-SHOULD-NOT-PERSIST")
    s2 = gw.SessionStore(persist=True, db_path=str(db))
    rec = s2.get(sid)
    assert rec is not None
    assert rec["sub"] == "zero_bound:owner"
    assert rec["identity_context"]["roles"] == ["ADMIN"]
    assert rec["identity_context"]["display_name"] == "老田"
    assert rec["zhice_token"] == "" and rec["token_persisted"] is False
    assert rec["_csrf"] and len(rec["csrf_hash"]) == 64
    assert len(_db_rows(db)) == 1


# ── 3. 过期清理 ──────────────────────────────────────────────────────────────
def test_expired_persisted_session_purged_on_read(tmp_path):
    db = tmp_path / "s2.db"
    store = gw.SessionStore(ttl=-1, persist=True, db_path=str(db))
    sid = store.create("zero_bound:owner", _ctx(), "")
    assert store.get(sid) is None
    assert _db_rows(db) == [], "过期行应在 get 时清理"


# ── 4. 登出删除 DB 行 ────────────────────────────────────────────────────────
def test_logout_delete_removes_persisted_row(tmp_path):
    db = tmp_path / "s3.db"
    s1 = gw.SessionStore(persist=True, db_path=str(db))
    sid = s1.create("zero_bound:owner", _ctx(), "")
    assert len(_db_rows(db)) == 1
    s2 = gw.SessionStore(persist=True, db_path=str(db))
    assert s2.delete(sid) is True
    assert _db_rows(db) == []
    assert gw.SessionStore(persist=True, db_path=str(db)).get(sid) is None


# ── 5. 损坏 DB fail-closed ────────────────────────────────────────────────────
def test_corrupt_db_fail_closed(tmp_path):
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database\x00\x01\x02")
    store = gw.SessionStore(persist=True, db_path=str(db))
    # 读：fail-closed（视为无会话 → guard/me 401），不抛穿到未鉴权放行
    assert store.get("any-sid") is None
    # 写：fail-visible（登录路径 503，不静默降级到内存）
    with pytest.raises(gw.SessionPersistenceError):
        store.create("zero_bound:owner", _ctx(), "tok")
    assert store.get("any-sid") is None


# ── 6. 无明文 IdP token 落盘 ─────────────────────────────────────────────────
def test_no_plaintext_idp_token_in_db(tmp_path):
    db = tmp_path / "tok.db"
    marker = "MARKER-IDP-TOKEN-4f8a1c9d"
    store = gw.SessionStore(persist=True, db_path=str(db))
    sid = store.create("zero_bound:owner", _ctx(), marker)
    blobs = b"".join(p.read_bytes() for p in tmp_path.glob("tok.db*")
                     if p.is_file()) if tmp_path.exists() else b""
    assert marker.encode() not in blobs, "DB/WAL 中不得出现明文 IdP token"
    # 内存 rec 仍保留 token（同进程兼容）；跨 store 恢复后为空
    assert store.get(sid)["zhice_token"] == marker
    assert gw.SessionStore(persist=True, db_path=str(db)).get(sid)["zhice_token"] == ""


# ── 7. login/me/guard/logout 跨重启兼容 ─────────────────────────────────────
def test_login_guard_logout_across_restart(tmp_path, monkeypatch):
    db = tmp_path / "restart.db"
    monkeypatch.setattr(gw, "STORE", gw.SessionStore(persist=True, db_path=str(db)))
    monkeypatch.setattr(gw, "_zhice_login", lambda u, p: {
        "user_id": "u-96d51f4d9b7a2826", "token": "fake-zhice-token-abc", "role": "USER"})
    h = _H({"username": "老田", "password": "pw"})
    gw.login(h)
    assert h.status == 200
    sid = h.cookie().split("zb_session=")[1].split(";")[0]

    # 模拟 8091 重启：换全新 SessionStore（同一 DB，内存清空）
    monkeypatch.setattr(gw, "STORE", gw.SessionStore(persist=True, db_path=str(db)))

    me = _H(cookie="zb_session=" + sid)
    gw.me(me)
    assert me.status == 200
    assert me.payload["session"]["sub"] == "zero_bound:owner"
    csrf = me.payload["csrf_token"]

    guard_h = _H(cookie="zb_session=" + sid)
    gw.guard(guard_h)
    assert guard_h.status == 200

    out = _H(cookie="zb_session=" + sid, csrf=csrf)
    gw.logout(out)
    assert out.status == 200
    assert _db_rows(db) == []

    after = _H(cookie="zb_session=" + sid)
    gw.me(after)
    assert after.status == 401


# ── 8. 持久化不可用 → 登录 503、不下发 cookie ───────────────────────────────
def test_login_503_when_persist_unavailable(tmp_path, monkeypatch):
    db = tmp_path / "broken.db"
    db.write_bytes(b"not sqlite")
    monkeypatch.setattr(gw, "STORE", gw.SessionStore(persist=True, db_path=str(db)))
    monkeypatch.setattr(gw, "_zhice_login", lambda u, p: {
        "user_id": "u-96d51f4d9b7a2826", "token": "t", "role": "USER"})
    h = _H({"username": "老田", "password": "pw"})
    gw.login(h)
    assert h.status == 503
    assert h.payload["error"] == "session_store_unavailable"
    assert h.cookie() == ""


# ── 9. 并发线程安全 ─────────────────────────────────────────────────────────
def test_thread_safety_concurrent_ops(tmp_path):
    db = tmp_path / "thread.db"
    store = gw.SessionStore(persist=True, db_path=str(db))
    sids, errors = [], []

    def worker(i):
        try:
            sid = store.create("zero_bound:owner", _ctx(), "tok-%d" % i)
            sids.append(sid)
            assert store.get(sid) is not None
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(sids)) == 8
    assert len(_db_rows(db)) == 8
    for sid in sids:
        assert store.delete(sid) is True
    assert _db_rows(db) == []
