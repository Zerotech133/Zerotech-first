# -*- coding: utf-8 -*-
"""PREFLIGHT 纯函数单测。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_night_preflight as pf  # noqa: E402


def test_window_detection():
    assert pf.night_window(datetime(2026, 10, 30, 22, 0))[0] is True
    assert pf.night_window(datetime(2026, 10, 31, 3, 0))[0] is True
    assert pf.night_window(datetime(2026, 10, 30, 21, 49))[0] is False
    assert pf.night_window(datetime(2026, 10, 30, 7, 1))[0] is False
    ok, detail = pf.night_window(datetime(2026, 10, 30, 21, 36))
    assert ok is False and "14 分钟" in detail


def test_summarize():
    checks = [{"name": "a", "ok": True, "detail": ""},
              {"name": "b", "ok": False, "detail": ""}]
    s = pf.summarize(checks)
    assert s["overall"] == "FAIL" and s["fails"] == ["b"]
    assert pf.summarize([{"name": "a", "ok": True, "detail": ""}])["overall"] == "PASS"


def test_cli_entry_present():
    import jev_night_preflight as _pf
    from pathlib import Path as _P
    code = _P(_pf.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in code
