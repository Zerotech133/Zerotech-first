# -*- coding: utf-8 -*-
"""L-B · 订阅到期提醒单测（P3.7-lite 2026-10-20，离线纯函数）。

纪律：零网络、零 IO、不真发飞书、不启服务、不连账本库（入参全部构造）。
覆盖：T-3/T-1 命中、T-10 不命中、同日同 stage 去重、已过期不提醒、
不同 plan 互不干扰、正文不含金额与明文 open_id、解析失败显式计数、
store 不可用不装空、纯日期/带时刻/带时区解析、输入零副作用、
subscription_expiring 模板色彩与标题。
P3.8-lite M-A 追加：RENEWAL_TZ 口径可切换（UTC vs Asia/Shanghai）、
非法时区名可见退化、collect_subscribers 截断提示 / limit 钳制 / store 降级。
"""

import copy
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_templates  # noqa: E402
import renewal_reminder as rr  # noqa: E402
from card_templates import apply_card_template  # noqa: E402
from renewal_reminder import (
    build_reminder_text,
    due_for_reminder,  # noqa: E402
    mask_open_id,
    plan_reminders,
)

# 固定"现在"：2026-11-16 10:00:00（本地时区口径，避免测试随运行时刻漂移）
NOW = "2026-11-16 10:00:00"

_MONEY_TOKENS = ("元", "¥", "￥", "价格", "金额", "收费", "费用")


def _row(
    open_id="ou_alice001",
    plan_id="basic_monthly",
    end="2026-11-19 10:00:00",
    status="active",
    name="Alice",
):
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
    assert "ou_alice001" not in text  # 明文 open_id 不得出现
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
    out, mt = apply_card_template(
        "subscription_expiring", {"text": "还剩 3 天"}, "interactive"
    )
    assert mt == "interactive"
    assert out["header"]["template"] == "orange"
    assert out["header"]["title"]["content"] == "⏳ 订阅即将到期"
    assert out["header"]["title"]["tag"] == "plain_text"
    assert out["elements"][0]["text"] == {"text": "还剩 3 天"}
    # 未被误伤：既有 kind 与未注册 kind 语义不变
    assert (
        apply_card_template("alert", {"text": "x"}, "interactive")[0]["header"][
            "template"
        ]
        == "red"
    )
    plain = {"text": "x"}
    assert apply_card_template("no_such_kind", plain, "interactive")[0] is plain


# 9. 解析失败显式跳过并计数（不静默把整批当空）


def test_bad_end_counted_not_silently_empty():
    rows = [
        _row(open_id="ou_good0001"),  # 正常命中
        _row(open_id="ou_bad00002", end="2026-13-45"),  # 脏日期
        _row(open_id="ou_bad00003", end="下个月"),  # 非日期文本
        _row(open_id="ou_none0004", end=None),  # 缺 end
    ]
    report = plan_reminders(rows, now=NOW)
    assert report["ok"] is True  # 整批可用，但问题可见
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
    payload = {"ok": True, "count": 1, "subscribers": [_row(end="2026-11-19")]}
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
    out, _ = apply_card_template(
        "subscription_expiring", {"tag": "lark_md", "content": text}, "interactive"
    )
    assert json.dumps(out, ensure_ascii=False)  # 可 JSON 序列化


# ---------------------------------------------------------------------------
# P3.8-lite M-A：时区口径 / 取数 limit / store 降级
# ---------------------------------------------------------------------------

# 14. 时区口径可切换：同一条 end，RENEWAL_TZ 不同 → days_left 差一天


def test_tz_switch_changes_days_left(monkeypatch):
    # now 用 aware UTC 固定，避免宿主机时区参与
    now = datetime(2026, 11, 16, 16, 30, tzinfo=timezone.utc)
    end = "2026-11-19T15:00:00Z"

    monkeypatch.setenv("RENEWAL_TZ", "Asia/Shanghai")
    sh = plan_reminders([_row(end=end)], now=now, lead_days=(3, 2, 1))
    monkeypatch.setenv("RENEWAL_TZ", "UTC")
    utc = plan_reminders([_row(end=end)], now=now, lead_days=(3, 2, 1))

    assert sh["tz"]["name"] == "Asia/Shanghai"
    assert utc["tz"]["name"] == "UTC"
    assert sh["tz"]["source"] == "env" and utc["tz"]["source"] == "env"
    assert sh["tz"]["offset"] == "+08:00"
    assert utc["tz"]["offset"] == "+00:00"
    # +08：now=11-17 00:30、end=11-19 23:00 → 2 天（t2）
    # UTC：now=11-16 16:30、end=11-19 15:00 → 3 天（t3）
    assert (sh["items"][0]["days_left"], sh["items"][0]["stage"]) == (2, "t2")
    assert (utc["items"][0]["days_left"], utc["items"][0]["stage"]) == (3, "t3")


# 15. 缺省时区固定为 Asia/Shanghai（不再随宿主机漂）


