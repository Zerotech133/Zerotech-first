# -*- coding: utf-8 -*-
"""hermes_auth 登录失败回退回归（2026-11-03 HermesUI 探活误报修复）。"""
from __future__ import annotations

import urllib.error


def test_auth_urlopen_falls_back_on_login_failure(monkeypatch):
    from agent_platform.legion_command import hermes_auth as H

    calls = []

    def boom(force=False):
        raise urllib.error.HTTPError("http://x", 401, "unauthorized", {}, None)

    monkeypatch.setattr(H, "enabled", lambda: True)
    monkeypatch.setattr(H, "_is_hermes", lambda url: True)
    monkeypatch.setattr(H, "_get_cookie", boom)
    monkeypatch.setattr(H, "safe_urlopen", lambda u, timeout=30: calls.append(u) or "OK")

    assert H.auth_urlopen("http://127.0.0.1:8787/health", timeout=3) == "OK"
    assert calls == ["http://127.0.0.1:8787/health"]
