# -*- coding: utf-8 -*-
"""军团写接口浏览器上下文守卫单测（2026-10-30 P0 安全加固）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

from legion_write_guard import PORTAL_WRITE_ORIGINS, write_context_allowed  # noqa: E402


def test_same_origin_portal_allowed():
    assert write_context_allowed(origin="http://127.0.0.1:8734")
    assert write_context_allowed(origin="http://localhost:8734")
    assert write_context_allowed(origin="http://127.0.0.1:8080")
    assert write_context_allowed(origin="http://127.0.0.1:8734/")  # 去尾斜杠


def test_cross_site_origin_rejected():
    assert not write_context_allowed(origin="http://evil.example")
    assert not write_context_allowed(origin="http://127.0.0.1:9999")
    assert not write_context_allowed(origin="http://127.0.0.1:5175")


def test_non_browser_request_passes_to_ip_token_judge():
    assert write_context_allowed()  # curl / 服务端脚本
    assert write_context_allowed(origin="", sec_fetch_site="", referer="")


def test_sec_fetch_site_rules():
    assert write_context_allowed(sec_fetch_site="same-origin")
    assert write_context_allowed(sec_fetch_site="none")
    assert not write_context_allowed(sec_fetch_site="cross-site")


def test_referer_fallback():
    assert write_context_allowed(referer="http://127.0.0.1:8734/legion")
    assert not write_context_allowed(referer="http://evil.example/x")


def test_allowlist_is_loopback_only():
    for o in PORTAL_WRITE_ORIGINS:
        assert o.startswith(("http://127.0.0.1", "http://localhost")), o
