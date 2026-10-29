# -*- coding: utf-8 -*-
"""make_context 空K线 fail-visible 测试（2026-10-30 WO-INV-BJ-EMPTY）。"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "agent_platform" / "engine"
sys.path.insert(0, str(ENGINE))


def _load_tools():
    spec = importlib.util.spec_from_file_location("engine_tools_t", ENGINE / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empty_bars_raises_valueerror(monkeypatch):
    t = _load_tools()
    monkeypatch.setattr(t, "kline_daily", lambda code, n: [])
    monkeypatch.setattr(t, "quote_snapshot", lambda code: {})
    with pytest.raises(ValueError, match="KLINE_EMPTY"):
        t.make_context("920378")
