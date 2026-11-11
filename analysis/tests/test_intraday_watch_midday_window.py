# -*- coding: utf-8 -*-
"""午间简报 11:30 窗口与 market_open 门解耦的回归测试（2026-11-10 MIDDAYFIX）。

背景：11:30 已过上午收盘，market_open=False；旧逻辑要求 market_open 才跑
maybe_midday_summary，导致首日 11:30 没发、只能等 13:00 补发。
本测试锁定：11:30–14:00 内即使 market_open=False，也允许构建/发送午间简报。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis import intraday_watch as iw


def _conf(routes_active: bool = True, **over) -> dict:
    conf = {
        "enabled": True,
        "midday_brief_enabled": True,
        "midday_time": "11:30",
        "portal_base": "http://127.0.0.1:8734",
        "push": {"kind": "watch:realtime", "receiver": "个人", "enabled": True,
                 "routes_active": routes_active,
                 "routes": {"customer": "个人", "ops": "量化群"}},
    }
    conf.update(over)
    return conf


def _now(hour: int = 11, minute: int = 31) -> datetime:
    return datetime(2026, 11, 10, hour, minute, 0, tzinfo=iw.CST)


def _quote(code: str = "600337.SH", name: str = "ST美克", pct: float = -0.85) -> dict:
    return {"code": code, "name": name, "price": 3.51, "pct": pct,
            "amount": 123450000.0, "vol_ratio": 2.8}


def test_midday_due_true_in_lunch_even_when_market_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(iw, "is_trading_day", lambda *a, **k: True)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    try:
        assert iw.market_open(now=_now(11, 31), cfg=_conf()) is False
        assert iw.midday_due(store, cfg=_conf(), now=_now(11, 31)) is True
    finally:
        store.close()


def test_midday_due_false_outside_window_or_done_or_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(iw, "is_trading_day", lambda *a, **k: True)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    try:
        assert iw.midday_due(store, cfg=_conf(), now=_now(11, 29)) is False
        assert iw.midday_due(store, cfg=_conf(), now=_now(14, 0)) is False
        assert iw.midday_due(store, cfg=_conf(routes_active=False), now=_now(11, 31)) is False
        store.set_kv("midday_done:2026-11-10", "2026-11-10 11:31:00")
        assert iw.midday_due(store, cfg=_conf(), now=_now(11, 31)) is False
    finally:
        store.close()


def test_midday_due_false_on_non_trading_day(monkeypatch, tmp_path):
    monkeypatch.setattr(iw, "is_trading_day", lambda *a, **k: False)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    try:
        assert iw.midday_due(store, cfg=_conf(), now=_now(11, 31)) is False
    finally:
        store.close()


def test_midday_summary_sends_at_1131_without_market_open(monkeypatch, tmp_path):
    """回归：不 patch market_open，11:31（午休）仍应发出午间简报。"""
    monkeypatch.setattr(iw, "is_trading_day", lambda *a, **k: True)
    conf = _conf()
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    sent = []
    try:
        def fake_push(payloads, cfg=None, receiver=None, timeout=10):
            sent.append(payloads[0]["receiver"])
            return [{"ok": True}]

        assert iw.market_open(now=_now(11, 31), cfg=conf) is False
        r = iw.maybe_midday_summary(store, [_quote()], ["600337.SH"], [],
                                    cfg=conf, now=_now(11, 31), push_fn=fake_push)
        assert r and r["ok"] is True
        assert sent == ["量化群"]
        assert store.get_kv("midday_done:2026-11-10")
    finally:
        store.close()
