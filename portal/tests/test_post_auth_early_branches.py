# -*- coding: utf-8 -*-
"""P142 回归：do_POST 早分支变更端点必须先过 X-Portal-Token（审计 L-01/L-02）。"""
from __future__ import annotations

import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import portal_api  # noqa: E402


class H:
    """最小 handler stub：只提供 do_POST 早分支所需属性/方法。"""

    def __init__(self, path, token_ok=False):
        self.path = path
        self._token_ok = token_ok
        self.status = None
        self.payload = None

    def _check_portal_token(self):
        return self._token_ok

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def test_analysis_early_branch_requires_token(monkeypatch):
    import api_analysis
    called = {"n": 0}
    monkeypatch.setattr(api_analysis, "handle_post",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    h = H("/api/analysis/submit", token_ok=False)
    portal_api.Handler.do_POST(h)
    assert h.status == 403 and called["n"] == 0


def test_jev_review_early_branch_requires_token(monkeypatch):
    import api_jev_review
    called = {"n": 0}
    monkeypatch.setattr(api_jev_review, "handle_post",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    h = H("/api/jev/review/resolve", token_ok=False)
    portal_api.Handler.do_POST(h)
    assert h.status == 403 and called["n"] == 0


def test_analysis_early_branch_with_token_passes(monkeypatch):
    import api_analysis
    seen = {}
    monkeypatch.setattr(api_analysis, "handle_post",
                        lambda handler, path, query="": seen.update(path=path))
    h = H("/api/analysis/submit", token_ok=True)
    portal_api.Handler.do_POST(h)
    # 进入 handler 分支（stub handler 不写 status）；证明 token 通过到达业务
    assert seen.get("path", "").startswith("/api/analysis/")


def test_mutation_posts_registered():
    assert "/api/jev/review/resolve" in portal_api._MUTATION_POSTS
    assert "/api/jev/review/reopen" in portal_api._MUTATION_POSTS
    assert "/api/analysis/submit" in portal_api._MUTATION_POSTS