def test_default_tz_is_shanghai(monkeypatch):
    monkeypatch.delenv("RENEWAL_TZ", raising=False)
    report = plan_reminders([_row()], now=NOW)
    tz = report["tz"]
    assert tz["name"] == "Asia/Shanghai"
    assert tz["source"] == "default"
    assert tz["offset"] == "+08:00"
    assert report["items"][0]["days_left"] == 3  # 与 L-B 在 +08 宿主机上的结果一致
    if tz["degraded"]:  # Windows 无 tzdata 时：退化必须可见
        assert tz["reason"] == "zoneinfo_not_found"
        assert "tz_degraded:Asia/Shanghai" in report["warnings"]
    else:
        assert report["warnings"] == []


# 16. 非法时区名：不抛、不静默退化为 +08


def test_invalid_tz_name_degrades_visibly(monkeypatch):
    monkeypatch.setenv("RENEWAL_TZ", "Not/AZone")
    report = plan_reminders([_row()], now=NOW)
    assert report["ok"] is True and report["count"] == 1
    tz = report["tz"]
    assert tz["name"] == "Not/AZone"
    assert tz["degraded"] is True
    assert tz["reason"] == "zoneinfo_not_found"
    assert tz["offset"] == "+08:00"
    assert tz["note"] == "fallback_fixed_offset:+08:00"
    assert "tz_degraded:Not/AZone" in report["warnings"]
    assert report["items"][0]["days_left"] == 3  # 退化后仍给出可用结果


class _FakeStore:
    """list_subscribers 同构假 store：记录 limit，可注入错误 / 整包返回值。"""

    def __init__(self, total=0, payload=None, exc=None):
        self.rows = [_row(open_id="ou_bulk%05d" % i) for i in range(total)]
        self.payload = payload
        self.exc = exc
        self.calls = []

    def list_subscribers(self, limit=50, now=None):
        self.calls.append(limit)
        if self.exc is not None:
            raise self.exc
        if self.payload is not None:
            return self.payload
        return {
            "ok": True,
            "count": min(limit, len(self.rows)),
            "subscribers": self.rows[:limit],
        }


# 17. limit 默认 200；行数达到上限即提示可能截断


def test_collect_subscribers_flags_truncation():
    full = _FakeStore(total=200)
    got = rr.collect_subscribers(store=full)
    assert got["ok"] is True and got["error"] is None
    assert got["limit"] == 200 and full.calls == [200]  # 默认不再是 50
    assert got["count"] == 200
    assert "may_be_truncated:200" in got["warnings"]

    # _FakeStore 只吃 limit（老接口形态）→ 必然走单页路径并留下降级标记。
    # P3.10-lite 契约演进：warnings 不再可能为空——降级本身必须可见。
    exact = _FakeStore(total=49)
    got2 = rr.collect_subscribers(store=exact, limit=50)
    assert got2["count"] == 49
    assert got2["mode"] == "single_page"
    assert got2["warnings"] == ["offset_unsupported"]
    assert got2["complete"] is True  # 未触顶 ⇒ 已到底

    edge = _FakeStore(total=50)
    got3 = rr.collect_subscribers(store=edge, limit=50)
    assert "may_be_truncated:50" in got3["warnings"]


# 18. limit 钳制与非法 limit（store 自身只到 200，不许假装拿全）


def test_collect_subscribers_limit_clamped_and_bad_limit():
    store = _FakeStore(total=3)
    got = rr.collect_subscribers(store=store, limit=500)
    assert got["limit"] == 200 and store.calls == [200]
    assert "limit_clamped:500->200" in got["warnings"]

    bad = _FakeStore(total=1)
    got2 = rr.collect_subscribers(store=bad, limit="many")
    assert got2["limit"] == 200
    assert got2["warnings"][0] == "bad_limit:'many'"


# 19. store 失败/异常/形态不可识别：带 problems 降级，绝不当空列表


def test_collect_subscribers_store_failure_not_empty():
    degraded = _FakeStore(payload={"ok": False, "error": "store_unavailable"})
    got = rr.collect_subscribers(store=degraded)
    assert got["ok"] is False and got["error"] == "store_unavailable"
    assert got["subscribers"] == [] and got["count"] == 0
    assert [p["reason"] for p in got["problems"]] == ["store_unavailable"]
    # 整包喂给判定层同样报错，不会被伪装成"今天没人该提醒"
    report = plan_reminders(got, now=NOW)
    assert report["ok"] is False and report["error"] == "store_unavailable"

    boom = _FakeStore(exc=RuntimeError("db locked"))
    got2 = rr.collect_subscribers(store=boom)
    assert got2["ok"] is False and got2["error"] == "store_exception"
    assert "db locked" in got2["problems"][0]["detail"]
    assert got2["subscribers"] == []

    weird = _FakeStore(payload=["not", "a", "dict"])
    got3 = rr.collect_subscribers(store=weird)
    assert got3["ok"] is False and got3["error"] == "bad_payload"
    assert got3["problems"][0]["reason"] == "bad_payload"


# 20. collect → plan 端到端（拉取结构可直接喂判定层）


