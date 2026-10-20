# -*- coding: utf-8 -*-
"""订阅管理员指令健壮性单测（飞书中台 P3.6-lite · K-B 工位）。

派工单允许「追加 test_sub_admin_cmds.py 或新建」——该文件本轮读取被
审批拦截，故新建本文件，零碰既有测试。

离线纪律：零网络、不真发飞书、不启服务、不重启在役 bot。
覆盖：
  · 重复开通幂等（指令层查重）：已有生效同档位 → 诚实回执不重复插行；
    过期同档位/不同档位 → 正常开通；
  · 首开正常回执（含掩码与 message_id 贯通）；
  · 掩码 mask_open_id 四档复核；
  · ValueError 路径复核（档位不存在 / days<=0 → ❌ 回执不崩）；
  · 非管理员拒绝；
  · 回执通道静态断言：send_text 带 message_id 走 Reply API
    （im.v1.message.reply），不经 8097 推送中台闸/静默窗。
"""

import inspect
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import feishu_bot_ws  # noqa: E402
import subscription_store as ss  # noqa: E402
import tune_gate  # noqa: E402

ADMIN_OID = "ou_admin_kB"
MID = "om_test_receipt"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """tmp DB + 管理员白名单注入 + send_text 收集器（零真发）。"""
    db = tmp_path / "sub_p36.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(db))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    monkeypatch.setattr(tune_gate, "_admins",
                        lambda: {"open_ids": [ADMIN_OID]})
    sent = []

    def fake_send_text(text, message_id=None):
        sent.append((text, message_id))
        return True

    monkeypatch.setattr(feishu_bot_ws, "send_text", fake_send_text)
    ss._connect(create=True)
    return {"db": db, "sent": sent}


def _sub_rows(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT plan_id, start, end, status FROM subscription"
        " ORDER BY end").fetchall()
    conn.close()
    return rows


def _grant_direct(open_id, plan_id="basic_monthly", days=30):
    """测试内直接插生效订阅行（沿 test_subscription_store 判例）。"""
    start = datetime(2026, 10, 20, 8, 0, 0)
    end = start + timedelta(days=days)
    with ss._connect(create=True) as conn:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
            " end, status) VALUES (?,?,?,?,?)",
            (open_id, plan_id, start.strftime("%Y-%m-%d %H:%M:%S"),
             end.strftime("%Y-%m-%d %H:%M:%S"), "active"))
        conn.commit()


# ── 幂等查重 ─────────────────────────────────────────────────

def test_01_first_grant_ok(env):
    """首开：无既有订阅 → ✅ 回执（掩码+档位+到期），message_id 贯通。"""
    feishu_bot_ws.handle_cmd("开通 ou_new_123456 basic_monthly",
                             MID, ADMIN_OID)
    assert len(env["sent"]) == 1
    text, mid = env["sent"][0]
    assert text.startswith("✅ 已开通") and "basic_monthly" in text
    assert "ou_new_1**" in text  # 掩码：>8 位保留前 8 + **
    assert mid == MID
    rows = _sub_rows(env["db"])
    assert len(rows) == 1 and rows[0][3] == "active"


def test_02_duplicate_same_plan_blocked(env):
    """重复开通同档位（active 覆盖当前）→ ℹ️ 提示回执，不重复插行。"""
    ss.upsert_subscriber("ou_dup")
    _grant_direct("ou_dup")
    feishu_bot_ws.handle_cmd("开通 ou_dup basic_monthly", MID, ADMIN_OID)
    assert len(env["sent"]) == 1
    text, _ = env["sent"][0]
    assert "已有生效" in text and "未重复开通" in text
    assert "basic_monthly" in text and "ou_d**" in text  # 6 位→前4+**
    assert len(_sub_rows(env["db"])) == 1  # 零新增行


def test_03_expired_same_plan_regrant_ok(env):
    """同档位但已过期（不覆盖当前时刻）→ 不拦，正常开通。"""
    with ss._connect(create=True) as conn:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
            " end, status) VALUES (?,?,?,?,?)",
            ("ou_exp", "basic_monthly", "2026-09-01 08:00:00",
             "2026-10-01 08:00:00", "active"))
        conn.commit()
    feishu_bot_ws.handle_cmd("开通 ou_exp basic_monthly", MID, ADMIN_OID)
    text, _ = env["sent"][0]
    assert text.startswith("✅ 已开通")
    assert len(_sub_rows(env["db"])) == 2


def test_04_other_active_plan_allowed(env):
    """同 open_id 已有生效 basic_monthly，开通另一档位 → 正常放行。"""
    ss.upsert_subscriber("ou_mix")
    _grant_direct("ou_mix", plan_id="basic_monthly")
    feishu_bot_ws.handle_cmd("开通 ou_mix internal", MID, ADMIN_OID)
    text, _ = env["sent"][0]
    assert text.startswith("✅ 已开通") and "internal" in text
    rows = _sub_rows(env["db"])
    assert len(rows) == 2 and {r[0] for r in rows} == {
        "basic_monthly", "internal"}


# ── 掩码复核 ─────────────────────────────────────────────────

def test_05_mask_open_id_review():
    """掩码四档：>8 保留前8 / 5-8 前4 / 1-4 前2 / 空 → **。"""
    assert feishu_bot_ws.mask_open_id("ou_abcdefgh123") == "ou_abcde**"
    assert feishu_bot_ws.mask_open_id("ou_dup") == "ou_d**"
    assert feishu_bot_ws.mask_open_id("ab") == "ab**"
    assert feishu_bot_ws.mask_open_id("") == "**"
    assert feishu_bot_ws.mask_open_id(None) == "**"


# ── ValueError 路径复核 ──────────────────────────────────────

def test_06_valueerror_receipts(env):
    """档位不存在 / days<=0 → ❌ 回执接住，不崩不裸栈。"""
    feishu_bot_ws.handle_cmd("开通 ou_ve no_such_plan", MID, ADMIN_OID)
    text, _ = env["sent"][0]
    assert text.startswith("❌") and "no_such_plan" in text
    feishu_bot_ws.handle_cmd("开通 ou_ve basic_monthly 0", MID, ADMIN_OID)
    text2, _ = env["sent"][1]
    assert text2.startswith("❌") and "days" in text2
    assert _sub_rows(env["db"]) == []  # 两次均未落行


# ── 权限闸 ───────────────────────────────────────────────────

def test_07_non_admin_denied(env):
    """非管理员 → 「无权限。」，零回执冗余、零落行。"""
    feishu_bot_ws.handle_cmd("开通 ou_victim basic_monthly",
                             MID, "ou_nobody")
    assert env["sent"] == [("无权限。", MID)]
    assert _sub_rows(env["db"]) == []


# ── 回执通道静态断言 ─────────────────────────────────────────

def test_08_receipt_channel_is_reply_api():
    """回执通道：send_text 带 message_id 走 Reply API（im.v1.message.
    reply），不走 OpenAPI 直发（send_feishu 仅 message_id 为空分支）——
    订阅指令回执不经 8097 推送中台闸/静默窗的代码级佐证。"""
    src = inspect.getsource(feishu_bot_ws.send_text)
    assert "ReplyMessageRequest" in src
    assert "im.v1.message.reply" in src
    assert "from send_feishu import send_text" in src
    assert src.index("if not message_id:") < src.index(
        "from send_feishu import send_text")  # 直发仅在无 message_id 分支
