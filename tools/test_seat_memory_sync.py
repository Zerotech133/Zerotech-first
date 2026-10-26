"""test_seat_memory_sync.py — 三层记忆装配器测试。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import seat_memory_sync as sms


def _isolate_all_seats(tmp_path, monkeypatch):
    """P0-5：把**三个席位**的 home 全部指到 tmp。

    旧测试只 monkeypatch 田丰一个席位，却调用 `sms.main(["--apply"])`——
    该命令会遍历全部 SEATS，于是会**真实覆盖**
    `~/.hermes/profiles/zhaoyun/SEAT_CARD.json` 与
    `legion_agents/zhuge/workspace/SEAT_CARD.json`（测试污染生产文件）。
    """
    homes = {}
    for seat in ("tianfeng", "zhaoyun", "zhuge"):
        home = tmp_path / seat
        home.mkdir()
        monkeypatch.setitem(sms.SEATS, seat,
                            dict(sms.SEATS[seat], home=str(home)))
        homes[seat] = home
    return homes


def test_plan_covers_three_seats():
    plan = sms.plan()
    seats = {card["seat"] for _, card in plan}
    assert seats == {"tianfeng", "zhaoyun", "zhuge"}
    for path, _ in plan:
        assert path.endswith("SEAT_CARD.json")


def test_card_schema_and_layers():
    card = sms.build_card("zhaoyun")
    assert card["schema"] == "seat_memory_card/v1.1"
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


def test_capability_summary_linkage(tmp_path, monkeypatch):
    """C2 联动：能力清单注入后，席位卡自动并入摘要。"""
    monkeypatch.setitem(sms.SEATS, "zhaoyun", dict(sms.SEATS["zhaoyun"], home=str(tmp_path)))
    # 未注入时如实报
    card0 = sms.build_card("zhaoyun")
    assert isinstance(card0["capabilities_summary"], str) and "未注入" in card0["capabilities_summary"]
    # 注入后并摘要
    skills = tmp_path / "skills"; skills.mkdir(parents=True)
    (skills / "CAPABILITIES.json").write_text(json.dumps({
        "total": 2, "ready": 1, "pending_key": 1, "pending_wiring": 0,
        "capabilities": [{"name": "aa", "status": "ready"}, {"name": "bb", "status": "pending_key"}],
        "generated_at": "2026-10-23T04:00:00+08:00"}), encoding="utf-8")
    card1 = sms.build_card("zhaoyun")
    cs = card1["capabilities_summary"]
    assert cs["ready"] == 1 and cs["ready_names"] == ["aa"] and cs["pending_names"] == ["bb"]


def test_dry_run_no_write(tmp_path, monkeypatch, capsys):
    homes = _isolate_all_seats(tmp_path, monkeypatch)
    rc = sms.main(["--dry-run"])
    assert rc == 0
    for home in homes.values():
        assert not os.path.exists(home / "SEAT_CARD.json")  # dry-run 零写入
    assert "dry-run" in capsys.readouterr().out


def test_apply_atomic_write(tmp_path, monkeypatch):
    homes = _isolate_all_seats(tmp_path, monkeypatch)
    rc = sms.main(["--apply"])
    assert rc == 0
    for seat, home in homes.items():
        p = home / "SEAT_CARD.json"
        assert p.is_file(), "席位 %s 的卡未落在隔离目录" % seat
        assert not os.path.exists(str(p) + ".tmp")  # 原子写无残留
    card = json.loads((homes["tianfeng"] / "SEAT_CARD.json").read_text(encoding="utf-8"))
    assert card["cn_name"] == "田丰"


def test_seat_base_labels_not_stale():
    """角色能力诚实性：base 标签必须反映真实主脑/载体，不得留旧口径。"""
    tf = sms.SEATS["tianfeng"]["base"]
    assert "9b" in tf.lower() and "glm" in tf.lower(), tf
    assert "Hermes（GLM）" not in tf
    zh = sms.SEATS["zhuge"]["base"]
    assert "deepseek" in zh.lower(), zh
    assert "GPT-6.0" not in zh and "key 未注入" not in zh
    zy = sms.SEATS["zhaoyun"]["base"]
    assert "key 未到位" not in zy, zy

