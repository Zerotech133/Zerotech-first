# -*- coding: utf-8 -*-
"""Static guard for jev-eval/start_jev_night.ps1 (K-ORG-20261029-JEVGATEFIX).

The on-demand gate once shipped with a corrupted path (Python \a/\t escapes turned
`C:\\aios\\tools\\jev_pending.py` into BEL/TAB chars), so the gate silently failed
and started a waiting night run. These assertions catch that class of bug.
"""
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "jev-eval" / "start_jev_night.ps1"


def _text() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def test_wrapper_is_ascii():
    raw = WRAPPER.read_bytes()
    assert all(b < 128 for b in raw), "start_jev_night.ps1 must stay ASCII"


def test_wrapper_gate_path_is_clean():
    t = _text()
    assert "C:\\\\aios\\\\tools\\\\jev_pending.py" in t or "C:\\aios\\tools\\jev_pending.py" in t
    assert "\x07" not in t, "BEL char found (corrupted \\a escape)"
    assert "\t" not in t, "TAB char found (corrupted \\t escape)"


def test_wrapper_has_on_demand_gate():
    t = _text()
    assert "JEV_NIGHT_MIN_BACKLOG" in t
    assert "SKIP night run" in t or "pending -lt $minBacklog" in t
    assert "jev_pending.py" in t

def test_wrapper_gate_is_fail_closed():
    t = _text()
    assert "fail-closed" in t
    assert "missing path" in t
    assert "pending output not integer" in t
    # 不再有「检查失败就照常跑」的旧行为
    assert "-> run as usual" not in t  # 注释里的历史说明不算

