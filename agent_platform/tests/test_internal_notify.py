# -*- coding: utf-8 -*-
"""内部通知适配器：统一投递到 inbox_gateway。"""
from __future__ import annotations

import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from gateway import inbox_gateway, internal_notify  # noqa: E402


def test_emit_lands_in_inbox_and_dedupes(tmp_path, monkeypatch):
    inbox_gateway.set_store(tmp_path / "bridge")
    env = internal_notify.emit(
        "A0", "[A0跑板] DATA1 需决策", "卡点：TDX 58h\n需决策：刷新盘点",
        kind="question", source="hq-daily", link="deliverables/x.json", actor="DATA1")
    assert env["ok"] is True
    mid = env["data"]["id"]
    rec = inbox_gateway.receipt(mid)["data"]["record"]
    assert rec["kind"] == "question" and rec["source"] == "hq-daily"
    assert rec["payload"]["target"] == "A0"
    assert rec["payload"]["schema"] == internal_notify.SCHEMA
    assert "TDX 58h" in rec["payload"]["body"]

    again = internal_notify.emit(
        "A0", "[A0跑板] DATA1 需决策", "卡点：TDX 58h\n需决策：刷新盘点",
        kind="question", source="hq-daily", link="deliverables/x.json", actor="DATA1")
    assert again["ok"] is True and again["data"]["id"] == mid


def test_emit_rejects_bad_target_and_title(tmp_path):
    inbox_gateway.set_store(tmp_path / "bridge")
    assert internal_notify.emit("bad target!", "t")["ok"] is False
    assert internal_notify.emit("A0", "")["ok"] is False
