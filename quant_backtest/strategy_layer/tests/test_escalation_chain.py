# -*- coding: utf-8 -*-
"""N9 上行提醒链（escalation_chain）单测（全 mock，零串味：不触网/库/交易）。

先红后绿：构造经 xunyou.assign_tiers 的 combined 行，喂入 select_escalation，
断言：
  - T1 进入 auto_alert_symbols；
  - divergence 绝不进入 auto_alert_symbols 且进入 manual_review_required；
  - suppressed_trading == True；
  - build_upstream_message 含 RESEARCH_ONLY / BLOCK_CUTOVER 标记。
"""
import pytest

from strategy_layer.escalation_chain import (
    build_upstream_message,
    select_escalation,
)
from strategy_layer.xunyou import assign_tiers

DAY = "20261019"


def row(symbol, score, divergence=False):
    """与 test_xunyou_tier.py 同口径构造 combined 行。"""
    return {"trade_date": DAY, "symbol": symbol, "action": "buy",
            "score": score, "confidence": 0.8, "divergence": divergence,
            "rationale": "r-" + symbol, "contributions": {}}


def _tiered(*rows):
    """经 assign_tiers 处理（模拟 xunyou 池产出）。"""
    return assign_tiers(list(rows))


# ── T1 / T2 / divergence 基础装配 ──────────────────────────────────
def test_t1_enters_auto_alert_symbols():
    tiered = _tiered(row("T1A", 99.9), row("T2A", 97.0),
                     row("DIV", 100.0, divergence=True))
    notice = select_escalation(tiered)
    assert "T1A" in notice["auto_alert_symbols"]
    assert notice["t1_alerts"][0]["symbol"] == "T1A"
    assert notice["t1_alerts"][0]["rank"] == 1
    assert notice["t2_watch"][0]["symbol"] == "T2A"


def test_divergence_excluded_from_auto_alert_but_in_manual_review():
    tiered = _tiered(row("T1A", 99.9), row("DIV", 100.0, divergence=True))
    notice = select_escalation(tiered)
    # 分歧票绝不进入自动告警
    assert "DIV" not in notice["auto_alert_symbols"]
    # 分歧票进入人工复核清单
    assert "DIV" in notice["manual_review_required"]
    # 分歧段含该票
    assert any(d["symbol"] == "DIV" for d in notice["divergences"])


def test_divergence_priority_over_t1_defensive():
    """防御性：若某票同时 T1 与 divergence，以 divergence 优先。"""
    # 构造一个被 assign_tiers 标 T1 后再强制加 divergence 的防御场景
    tiered = _tiered(row("BOTH", 100.0))
    tiered[0]["tier"] = "T1"
    tiered[0]["divergence"] = True
    tiered[0]["review_required"] = True
    notice = select_escalation(tiered)
    assert "BOTH" not in notice["auto_alert_symbols"]
    assert "BOTH" in notice["manual_review_required"]


def test_review_required_rows_also_in_manual_review():
    tiered = _tiered(row("T1A", 99.9))
    tiered[0]["review_required"] = True
    notice = select_escalation(tiered)
    assert "T1A" in notice["manual_review_required"]
    # 注意：review_required 但非 divergence 仍可能进 T1 自动告警（仅人工复核
    # 标记不拦截非分歧票）
    assert "T1A" in notice["auto_alert_symbols"]


def test_suppressed_trading_true():
    tiered = _tiered(row("T1A", 99.9), row("T2A", 97.0),
                     row("DIV", 100.0, divergence=True))
    notice = select_escalation(tiered)
    assert notice["suppressed_trading"] is True


def test_upstream_message_carries_markers_and_no_send():
    tiered = _tiered(row("T1A", 99.9), row("T2A", 97.0),
                     row("DIV", 100.0, divergence=True))
    notice = select_escalation(tiered)
    msg = build_upstream_message(notice)
    assert "RESEARCH_ONLY_NOT_PRODUCTION" in msg
    assert "BLOCK_CUTOVER" in msg
    assert "不直连飞书" in msg and "不自动发送" in msg
    assert "荀攸" in msg and "司马懿" in msg and "田丰" in msg
    assert "T1A" in msg and "DIV" in msg


def test_empty_input_safe():
    notice = select_escalation([])
    assert notice["auto_alert_symbols"] == []
    assert notice["manual_review_required"] == []
    assert notice["suppressed_trading"] is True
    assert "RESEARCH_ONLY_NOT_PRODUCTION" in build_upstream_message(notice)


def test_fail_visible_on_missing_fields():
    # 缺 symbol
    with pytest.raises(ValueError):
        select_escalation([{"tier": "T1", "score": 99.9}])
    # 缺 tier（未经 assign_tiers）
    with pytest.raises(ValueError):
        select_escalation([{"symbol": "X", "score": 99.9}])
