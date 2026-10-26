"""test_council_route.py — 议会厅路由器测试。"""
from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import council_route as cr


def test_route_dev_quant():
    r = cr.route(["dev", "quant"])
    assert r["lead"] == "workbuddy"        # 两表 lead 一致
    assert "zhaoyun" in r["assist"]         # dev 表协同
    assert set(r["review"]) >= {"tianfeng"} # quant 表监读
    assert r["gate"] == "laotian"


def test_route_govern_is_zhaoyun():
    r = cr.route(["govern"])
    assert r["lead"] == "zhaoyun" and "tianfeng" in r["review"]


def test_route_no_match_honest():
    r = cr.route(["nonexistent-tag"])
    assert "error" in r  # 不硬派


def test_extract_tags_from_proposal(tmp_path):
    p = tmp_path / "prop.md"
    p.write_text("# 提案\ntags: dev, ops\n", encoding="utf-8")
    assert cr.extract_tags(str(p)) == ["dev", "ops"]


def test_extract_tags_missing_returns_empty(tmp_path):
    p = tmp_path / "prop.md"
    p.write_text("# 无标签提案\n", encoding="utf-8")
    assert cr.extract_tags(str(p)) == []  # 不猜


def test_dispatch_plan_contains_gate_reminder():
    plan = cr.dispatch_plan(["dev"])
    assert "gate" in plan and "验收" in plan["gate"]


def test_seat_cards_load_or_honest():
    cards = cr.load_seat_cards()
    assert set(cards) == {"tianfeng", "zhaoyun", "zhuge"}
    # 本机三卡已注入；读不到的席位值应为 None 而非抛错
    for seat, card in cards.items():
        assert card is None or card.get("seat") == seat
