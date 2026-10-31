# -*- coding: utf-8 -*-
"""W1（2026-10-31）live-version smoke 骨架：信任链/发布单元只读验收。

验收口径（docs/plans/2026-10-31_零界研发计划_v2_收口与闭环.md W1）：

    8734 一次登录 → /hermes/ 200（未登录 302 /login）
    8787 直连 /api/sessions → 401（/health 200 公共探活）
    8103 /api/ide/version → 200 all_match=true
    current junction 实际指向 == current.json 指针（无 drift）
    served /extensions/* 与 manifest hash 一致（auth 后，见验收报告）

本文件是**骨架**，分两层：

1) 离线单测（默认运行，零网络/零生产写）：桩替换 urlopen，验证 verify_live()
   的对账逻辑（版本一致 / 版本漂移 / junction drift / 不可达 fail-closed）。
2) live 冒烟（默认 pytest.skip，须显式 `LEGION_LIVE_SMOKE=1` 才跑）：只读 GET
   8103 version、8787 health/sessions、8734 SSO 门。不重启、不发布、不加凭证。

边界：只读；不重启 8103/8787/nginx；不发布 artifact；不写 profile。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "release_legion_ide.py"
RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
LIVE = os.environ.get("LEGION_LIVE_SMOKE") == "1"


def _load():
    spec = importlib.util.spec_from_file_location("rel_ide_live_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact(root: Path, ver: str, content: bytes) -> Path:
    import hashlib

    d = root / ver
    d.mkdir(parents=True)
    (d / "app.js").write_bytes(content)
    manifest = {
        "schema": "legion-ide-release/v1", "version": ver, "commit": "test-commit",
        "files": {"app.js": {"sha256": hashlib.sha256(content).hexdigest(),
                             "bytes": len(content)}},
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _release(root: Path, mod):
    """构造 v1(current) + v2 两个 artifact，默认发布 v1。"""
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    mod.promote(root, "v1", switch_current=True)
    return root


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, mod, payload=None, exc=None):
    def _open(url, timeout=None):
        if exc is not None:
            raise exc
        return _FakeResp(payload)

    monkeypatch.setattr(mod.urllib.request, "urlopen", _open)


# ── 离线单测：对账逻辑 ─────────────────────────────────────────────────────

def test_verify_live_ok_when_live_matches_pointer_and_junction(tmp_path, monkeypatch):
    m = _load()
    root = _release(tmp_path / "rel", m)
    payload = {"ok": True, "version": "v1", "commit": "c",
               "artifact_dir": str(root / "v1"), "all_match": True}
    _patch_urlopen(monkeypatch, m, payload=payload)
    res = m.verify_live(root, url="http://stub/api/ide/version")
    assert res["ok"] is True, res
    assert res["checks"] == {
        "live_reachable": True, "live_all_match": True,
        "version_matches_pointer": True, "artifact_matches_live": True,
        "junction_not_drifted": True,
    }


def test_verify_live_detects_version_mismatch(tmp_path, monkeypatch):
    m = _load()
    root = _release(tmp_path / "rel", m)
    payload = {"ok": True, "version": "v2", "artifact_dir": str(root / "v1"),
               "all_match": True}
    _patch_urlopen(monkeypatch, m, payload=payload)
    res = m.verify_live(root, url="http://stub/api/ide/version")
    assert res["ok"] is False
    assert res["checks"]["version_matches_pointer"] is False


def test_verify_live_detects_junction_drift(tmp_path, monkeypatch):
    m = _load()
    root = _release(tmp_path / "rel", m)
    # 指针改 v2，junction 仍 v1 → drift
    m._write_current_json(root, {"version": "v2", "dir": str(root / "v2"),
                                 "promoted_at": "x", "note": "drift"})
    payload = {"ok": True, "version": "v2", "artifact_dir": str(root / "v2"),
               "all_match": True}
    _patch_urlopen(monkeypatch, m, payload=payload)
    res = m.verify_live(root, url="http://stub/api/ide/version")
    assert res["ok"] is False
    assert res["checks"]["junction_not_drifted"] is False


def test_verify_live_fails_closed_when_unreachable(tmp_path, monkeypatch):
    m = _load()
    root = _release(tmp_path / "rel", m)
    _patch_urlopen(monkeypatch, m, exc=OSError("connection refused"))
    res = m.verify_live(root, url="http://stub/api/ide/version", timeout=0.1)
    assert res["ok"] is False
    assert res["checks"]["live_reachable"] is False
    assert "connection refused" in res["error"]


# ── live 冒烟（默认 skip；只读）────────────────────────────────────────────

def _http_get(port: int, path: str, timeout: float = 5.0):
    """无重定向 GET：返回 (status, location, body_bytes)。不跟随跳转，便于断言 302。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Connection": "close"})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Location") or "", body
    finally:
        conn.close()


@pytest.mark.skipif(not LIVE, reason="live smoke 需显式 LEGION_LIVE_SMOKE=1（只读）")
def test_live_8103_version_matches_release_pointer():
    """8103 /api/ide/version 必须 200 all_match 且与本地指针/junction 一致。"""
    if not RELEASE_ROOT.is_dir():
        pytest.skip("release root 不存在（非生产机）")
    m = _load()
    res = m.verify_live(RELEASE_ROOT)
    assert res["ok"] is True, json.dumps(res, ensure_ascii=False)
    assert res["live"]["all_match"] is True


@pytest.mark.skipif(not LIVE, reason="live smoke 需显式 LEGION_LIVE_SMOKE=1（只读）")
def test_live_sso_trust_chain_gates():
    """SSO 信任链门：8734 未登录跳登录、8787 直连 401、公共探活 200。"""
    try:
        st, loc, _ = _http_get(8734, "/hermes/")
    except OSError as exc:
        pytest.skip("8734 不可达: %s" % exc)
    assert st == 302 and "/login" in loc, (st, loc)
    st, _, _ = _http_get(8787, "/api/sessions")
    assert st == 401, st
    st, _, _ = _http_get(8787, "/health")
    assert st == 200, st
    st, _, body = _http_get(8103, "/api/ide/version")
    assert st == 200, st
    assert json.loads(body.decode("utf-8")).get("all_match") is True
