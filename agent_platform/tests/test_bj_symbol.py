# -*- coding: utf-8 -*-
"""北交所符号映射测试（2026-10-30 WO-INV-BJ-POOL）。"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "agent_platform" / "engine"
sys.path.insert(0, str(ENGINE))


def _load():
    spec = importlib.util.spec_from_file_location("engine_tools_bj_t", ENGINE / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_norm_symbol_bj():
    t = _load()
    assert t.norm_symbol("920378") == "bj920378"
    assert t.norm_symbol("920378.BJ") == "bj920378"
    assert t.norm_symbol("bj920378") == "bj920378"
    assert t.norm_symbol("830001") == "bj830001"
    assert t.norm_symbol("600519") == "sh600519"
    assert t.norm_symbol("000001") == "sz000001"


def test_run_shadow_has_pool_auto_hook():
    src = (REPO / "agent_platform" / "run_shadow.py").read_text(encoding="utf-8")
    assert "_pool_review_auto(today)" in src
    assert "POOL_REVIEW_AUTO" in src
