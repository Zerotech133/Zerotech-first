# -*- coding: utf-8 -*-
"""pending 不推导水平 + levels 缓存 TTL 测试（WO-AUDIT2-FIX-20261031）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO))


def test_pending_not_leveled():
    import d1_data
    assert d1_data._should_derive_levels({"decision_status": "done"}, 10.0) is True
    assert d1_data._should_derive_levels({"decision_status": "pending"}, 10.0) is False
    assert d1_data._should_derive_levels({"decision_status": "done"}, None) is False


def test_technical_levels_cache_ttl(monkeypatch):
    from agent_platform import technical_levels as tl
    calls = {"n": 0}

    def fake_load(code, limit=80):
        calls["n"] += 1
        return [["d", 10, 10, 10.1, 9.9, 1]] * 40

    monkeypatch.setattr(tl, "load_bars", fake_load)
    tl._CACHE.clear()
    tl._CACHE_TS.clear()
    first = tl.derive("600519", price=10, can_buy=False)
    second = tl.derive("600519", price=10, can_buy=False)
    assert first and second == first
    assert calls["n"] == 1
