# -*- coding: utf-8 -*-
"""bot 指令族「撤销 <open_id> <档位>」桩测（P3.13-lite ② 方案 a，2026-10-21）。

只桩测指令路由与回执文案，store 真实契约由 test_revoke_subscription.py 承担。
参照 test_sub_admin_cmds.py 的 capture fixture 形态（判例风格）。
"""

import inspect
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import feishu_bot_ws  # noqa: E402
import subscription_store  # noqa: E402
import tune_gate  # noqa: E402

_SRC = inspect.getsource(feishu_bot_ws)

ADMIN = "ou_admin0001"
MID = "M_test"


@pytest.fixture()
def captured(monkeypatch):
    """send_text 回执捕获 + revoke 桩位（is_admin 放行，判例同 sub_admin_cmds）。"""
    sent = []
    monkeypatch.setattr(
        feishu_bot_ws, "send_text", lambda text, mid=None: sent.append(text) or True
    )
    monkeypatch.setattr(tune_gate, "is_admin", lambda oid: oid == ADMIN)
    calls = {"revoke": []}

    def _fake_revoke(oid, plan, now=None):
        calls["revoke"].append((oid, plan))
        return 1

    monkeypatch.setattr(subscription_store, "revoke_subscription", _fake_revoke)
    return {"sent": sent, "calls": calls}


def _last_text(cap):
    assert cap["sent"], "expected a reply, got none"
    return cap["sent"][-1]


def test_01_revoke_routes_to_store(captured):
    """撤销指令调 store 且回执含掩码 open_id 与留痕说明。"""
    feishu_bot_ws.handle_cmd("撤销 ou_abc123 basic_monthly", MID, open_id=ADMIN)
    assert captured["calls"]["revoke"] == [("ou_abc123", "basic_monthly")]
    text = _last_text(captured)
    assert "已撤销" in text and "revoked" in text
    # 掩码口径：不得回显明文 open_id
    assert "ou_abc123" not in text


def test_02_revoke_valueerror_honest_reply(captured, monkeypatch):
    """无生效行/档位不存在 → ValueError 转诚实回执，不臆造。"""

    def _raise(oid, plan, now=None):
        raise ValueError("无生效订阅行：ou_x basic_monthly")

    monkeypatch.setattr(subscription_store, "revoke_subscription", _raise)
    feishu_bot_ws.handle_cmd("撤销 ou_x basic_monthly", MID, open_id=ADMIN)
    text = _last_text(captured)
    assert text.startswith("❌") and "无生效订阅行" in text


def test_03_revoke_missing_args_usage(captured):
    """缺参 → 用法回执，且不触达 store。"""
    feishu_bot_ws.handle_cmd("撤销 ou_only_id", MID, open_id=ADMIN)
    assert captured["calls"]["revoke"] == []
    assert "用法：撤销" in _last_text(captured)


def test_04_help_mentions_revoke(captured):
    """帮助文案含撤销条目。"""
    feishu_bot_ws.handle_cmd("帮助", MID, open_id=ADMIN)
    text = "\n".join(captured["sent"])
    assert "撤销 <open_id>" in text


def test_05_revoke_prefix_not_confused_with_other_cmds(captured):
    """「撤销」前缀路由进撤销分支（与卡片按钮文案无碰撞，文本前缀独占）。"""
    feishu_bot_ws.handle_cmd("撤销 ou_p1 basic_monthly", MID, open_id=ADMIN)
    assert captured["calls"]["revoke"] == [("ou_p1", "basic_monthly")]


def test_06_revoke_signature_compat():
    """store.revoke_subscription 签名向后兼容（oid, plan, now=None）。"""
    src = inspect.getsource(subscription_store)
    assert "def revoke_subscription(open_id, plan_id, now=None)" in src
