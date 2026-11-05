# -*- coding: utf-8 -*-
"""JEV 告警卡并轨转发（FB-A）回归测试。

背景：飞书一个 app 的事件接收方式只能二选一（长连接 / HTTP 回调）。JEV 原先
自建第二个长连接消费者，和本体抢同一个 app 的唯一 WS，本体一重启就把它顶死，
且失效是静默的——只有人真点按钮才发现。并轨后由本体独占 WS，把 value 带
alert_id 的卡片动作转发到 JEV 本地接入（127.0.0.1:9106）。

本文件锁住三件事：转发条件、转发报文形状、回执话术。
不碰网络（urlopen 打桩）、不真发飞书消息（send_text 打桩）。
"""

from __future__ import annotations

import json
import sys
import types
import urllib.request
from pathlib import Path

import pytest

# 从仓库根跑 pytest 时 portal 不在 sys.path，显式补（与 conftest 同思路）
PORTAL_DIR = Path(__file__).resolve().parents[1]
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))

import feishu_bot_ws as bot  # noqa: E402

OP = "ou_test_operator"


def _fake_event(message_id="om_test_1", token="tk_test_1", value=None):
    return types.SimpleNamespace(
        action=types.SimpleNamespace(value=value or {}),
        token=token,
        context=types.SimpleNamespace(open_message_id=message_id),
    )


@pytest.fixture
def stubs(monkeypatch):
    """把回执与被转发的调用都记下来，顺便掐断真实网络。"""
    sent = []
    monkeypatch.setattr(bot, "send_text", lambda text, message_id=None: sent.append(text) or True)
    return sent


def test_card_ctx_extracts_message_id_and_token():
    ev = _fake_event("om_abc", "tk_abc")
    assert bot._card_ctx(ev) == {"message_id": "om_abc", "token": "tk_abc"}


def test_card_ctx_tolerates_missing_context():
    ev = types.SimpleNamespace(action=None, token="tk_only", context=None)
    assert bot._card_ctx(ev) == {"message_id": "", "token": "tk_only"}


def test_jev_card_is_forwarded_and_not_handled_locally(stubs, monkeypatch):
    seen = []
    monkeypatch.setattr(
        bot, "_forward_jev_card", lambda val, sender, ctx: seen.append((val, sender, ctx)) or (True, "")
    )
    val = {"source": "backup", "action": "ack", "alert_id": "BACKUP_A1"}
    ev = _fake_event("om_1", "tk_1", val)

    bot._handle_card(val, OP, bot._card_ctx(ev))

    assert len(seen) == 1, "JEV 卡片必须转发给 JEV 接入，不在本进程内解释"
    assert seen[0][0] == val and seen[0][1] == OP
    assert seen[0][2]["message_id"] == "om_1"
    assert stubs == ["✅ 已记录：ack"]


def test_forward_failure_gives_visible_receipt(stubs, monkeypatch):
    monkeypatch.setattr(bot, "_forward_jev_card", lambda *a: (False, "connection refused"))
    val = {"source": "backup", "action": "ignore", "alert_id": "BACKUP_A2"}

    bot._handle_card(val, OP, {"message_id": "om_2", "token": "tk_2"})

    assert stubs == ["❌ 未记录（JEV 接入不可达）：connection refused"], (
        "转发失败必须让点击者看到，不能静默吞掉（这正是并轨前的老毛病）"
    )


def test_forward_payload_contract(monkeypatch):
    """报文即契约：JEV 侧 _event_from_payload 按这套键取值。"""
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    ok, why = bot._forward_jev_card(
        {"source": "backup", "action": "ack", "alert_id": "BACKUP_A3"},
        OP,
        {"message_id": "om_3", "token": "tk_3"},
    )

    assert ok is True and why == ""
    assert captured["url"] == bot.JEV_CARD_ACK_URL
    assert captured["body"] == {
        "source": "backup",
        "action": "ack",
        "alert_id": "BACKUP_A3",
        "operator_id": OP,
        "message_id": "om_3",
        "token": "tk_3",
    }


def test_forward_returns_false_on_unreachable(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ok, why = bot._forward_jev_card({"action": "ack", "alert_id": "A4"}, OP, {})
    assert ok is False and "connection refused" in why


def test_ack_url_defaults_to_9106():
    """9106 是 JEV 的 HTTP 接入口；写回 9104 会打到 metrics 端点（返回的不是 JSON）。"""
    assert bot.JEV_CARD_ACK_URL.endswith(":9106/card_action")