def test_collect_then_plan_end_to_end():
    store = _FakeStore(total=0)
    store.rows = [_row(), _row(open_id="ou_bob0002", end="2026-11-17 10:00:00")]
    fetched = rr.collect_subscribers(store=store)
    assert fetched["ok"] is True
    # 该假 store 只接受 limit ⇒ 单页路径 + 降级可见（P3.10-lite 契约演进）
    assert fetched["warnings"] == ["offset_unsupported"]
    assert fetched["complete"] is True
    report = plan_reminders(fetched, now=NOW)
    assert report["ok"] is True
    assert {it["stage"] for it in report["items"]} == {"t3", "t1"}
    assert report["count"] == 2


# ── P3.10-lite O-B 追加：分页取数口径（第 201 人之后不再漏） ─────────────


class _OffsetStore:
    """支持 offset 的假 store：无 iter_all_subscribers，走路径 2。"""

    def __init__(self, total=0, fail_from_page=None, exc=None):
        self.rows = [_row(open_id="ou_pg%05d" % i) for i in range(total)]
        self.fail_from_page = fail_from_page
        self.exc = exc
        self.pages_called = []

    def list_subscribers(self, limit=50, now=None, offset=0):
        page = offset // limit if limit else 0
        self.pages_called.append((limit, offset))
        if self.fail_from_page is not None and page >= self.fail_from_page:
            raise self.exc or RuntimeError("page boom")
        return {
            "ok": True,
            "count": min(limit, max(0, len(self.rows) - offset)),
            "subscribers": self.rows[offset : offset + limit],
        }


class _IterStore:
    """带 iter_all_subscribers 的假 store：走路径 1（与真实 store 同构）。"""

    def __init__(self, total=0, truncated=False):
        self.rows = [_row(open_id="ou_it%05d" % i) for i in range(total)]
        self.truncated = truncated

    def iter_all_subscribers(self, page_size=200, now=None, max_pages=50):
        got = self.rows[: page_size * max_pages]
        more = len(self.rows) > len(got)
        trunc = self.truncated or more
        return {
            "ok": True,
            "error": None,
            "count": len(got),
            "subscribers": got,
            "page_size": page_size,
            "pages": 1,
            "max_pages": max_pages,
            "truncated": trunc,
            "complete": not trunc,
            "problems": [],
        }


def test_ob_1_offset_store_pages_through_250():
    """路径 2：store 支持 offset 但无 iter → 逐页取全 250 人，降级可见。"""
    store = _OffsetStore(total=250)
    got = rr.collect_subscribers(store=store, limit=100)
    assert got["ok"] is True and got["count"] == 250
    assert got["pages"] == 3 and got["complete"] is True
    assert got["truncated"] is False
    assert "paged_via_list_subscribers" in got["warnings"]
    assert got["page_size"] == 100


def test_ob_2_max_pages_exhausted_reports_truncated():
    """路径 2 截断：max_pages 用尽仍有更多 → truncated 进 warnings+problems，
    complete 必须为 False——绝不假装取全。"""
    store = _OffsetStore(total=250)
    # 250 人 ÷ 1 行/页 = 250 页 > _MAX_PAGES(50) → 必然截断
    got = rr.collect_subscribers(store=store, limit=1)
    assert got["ok"] is True
    assert got["truncated"] is True and got["complete"] is False
    assert got["count"] == 50  # 恰好 _MAX_PAGES 页 × 1 行
    assert got["count"] < 250
    assert any(w.startswith("truncated_at_page:") for w in got["warnings"])
    assert any(p.get("reason") == "truncated" for p in got["problems"])


def test_ob_3_iter_store_complete_means_full():
    """路径 1：iter 接口 complete=True 是唯一可声称取全的组合。"""
    store = _IterStore(total=250)
    got = rr.collect_subscribers(store=store, limit=200)
    assert got["ok"] is True and got["count"] == 250
    assert got["complete"] is True and got["truncated"] is False
    assert got["mode"] == "iter"


def test_ob_4_midpage_exception_is_fail_visible():
    """分页中途异常 → ok=False + problems 带页码 + complete=False，
    绝不把异常当成「没有更多」。"""
    store = _OffsetStore(total=250, fail_from_page=2, exc=RuntimeError("boom"))
    got = rr.collect_subscribers(store=store, limit=100)
    assert got["ok"] is False and got["error"] == "store_exception"
    assert got["complete"] is False and got["truncated"] is False
    # page 0/1（offset 0/100）正常取回，page 2（第 3 页）抛异常 → 前 2 页保留
    assert got["count"] == 200
    assert any(p.get("page") == 3 for p in got["problems"])


def test_ob_5_limit_means_page_size_not_total_cap():
    """limit 语义变更钉住：limit=100 是单页大小，总数可超过 100。
    旧行为会在 100 截断——此例防止口径被悄悄改回去。"""
    store = _OffsetStore(total=250)
    got = rr.collect_subscribers(store=store, limit=100)
    assert got["count"] == 250 and got["page_size"] == 100
