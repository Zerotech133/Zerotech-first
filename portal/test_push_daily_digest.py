# -*- coding: utf-8 -*-
"""OS-FIX-W4CLOSE：push_daily_digest 统一发送 + 静默窗回归。

背景：旧实现绕过 8097 直发 OpenAPI——失败进 push_flow、成功不记账，
导致 W4 24h 丢失误判（2026-10-26 02:19 两条 dead_letter）。本测试锁定：
1) 22:00–07:00 静默窗非 critical 不发（不产生死信）；
2) 发送走 feishu_client.send_msg_tracked(kind=auto:push_digest, via_hub=True)；
3) 失败 fail-closed（不回落直发）。
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import push_daily_digest as pdd  # noqa: E402
import feishu_client  # noqa: E402


def _patch_silent(monkeypatch, silent):
    monkeypatch.setattr(
        pdd,
        "_in_silent_window",
        lambda now=None: (silent, "silent_window(02:19)" if silent else ""),
    )


def _patch_union(monkeypatch):
    monkeypatch.setattr(
        pdd.authorization, "person", lambda name: {"union_id": "on_test_union"}
    )


def test_silent_window_blocks_noncritical(monkeypatch):
    _patch_silent(monkeypatch, True)
    _patch_union(monkeypatch)
    calls = []
    monkeypatch.setattr(
        feishu_client, "send_msg_tracked", lambda *a, **k: calls.append((a, k))
    )
    ok, why = pdd.send({"config": {}})
    assert ok is False
    assert "silent_window" in why
    assert calls == [], "静默窗内不得发起发送"


def test_silent_window_helper_hours():
    assert pdd._in_silent_window(datetime.datetime(2026, 10, 26, 23, 0))[0] is True
    assert pdd._in_silent_window(datetime.datetime(2026, 10, 26, 6, 59))[0] is True
    assert pdd._in_silent_window(datetime.datetime(2026, 10, 26, 7, 0))[0] is False
    assert pdd._in_silent_window(datetime.datetime(2026, 10, 26, 12, 0))[0] is False


def test_unified_send_success_records_flow(monkeypatch):
    _patch_silent(monkeypatch, False)
    _patch_union(monkeypatch)
    seen = {}

    def fake_send(content, msg_type, **kw):
        seen["content"] = content
        seen["msg_type"] = msg_type
        seen.update(kw)
        return True, "ok", "om_123"

    monkeypatch.setattr(feishu_client, "send_msg_tracked", fake_send)
    ok, why = pdd.send({"schema": "2.0", "body": {"elements": []}})
    assert ok is True
    assert "om_123" in why
    assert isinstance(seen["content"], str), "dict 必须先序列化，避免 OpenAPI 9499"
    assert '"schema": "2.0"' in seen["content"]
    assert seen["kind"] == "auto:push_digest"
    assert seen["via_hub"] is True
    assert seen["sender_role"] == "zb.ops.notify"
    assert seen["receive_id"] == "on_test_union"
    assert seen["receive_id_type"] == "union_id"
    assert seen["critical"] is False


def test_silent_window_critical_allowed(monkeypatch):
    _patch_silent(monkeypatch, True)
    _patch_union(monkeypatch)
    monkeypatch.setattr(
        feishu_client, "send_msg_tracked", lambda *a, **k: (True, "ok", "om_crit")
    )
    ok, _ = pdd.send({"config": {}}, critical=True)
    assert ok is True


def test_failure_fail_closed(monkeypatch):
    _patch_silent(monkeypatch, False)
    _patch_union(monkeypatch)
    monkeypatch.setattr(
        feishu_client, "send_msg_tracked", lambda *a, **k: (False, "hub HTTP 403", None)
    )
    ok, why = pdd.send({"config": {}})
    assert ok is False
    assert "403" in why


def test_missing_union_id(monkeypatch):
    _patch_silent(monkeypatch, False)
    monkeypatch.setattr(pdd.authorization, "person", lambda name: {})
    ok, why = pdd.send({"config": {}})
    assert ok is False and "union_id" in why
