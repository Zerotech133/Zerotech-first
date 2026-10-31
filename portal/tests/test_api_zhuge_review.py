# -*- coding: utf-8 -*-
"""api_zhuge_review HTTP 适配器测试（stub handler，不起服务）。

覆盖 P0 基线（feature flag/未授权/202/status 鉴权）+ 运营化
（WO-LEGION-REVIEWOPS-20261031）：adoption 子资源、session 限流 429+retry_after_s、
旧 action=adopt 兼容、未知子路径 404。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))
import api_zhuge_review as api  # noqa: E402
import zhuge_review as z  # noqa: E402


class H:
    def __init__(self, body=b"{}", cookie=""):
        self.headers = {"Content-Length": str(len(body))}
        if cookie:
            self.headers["Cookie"] = cookie
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _seed_done(review_id, findings, task_id="WO-TEST"):
    with z._LOCK:
        z._JOBS[review_id] = {
            "ts": time.time(), "key": "k-" + review_id, "task_id": task_id,
            "payload": {"ok": True, "review_id": review_id, "task_id": task_id,
                        "status": "done", "verdict": "concerns",
                        "findings": findings, "read_only": True},
        }


def _ledger_rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


# ── P0 基线：flag / 未授权 / 202 / status 鉴权 ─────────────────────────
def test_feature_off_404(monkeypatch):
    monkeypatch.delenv(z.FEATURE_ENV, raising=False)
    h = H()
    api.handle_post(h)
    assert h.status == 404


def test_unauthenticated_403(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.delenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", raising=False)
    h = H()
    api.handle_post(h)
    assert h.status == 403


def test_post_202_with_anonymous_test_flag(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    monkeypatch.setattr(api.zhuge_review, "start_review",
                        lambda payload, session_key="": (202, {"ok": True, "review_id": "zr-x"}))
    body = json.dumps({"task_id": "T", "diff": "d"}).encode()
    h = H(body)
    api.handle_post(h)
    assert h.status == 202 and h.payload["review_id"] == "zr-x"


def test_get_status(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    monkeypatch.setattr(api.zhuge_review, "status",
                        lambda: {"enabled": True, "model": "m"})
    h = H()
    api.handle_get(h, "/api/zhuge/review/status")
    assert h.status == 200 and h.payload["model"] == "m"


def test_status_requires_auth(monkeypatch):
    monkeypatch.delenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", raising=False)
    h = H()
    api.handle_get(h, "/api/zhuge/review/status")
    assert h.status == 403


def test_status_anonymous_flag_allows(monkeypatch):
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    h = H()
    api.handle_get(h, "/api/zhuge/review/status")
    assert h.status == 200


# ── 运营化：session 限流 429 + retry_after_s（可注入 clock）─────────────
def test_post_rate_limited_429(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    monkeypatch.setattr(z, "RATE_PER_SESSION", 1)
    monkeypatch.setattr(z, "CLOCK", lambda: 100.0)
    z._RATE_BY_SESSION.clear()
    z._CACHE.clear()

    class _Exec:
        def submit(self, *a, **k):
            pass

    monkeypatch.setattr(z, "_EXECUTOR", _Exec())
    h = H(json.dumps({"task_id": "T", "diff": "d1"}).encode())
    api.handle_post(h)
    assert h.status == 202, "首单应放行"
    h2 = H(json.dumps({"task_id": "T", "diff": "d2"}).encode())
    api.handle_post(h2)
    assert h2.status == 429
    assert h2.payload["error"] == "rate_limited"
    assert h2.payload["retry_after_s"] == 3601
    assert h2.payload["scope"] == "session"


def test_rate_limit_buckets_per_session_cookie(monkeypatch):
    """不同门户 session cookie → 不同限额桶，互不挤占。"""
    key_a = z._session_bucket("hash-a")
    key_b = z._session_bucket("hash-b")
    assert key_a != key_b
    monkeypatch.setattr(z, "RATE_PER_SESSION", 1)
    monkeypatch.setattr(z, "CLOCK", lambda: 200.0)
    z._RATE_BY_SESSION.clear()
    assert z._rate_check(key_a)[0] is True
    assert z._rate_check(key_a)[0] is False
    assert z._rate_check(key_b)[0] is True, "不同 session 不应共享额度"


# ── 运营化：adoption 子资源 ────────────────────────────────────────────
def test_adoption_endpoint_success_and_get_returns_adoption(monkeypatch, tmp_path):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    ledger = tmp_path / "zhuge_review_adoption.jsonl"
    monkeypatch.setattr(z, "ADOPTION_PATH", ledger)
    _seed_done("zr-http-1", [{"id": "zf-aaa", "severity": "P1", "file": "a.py", "line": 1},
                             {"id": "zf-bbb", "severity": "P2", "file": "b.py", "line": 2}])
    body = json.dumps({"adopted": ["zf-aaa"], "ignored": ["zf-bbb"],
                       "note": "修复 a，忽略 b"}).encode()
    h = H(body)
    api.handle_post(h, "/api/zhuge/review/zr-http-1/adoption")
    assert h.status == 200 and h.payload["ok"] is True
    rows = _ledger_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["adopted"] == ["zf-aaa"] and rows[0]["ignored"] == ["zf-bbb"]
    assert rows[0]["note"] == "修复 a，忽略 b"
    assert rows[0]["by"], "by 缺省应取 session 标识（匿名测试为 anonymous）"
    # GET review/<id> 返回 adoption
    h2 = H()
    api.handle_get(h2, "/api/zhuge/review/zr-http-1")
    assert h2.status == 200
    assert h2.payload["adoption"]["adopted"] == ["zf-aaa"]


def test_adoption_requires_auth(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.delenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", raising=False)
    h = H(b'{"adopted":["zf-a"]}')
    api.handle_post(h, "/api/zhuge/review/zr-x/adoption")
    assert h.status == 403


def test_adoption_feature_off_404(monkeypatch):
    monkeypatch.delenv(z.FEATURE_ENV, raising=False)
    h = H(b'{"adopted":["zf-a"]}')
    api.handle_post(h, "/api/zhuge/review/zr-x/adoption")
    assert h.status == 404


def test_adoption_unknown_review_404(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    h = H(b'{"adopted":["zf-a"]}')
    api.handle_post(h, "/api/zhuge/review/zr-missing/adoption")
    assert h.status == 404 and h.payload["error"] == "review_not_found"


def test_adoption_bad_finding_id_400(monkeypatch, tmp_path):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    monkeypatch.setattr(z, "ADOPTION_PATH", tmp_path / "a.jsonl")
    _seed_done("zr-http-bad", [{"id": "zf-ok"}])
    h = H(b'{"adopted":["zf-nope"]}')
    api.handle_post(h, "/api/zhuge/review/zr-http-bad/adoption")
    assert h.status == 400 and h.payload["error"] == "bad_finding_id"


def test_post_unknown_subpath_404(monkeypatch):
    """portal_api 把 /api/zhuge/review/* POST 全交给本模块，未知子路径必须 404。"""
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    h = H()
    api.handle_post(h, "/api/zhuge/review/x/other")
    assert h.status == 404 and h.payload["error"] == "not_found"


def test_legacy_post_action_adopt_still_works(monkeypatch, tmp_path):
    """旧调用形态兼容：POST /api/zhuge/review body.action=adopt。"""
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setenv("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "1")
    ledger = tmp_path / "legacy.jsonl"
    monkeypatch.setattr(z, "ADOPTION_PATH", ledger)
    _seed_done("zr-legacy", [{"id": "zf-leg"}])
    body = json.dumps({"action": "adopt", "review_id": "zr-legacy",
                       "decision": "accept", "note": "n", "actor": "qa"}).encode()
    h = H(body)
    api.handle_post(h)
    assert h.status == 200 and h.payload["ok"] is True
    assert _ledger_rows(ledger)[0]["decision"] == "accept"
