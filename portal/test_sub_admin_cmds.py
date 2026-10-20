# -*- coding: utf-8 -*-
"""飞书中台 P3-lite 订阅管理员指令族单测（H-A 工位）。

离线纪律：零网络、不真发飞书、不启任何服务、不重启在役 bot、不碰真实
subscription.sqlite3（store 全桩测，经 monkeypatch 换函数）。
覆盖：
  · mask_open_id 纯函数：8 位样式 / 短 id 降级 / 空 None；
  · 管理员闸：非管理员拒绝且回执"无权限"、不触 store（桩测断言零调用）；
  · 开通：成功路径桩测 store（upsert+grant 皆被调、回执含掩码+档位+到期）、
    缺参 usage、ValueError（档位不存在 / 天数非数）回 ❌；
  · 订阅查询：摘要掩码展示 / store error 回执 / 缺参 usage；
  · 订阅巡查：expire_sweep+list_subscribers 概要 / store error 回执；
  · HELP_MD 含管理员段（标注"仅管理员"）；
  · handle_cmd 源码静态断言：新分支插在"帮助"分支之前、存量分支
    （止盈单/审批/兜底 inbound）零回归。
风格照 portal/test_bot_ws_backoff.py。
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pytest  # noqa: E402

import feishu_bot_ws  # noqa: E402
import subscription_store  # noqa: E402
import tune_gate  # noqa: E402

_SRC = inspect.getsource(feishu_bot_ws)

ADMIN = "ou_admin0001"
STRANGER = "ou_stranger99"
MID = "M_test"


@pytest.fixture()
def captured(monkeypatch):
    """send_text 回执捕获 + store/tune_gate 桩位（默认管理员放行）。"""
    sent = []
    monkeypatch.setattr(feishu_bot_ws, "send_text",
                        lambda text, mid=None: sent.append(text) or True)
    calls = {"upsert": [], "grant": [], "sweep": 0, "summary": [],
             "list": 0}
    monkeypatch.setattr(subscription_store, "upsert_subscriber",
                        lambda oid, display_name="":
                        calls["upsert"].append(oid) or oid)
    monkeypatch.setattr(subscription_store, "grant_subscription",
                        lambda oid, plan, days:
                        calls["grant"].append((oid, plan, days))
                        or {"open_id": oid, "plan_id": plan,
                            "start": "2026-10-20 00:00:00",
                            "end": "2026-11-19 00:00:00",
                            "status": "active"})
    monkeypatch.setattr(subscription_store, "expire_sweep",
                        lambda now=None: calls.__setitem__(
                            "sweep", calls["sweep"] + 1) or 2)
    monkeypatch.setattr(subscription_store, "get_subscriber_summary",
                        lambda oid, now=None:
                        calls["summary"].append(oid)
                        or {"open_id": oid, "display_name": "客户甲",
                            "subscriptions": [
                                {"plan_id": "basic_monthly",
                                 "start": "2026-10-20 00:00:00",
                                 "end": "2026-11-19 00:00:00",
                                 "status": "active"}],
                            "today_used_by_prefix": {"news": 3},
                            "daily_quota_by_plan": {"basic_monthly": 20},
                            "plan_name_by_id": {"basic_monthly": "基础月度档"},
                            "reason": "subscription_active",
                            "generated_at": "2026-10-20 00:00:00"})
    monkeypatch.setattr(subscription_store, "list_subscribers",
                        lambda limit=50, now=None:
                        calls.__setitem__("list", calls["list"] + 1)
                        or {"ok": True, "count": 2, "subscribers": [
                            {"open_id": "ou_subscriber01", "display_name":
                             "甲", "plan_id": "basic_monthly",
                             "status": "active",
                             "start": "2026-10-20 00:00:00",
                             "end": "2026-11-19 00:00:00",
                             "daily_quota": 20, "used_today": 3},
                            {"open_id": "ou_subscriber02", "display_name":
                             "乙", "plan_id": None, "status": None,
                             "start": None, "end": None,
                             "daily_quota": None, "used_today": 0}]})
    monkeypatch.setattr(tune_gate, "is_admin", lambda oid: oid == ADMIN)
    return sent, calls


def _cmd(text, open_id=ADMIN):
    feishu_bot_ws.handle_cmd(text, MID, open_id=open_id)


# ── mask_open_id 纯函数 ──────────────────────────────────────

def test_mask_keeps_first_8_with_stars():
    """长 id 保留前 8 位 + **（ou_abc12** 样式）。"""
    assert feishu_bot_ws.mask_open_id("ou_abc1234567890xyz") == "ou_abc12**"


def test_mask_short_and_empty_degrade():
    """短 id 降级保留前 4/前 2 位；空与 None 回 **（不抛）。"""
    m = feishu_bot_ws.mask_open_id
    assert m("ou_abc1") == "ou_a**"      # len 6 → 前 4
    assert m("ouab") == "ou**"           # len 4 → 前 2
    assert m("") == "**"
    assert m(None) == "**"
    assert m("ou_xxxxxxxxxxxxxxxx") == "ou_xxxxx**"  # [:8] 共 8 字符


# ── 管理员闸 ─────────────────────────────────────────────────

def test_admin_gate_denies_without_touching_store(captured):
    """非管理员：一律"无权限"拒绝，store 五个函数零调用（不透漏指令）。"""
    sent, calls = captured
    for text in ("开通 ou_x basic_monthly", "订阅查询 ou_x", "订阅巡查"):
        _cmd(text, open_id=STRANGER)
    assert all("无权限" in s for s in sent)
    assert calls == {"upsert": [], "grant": [], "sweep": 0, "summary": [],
                     "list": 0}


def test_admin_gate_usage_hidden_from_stranger(captured):
    """非管理员缺参同样只回"无权限"——usage 提示在闸后，不泄露用法。"""
    sent, _ = captured
    _cmd("开通", open_id=STRANGER)
    assert sent == ["无权限。"]


# ── 开通 ─────────────────────────────────────────────────────

def test_kaitong_success_hits_store_and_masks_id(captured):
    """成功路径：upsert+grant 皆被调，回执含掩码 id+档位+到期日。"""
    sent, calls = captured
    _cmd("开通 ou_abc123456789 basic_monthly 15")
    assert calls["upsert"] == ["ou_abc123456789"]
    assert calls["grant"] == [("ou_abc123456789", "basic_monthly", 15)]
    assert len(sent) == 1
    assert sent[0].startswith("✅")
    assert "ou_abc12**" in sent[0]
    assert "basic_monthly" in sent[0]
    assert "2026-11-19" in sent[0]


def test_kaitong_default_days_30(captured):
    """缺省天数 → grant 收到 30。"""
    sent, calls = captured
    _cmd("开通 ou_abc123456789 basic_monthly")
    assert calls["grant"] == [("ou_abc123456789", "basic_monthly", 30)]


def test_kaitong_missing_args_usage(captured):
    """管理员缺参 → usage 提示，store 零调用。"""
    sent, calls = captured
    _cmd("开通")
    assert "用法" in sent[0]
    assert calls["upsert"] == [] and calls["grant"] == []


def test_kaitong_valueerror_plan_receipt(captured, monkeypatch):
    """档位不存在（store 抛 ValueError）→ ❌ 回执透传理由。"""
    sent, _ = captured
    monkeypatch.setattr(subscription_store, "grant_subscription",
                        lambda oid, plan, days: (_ for _ in ()).throw(
                            ValueError("plan 不存在: nope")))
    _cmd("开通 ou_abc123456789 nope")
    assert sent[0].startswith("❌")
    assert "plan 不存在" in sent[0]


def test_kaitong_valueerror_bad_days_receipt(captured):
    """天数非数字 → int() ValueError → ❌ 回执，store 不落订阅。"""
    sent, calls = captured
    _cmd("开通 ou_abc123456789 basic_monthly abc")
    assert sent[0].startswith("❌")
    assert calls["upsert"] == ["ou_abc123456789"]  # upsert 先行
    assert calls["grant"] == []                    # grant 未触


# ── 订阅查询 ─────────────────────────────────────────────────

def test_chaxun_summary_masked(captured):
    """摘要展示：掩码 id + display_name + reason + 订阅行。"""
    sent, calls = captured
    _cmd("订阅查询 ou_abc123456789")
    assert calls["summary"] == ["ou_abc123456789"]
    body = "\n".join(sent)
    assert "ou_abc12**" in body
    assert "客户甲" in body
    assert "subscription_active" in body
    assert "basic_monthly" in body


def test_chaxun_store_error_receipt(captured, monkeypatch):
    """store error（closed on failure 字段）→ ❌ 回执不抛。"""
    sent, _ = captured
    monkeypatch.setattr(subscription_store, "get_subscriber_summary",
                        lambda oid, now=None: {"error": "store_unavailable"})
    _cmd("订阅查询 ou_abc123456789")
    assert sent[0].startswith("❌") and "store_unavailable" in sent[0]


def test_chaxun_missing_args_usage(captured):
    """缺参 → usage，store 零调用。"""
    sent, calls = captured
    _cmd("订阅查询")
    assert "用法" in sent[0]
    assert calls["summary"] == []


# ── 订阅巡查 ─────────────────────────────────────────────────

def test_xuncha_sweep_then_list_summary(captured):
    """巡查：先 expire_sweep 后 list_subscribers，概要含清扫数+人数+掩码行。"""
    sent, calls = captured
    _cmd("订阅巡查")
    assert calls["sweep"] == 1 and calls["list"] == 1
    body = "\n".join(sent)
    assert "清扫过期 2 行" in body
    assert "订阅者 2 人" in body
    assert "ou_subsc**" in body
    assert "无档位" in body  # 无订阅行订阅者诚实展示


def test_xuncha_store_error_receipt(captured, monkeypatch):
    """list_subscribers closed on failure → ❌ 回执（含已清扫数）。"""
    sent, _ = captured
    monkeypatch.setattr(subscription_store, "list_subscribers",
                        lambda limit=50, now=None:
                        {"ok": False, "error": "store_unavailable"})
    _cmd("订阅巡查")
    assert sent[0].startswith("❌") and "store_unavailable" in sent[0]


# ── HELP_MD 与静态零回归 ─────────────────────────────────────

def test_help_md_contains_admin_section():
    """帮助含管理员指令段，标注"仅管理员"，三条指令齐全。"""
    help_md = feishu_bot_ws.HELP_MD
    assert "管理员指令（仅管理员）" in help_md
    for cmd in ("开通 <open_id> <档位> [天数]", "订阅查询 <open_id>",
                "订阅巡查"):
        assert cmd in help_md


def test_src_new_branch_before_help_no_regression():
    """静态零回归：新分支插在"帮助"分支之前；存量分支原样在位。"""
    i_open = _SRC.index('low.startswith(("开通", "订阅查询", "订阅巡查"))')
    i_help = _SRC.index('low.startswith("帮助")')
    assert i_open < i_help
    # 存量分支零回归
    for marker in ('low.startswith("止盈单")', 'low.startswith("审批")',
                   "inbound_gateway.handle_inbound", "未识别指令",
                   "tune_gate.is_admin"):
        assert marker in _SRC
    # 拒绝文案不透漏指令存在
    assert 'send_text("无权限。", message_id)' in _SRC
