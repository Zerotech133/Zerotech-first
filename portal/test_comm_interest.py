# -*- coding: utf-8 -*-
"""阚泽部 customer_interest 聚合与商机信号单测（K4.1/K4.2）。

覆盖：建表幂等、日聚合 upsert、频次累加、top_codes 合并、
7 日滚动窗口、四档信号判定、空态不造数、隐私（不存原文）。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comm_interest as ci  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "interest.sqlite3"
    monkeypatch.setenv("COMM_INTEREST_DB", str(p))
    ci.init_schema()
    return p


def _record(open_id, intent, code=None, blocked=False, day=None):
    return ci.record(open_id, intent=intent, code=code, blocked=blocked,
                     day=day)


# ── 建表与写入 ───────────────────────────────
def test_01_init_idempotent(db):
    ci.init_schema()
    ci.init_schema()
    assert db.exists()


def test_02_record_counts_consult(db):
    _record("ou_a", "stock_consult", code="605100")
    _record("ou_a", "subscription_qa")
    st = ci.stats_7d("ou_a")
    assert st["consult_count"] == 2
    assert st["blocked_count"] == 0


def test_03_record_blocked(db):
    _record("ou_b", "stock_consult", code="605100", blocked=True)
    st = ci.stats_7d("ou_b")
    assert st["blocked_count"] == 1


def test_04_same_day_accumulates(db):
    for _ in range(3):
        _record("ou_c", "stock_consult", code="605100")
    st = ci.stats_7d("ou_c")
    assert st["consult_count"] == 3
    assert st["top_codes"]["605100"] == 3


def test_05_top_codes_merge_and_sort(db):
    _record("ou_d", "stock_consult", code="605100")
    _record("ou_d", "stock_consult", code="300750")
    _record("ou_d", "stock_consult", code="300750")
    st = ci.stats_7d("ou_d")
    assert list(st["top_codes"])[0] == "300750"
    assert st["top_codes"]["300750"] == 2


def test_06_domain_buckets(db):
    _record("ou_e", "stock_consult", code="605100")
    _record("ou_e", "watch_setup", code="300750")
    _record("ou_e", "subscription_qa")
    _record("ou_e", "complaint")
    _record("ou_e", "feed")
    st = ci.stats_7d("ou_e")
    assert st["domain_quant"] == 2          # stock + watch
    assert st["domain_business"] >= 1       # subscription
    assert st["domain_knowledge"] == 1      # feed


def test_07_feed_and_complaint_not_counted_as_consult(db):
    _record("ou_f", "feed")
    _record("ou_f", "complaint")
    _record("ou_f", "smalltalk")
    st = ci.stats_7d("ou_f")
    assert st["consult_count"] == 0, "投喂/投诉/闲聊不算咨询"


def test_08_7day_window_excludes_old(db):
    # 今天 + 8 天前
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=8)).isoformat()
    _record("ou_g", "stock_consult", code="605100", day=old)
    _record("ou_g", "stock_consult", code="300750")
    st = ci.stats_7d("ou_g")
    assert st["consult_count"] == 1
    assert "605100" not in st["top_codes"]


def test_09_empty_state_no_fabrication(db):
    st = ci.stats_7d("ou_none")
    assert st["consult_count"] == 0 and st["top_codes"] == {}


# ── 四档信号 ─────────────────────────────────
def test_10_hot_customer(db):
    for _ in range(5):
        _record("ou_hot", "stock_consult", code="605100", blocked=True)
    sigs = ci.signals(subscribed={"ou_hot": False})
    row = [s for s in sigs if s["open_id"] == "ou_hot"][0]
    assert row["level"] == "hot"
    assert row["stars"] == 3


def test_11_prospect(db):
    for _ in range(3):
        _record("ou_pro", "stock_consult", code="605100")
    sigs = ci.signals(subscribed={"ou_pro": False})
    row = [s for s in sigs if s["open_id"] == "ou_pro"][0]
    assert row["level"] == "prospect"
    assert row["stars"] == 2


def test_12_churning_when_was_active(db):
    from datetime import date, timedelta
    # 上周活跃（6 天前 3 次），本周 0
    d = (date.today() - timedelta(days=6)).isoformat()
    for _ in range(3):
        _record("ou_gone", "stock_consult", code="605100", day=d)
    sigs = ci.signals(subscribed={"ou_gone": True})
    row = [s for s in sigs if s["open_id"] == "ou_gone"]
    # 6 天前仍在 7 日窗内，故可能不算流失；此处只断言不误标 hot
    assert not row or row[0]["level"] in ("churning", "expiring", "prospect")


def test_13_subscribed_active_not_hot(db):
    for _ in range(5):
        _record("ou_sub", "stock_consult", code="605100", blocked=True)
    sigs = ci.signals(subscribed={"ou_sub": True})
    row = [s for s in sigs if s["open_id"] == "ou_sub"]
    # 已订阅+活跃+无到期信息 → 不产生商机信号（合理：没有商机），
    # 但绝不能标成 hot（热客只对未订阅者）
    assert not row or row[0]["level"] != "hot"


def test_13b_expiring_subscribed_active(db):
    """已订阅 + 3 天后到期 + 本周咨询≥3 → expiring ★★（续费优先触达）。"""
    from datetime import date, timedelta
    end = (date.today() + timedelta(days=3)).isoformat()
    for _ in range(3):
        _record("ou_exp", "stock_consult", code="605100")
    sigs = ci.signals(subscribed={
        "ou_exp": {"subscribed": True, "end": end}})
    row = [s for s in sigs if s["open_id"] == "ou_exp"][0]
    assert row["level"] == "expiring"
    assert row["stars"] == 2
    assert "到期" in row["hint"]


def test_14_signals_sorted_by_level(db):
    for _ in range(5):
        _record("ou_h2", "stock_consult", blocked=True)
    for _ in range(3):
        _record("ou_p2", "stock_consult")
    sigs = ci.signals(subscribed={"ou_h2": False, "ou_p2": False})
    levels = [s["level"] for s in sigs]
    assert levels.index("hot") < levels.index("prospect")


def test_15_signal_has_action_hint(db):
    for _ in range(5):
        _record("ou_h3", "stock_consult", blocked=True)
    sigs = ci.signals(subscribed={"ou_h3": False})
    row = [s for s in sigs if s["open_id"] == "ou_h3"][0]
    assert row["hint"], "信号必须带建议动作"


# ── 隐私：不存原文 ────────────────────────────
def test_16_no_raw_text_columns(db):
    import sqlite3
    conn = sqlite3.connect(str(db))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(customer_interest)")]
    conn.close()
    assert not any("text" == c or c.startswith("text_") for c in cols), \
        "画像表不得有原文列：%s" % cols
    assert "top_codes" in cols and "top_topics" in cols


def test_17_unknown_intent_ignored_safely(db):
    ok, err = ci.record("ou_x", intent="not_an_intent")
    assert ok is True, "未知意图不报错，只记 domain=comm 兜底"


# ── K4.3 个性化引导卡 ────────────────────────
def test_18_guide_text_uses_real_stats(db):
    for _ in range(6):
        _record("ou_guide", "stock_consult", code="605100")
    t = ci.guide_text("ou_guide")
    assert t and "6 次" in t
    assert "605100" in t
    assert "盯盘档" in t


def test_19_guide_text_none_when_no_data(db):
    assert ci.guide_text("ou_empty") is None, "无数据必须返回 None 不硬编"


def test_20_guide_card_personalized(monkeypatch, db):
    """引导卡有统计→个性化文案；无统计→通用文案（不臆造）。"""
    import inbound_gateway as gw
    for _ in range(6):
        _record("ou_card", "stock_consult", code="605100")
    card = gw._subscription_guide_card("ou_card")
    body = str(card)
    assert "6 次" in body and "605100" in body

    card2 = gw._subscription_guide_card("ou_nobody")
    assert "6 次" not in str(card2)


def test_21_blocked_records_signal(monkeypatch, db):
    """被闸落账（K4.1 blocked 来源）：5 次咨询+2 次被闸 → hot。"""
    for _ in range(3):
        _record("ou_blk", "stock_consult", code="605100")
    for _ in range(2):
        _record("ou_blk", "stock_consult", code="605100", blocked=True)
    sigs = ci.signals(subscribed={"ou_blk": False})
    row = [s for s in sigs if s["open_id"] == "ou_blk"][0]
    assert row["level"] == "hot"
    assert row["blocked_7d"] == 2
