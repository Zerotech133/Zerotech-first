# -*- coding: utf-8 -*-
"""L-B · 订阅到期提醒单测（P3.7-lite 2026-10-20，离线纯函数）。

纪律：零网络、零 IO、不真发飞书、不启服务、不连账本库（入参全部构造）。
覆盖：T-3/T-1 命中、T-10 不命中、同日同 stage 去重、已过期不提醒、
不同 plan 互不干扰、正文不含金额与明文 open_id、解析失败显式计数、
store 不可用不装空、纯日期/带时刻/带时区解析、输入零副作用、
subscription_expiring 模板色彩与标题。
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_templates  # noqa: E402
import renewal_reminder as rr  # noqa: E402
from card_templates import apply_card_template  # noqa: E402
from renewal_reminder import (build_reminder_text, due_for_reminder,  # noqa: E402
                              mask_open_id, plan_reminders)

# 固定"现在"：2026-11-16 10:00:00（本地时区口径，避免测试随运行时刻漂移）
NOW = "2026-11-16 10:00:00"

_MONEY_TOKENS = ("元", "¥", "￥", "价格", "金额", "收费", "费用")


def _row(open_id="ou_alice001", plan_id="basic_monthly",
         end="2026-11-19 10:00:00", status="active", name="Alice"):
    """构造 list_subscribers() 同构的一行。"""
    return {
        "open_id": open_id,
        "display_name": name,
        "plan_id": plan_id,
        "status": status,
        "start": "2026-10-20 10:00:00",
        "end": end,
        "daily_quota": 20,
        "used_today": 0,
    }


# 1. T-3 命中

def test_t3_hit():
    items = due_for_reminder([_row()], now=NOW)
    assert len(items) == 1
    assert items[0]["open_id"] == "ou_alice001"
    assert items[0]["plan_id"] == "basic_monthly"
    assert items[0]["days_left"] == 3
    assert items[0]["stage"] == "t3"
    assert items[0]["end"] == "2026-11-19 10:00:00"


# 2. T-1 命中

def test_t1_hit():
    items = due_for_reminder([_row(end="2026-11-17 10:00:00")], now=NOW)
    assert len(items) == 1
    assert items[0]["days_left"] == 1
    assert items[0]["stage"] == "t1"


# 3. T-10 不命中（不在档位内，也不该被静默丢掉——计入 not_due）

def test_t10_not_due():
    report = plan_reminders([_row(end="2026-11-26 10:00:00")], now=NOW)
    assert report["items"] == []
    assert report["count"] == 0
    assert report["skipped"]["not_due"] == 1
    assert report["ok"] is True
    assert report["error"] is None


# 4. 同日同 stage 去重（重复行只提醒一次）

def test_same_day_same_stage_dedup():
    rows = [_row(), _row(), _row(name="Alice-dup")]
    report = plan_reminders(rows, now=NOW)
    assert len(report["items"]) == 1
    assert report["skipped"]["duplicate"] == 2
    # 去重键含 plan_id：同人不同 plan 不受影响（见下一例）


# 5. 已过期不提醒（status=expired 与 status=active 但 end 已过，都不提醒）

def test_expired_never_remind():
    rows = [
        _row(open_id="ou_bob0002", end="2026-11-19 10:00:00", status="expired"),
        _row(open_id="ou_carol003", end="2026-11-15 10:00:00", status="active"),
        _row(open_id="ou_carol003", end="2026-11-15 23:00:00", status="active"),
    ]
    report = plan_reminders(rows, now=NOW)
    assert report["items"] == []
    assert report["skipped"]["not_active"] == 1
    assert report["skipped"]["expired"] == 2


# 6. 不同 plan 互不干扰（同一 open_id 两条有效订阅各自成项）

def test_different_plans_independent():
    rows = [
        _row(plan_id="basic_monthly", end="2026-11-19 10:00:00"),
        _row(plan_id="pro_monthly", end="2026-11-19 23:00:00"),
        # 另一个 plan 已过期，不影响上面两条
        _row(plan_id="legacy_monthly", end="2026-11-10 10:00:00"),
    ]
    items = due_for_reminder(rows, now=NOW)
    assert len(items) == 2
    assert {it["plan_id"] for it in items} == {"basic_monthly", "pro_monthly"}
    assert all(it["stage"] == "t3" for it in items)


# 7. 正文不含金额、不含明文 open_id

def test_text_has_no_amount_and_no_plain_open_id():
    item = due_for_reminder([_row()], now=NOW)[0]
    text = build_reminder_text(item)
    for token in _MONEY_TOKENS:
        assert token not in text, "正文不得含金额口径: %s" % token
    assert "ou_alice001" not in text          # 明文 open_id 不得出现
    assert mask_open_id("ou_alice001") in text  # 用掩码标识
    assert "3" in text and "2026-11-19" in text
    assert "续费" in text and "联系管理员开通" in text


def test_mask_open_id_shape():
    assert mask_open_id("ou_alice001") == "ou_***001"
    assert mask_open_id("short") == "***"
    assert mask_open_id("") == ""


# 8. subscription_expiring 已注册且色彩与标题正确

def test_subscription_expiring_registered():
    # 收编口（2026-10-20 13:48）：已按 FC-C 快照同步扩充判例并入主表，
    # 与其余全部 kind 同表存放，不再有独立追加表。
    assert "subscription_expiring" in card_templates.KIND_CARD_TEMPLATES
    out, mt = apply_card_template("subscription_expiring",
                                  {"text": "还剩 3 天"}, "interactive")
    assert mt == "interactive"
    assert out["header"]["template"] == "orange"
    assert out["header"]["title"]["content"] == "⏳ 订阅即将到期"
    assert out["header"]["title"]["tag"] == "plain_text"
    assert out["elements"][0]["text"] == {"text": "还剩 3 天"}
    # 未被误伤：既有 kind 与未注册 kind 语义不变
    assert apply_card_template("alert", {"text": "x"}, "interactive")[0][
        "header"]["template"] == "red"
    plain = {"text": "x"}
    assert apply_card_template("no_such_kind", plain, "interactive")[0] is plain


# 9. 解析失败显式跳过并计数（不静默把整批当空）

def test_bad_end_counted_not_silently_empty():
    rows = [
        _row(open_id="ou_good0001"),                    # 正常命中
        _row(open_id="ou_bad00002", end="2026-13-45"),  # 脏日期
        _row(open_id="ou_bad00003", end="下个月"),       # 非日期文本
        _row(open_id="ou_none0004", end=None),          # 缺 end
    ]
    report = plan_reminders(rows, now=NOW)
    assert report["ok"] is True                     # 整批可用，但问题可见
    assert report["count"] == 1
    assert report["items"][0]["open_id"] == "ou_good0001"
    assert report["skipped"]["bad_end"] == 2
    assert report["skipped"]["no_end"] == 1
    reasons = [p["reason"] for p in report["problems"]]
    assert reasons.count("bad_end") == 2
    assert "no_end" in reasons
    # 问题明细同样打码，不泄明文 open_id
    assert all("ou_bad00002" != p["open_id"] for p in report["problems"])
    # 计数守恒：items + 各 skip == 输入行数
    assert report["count"] + sum(report["skipped"].values()) == len(rows)


# 10. store 不可用不装成"今天没人该提醒"

def test_store_unavailable_not_faked_empty():
    report = plan_reminders({"ok": False, "error": "store_unavailable"}, now=NOW)
    assert report["ok"] is False
    assert report["error"] == "store_unavailable"
    assert report["items"] == []
    assert due_for_reminder(None, now=NOW) == []
    assert plan_reminders(None, now=NOW)["error"] == "bad_input"


# 11. 入参形态：整包 dict（list_subscribers 同构）与纯日期 end

def test_accepts_whole_list_subscribers_payload_and_date_only_end():
    payload = {"ok": True, "count": 1,
               "subscribers": [_row(end="2026-11-19")]}
    items = due_for_reminder(payload, now=NOW)
    assert len(items) == 1
    assert items[0]["days_left"] == 3
    assert items[0]["stage"] == "t3"
    # 纯日期 end 视为当日有效：当天仍未过期，只是 days_left=0（不在档位）
    report = plan_reminders([_row(end="2026-11-16")], now=NOW)
    assert report["items"] == []
    assert report["skipped"]["expired"] == 0
    assert report["skipped"]["not_due"] == 1


# 12. 时区串 / ISO T 串解析一致

def test_iso_and_offset_end_parsed():
    rows = [
        _row(open_id="ou_iso000001", end="2026-11-19T10:00:00"),
        _row(open_id="ou_utc000002", end="2026-11-19T02:00:00Z"),
    ]
    report = plan_reminders(rows, now=NOW)
    assert report["skipped"]["bad_end"] == 0
    assert report["count"] == 2
    assert {it["days_left"] for it in report["items"]} == {3}


# 13. 纯函数纪律：输入零副作用

def test_input_not_mutated():
    rows = [_row(), _row(plan_id="pro_monthly")]
    snapshot = copy.deepcopy(rows)
    due_for_reminder(rows, now=NOW)
    assert rows == snapshot
    item = due_for_reminder(rows, now=NOW)[0]
    text = build_reminder_text(item)
    assert isinstance(text, str) and "⏳ 订阅即将到期" in text
    # 正文可直接作为 lark_md 内容包进 subscription_expiring 卡
    out, _ = apply_card_template("subscription_expiring",
                                 {"tag": "lark_md", "content": text},
                                 "interactive")
    assert json.dumps(out, ensure_ascii=False)  # 可 JSON 序列化
