"""test_seat_memory_sync.py — 三层记忆装配器测试。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import seat_memory_sync as sms


def test_plan_covers_three_seats():
    plan = sms.plan()
    seats = {card["seat"] for _, card in plan}
    assert seats == {"tianfeng", "zhaoyun", "zhuge"}
    for path, _ in plan:
        assert path.endswith("SEAT_CARD.json")


def test_card_schema_and_layers():
    card = sms.build_card("zhaoyun")
    assert card["schema"] == "seat_memory_card/v1"
    assert "L1_work" in card["layers"] and "L2_seat" in card["layers"] and "L3_org" in card["layers"]
    # 赵云职责含 V3.1 红线
    assert "只产建议与证据" in card["layers"]["L2_seat"]["duty"]
    # L3 是引用不是副本：refs 全部带 name+path，无内嵌内容
    for ref in card["layers"]["L3_org"]["refs"]:
        assert ref.get("name") and ref.get("path") and set(ref.keys()) <= {"name", "path"}
    # 硬规则含三条判例
    rules = " ".join(card["layers"]["L3_org"]["hard_rules"])
    for kw in ("检测器自证", "假成功", "快照假绿", "五要素"):
        assert kw in rules


def test_dry_run_no_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sms.SEATS, "tianfeng", dict(sms.SEATS["tianfeng"], home=str(tmp_path)))
    rc = sms.main(["--dry-run"])
    assert rc == 0
    assert not os.path.exists(tmp_path / "SEAT_CARD.json")  # dry-run 零写入
    assert "dry-run" in capsys.readouterr().out


def test_apply_atomic_write(tmp_path, monkeypatch):
    monkeypatch.setitem(sms.SEATS, "tianfeng", dict(sms.SEATS["tianfeng"], home=str(tmp_path)))
    rc = sms.main(["--apply"])
    assert rc == 0
    p = tmp_path / "SEAT_CARD.json"
    card = json.loads(p.read_text(encoding="utf-8"))
    assert card["cn_name"] == "田丰"
    assert not os.path.exists(str(p) + ".tmp")  # 原子写无残留
