# -*- coding: utf-8 -*-
"""api_zhuge_review HTTP 适配器测试（stub handler，不起服务）。"""
from __future__ import annotations

import io
import json
import sys
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
                        lambda payload: (202, {"ok": True, "review_id": "zr-x"}))
    body = json.dumps({"task_id": "T", "diff": "d"}).encode()
    h = H(body)
    api.handle_post(h)
    assert h.status == 202 and h.payload["review_id"] == "zr-x"


def test_get_status(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
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
