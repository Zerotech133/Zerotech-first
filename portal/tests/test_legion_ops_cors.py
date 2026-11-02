# -*- coding: utf-8 -*-
"""UF-12：legion ops CORS 通配下线回归。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

import api_legion_ops  # noqa: E402


def test_allowed_origins_echoed():
    for origin in ("http://localhost:8080", "http://127.0.0.1:8080", "http://127.0.0.1:8734"):
        assert api_legion_ops.cors_origin(origin) == origin


def test_unknown_origin_denied():
    for origin in ("http://evil.example", "https://127.0.0.1:8734", None, ""):
        assert api_legion_ops.cors_origin(origin) is None


def test_no_wildcard_header_in_source():
    src = (ROOT / "portal" / "api_legion_ops.py").read_text(encoding="utf-8")
    assert 'Access-Control-Allow-Origin", "*"' not in src
    assert "CORS_ALLOWED_ORIGINS" in src
