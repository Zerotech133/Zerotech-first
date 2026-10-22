"""test_os_council.py — 会议厅契约测试（S-1b）。

纪律：真执行渲染函数（最小 DOM stub，WO-OS-30 判例）；数据生成器不碰真实
计划任务盘面（probe_seats 单测用 monkeypatch）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import gen_council_data as gcd


def test_status_light_mapping():
    assert gcd._status_light("修订已接受，门禁口径已定") == "green"
    assert gcd._status_light("分歧中") == "red"
    assert gcd._status_light("") == "yellow"  # 有交版权当修订中


def test_seat_of_rules():
    assert gcd._seat_of("", "互审单001_赵云侧回执2_2026-10-22.md") == "zhaoyun"
    assert gcd._seat_of("", "互审单001_回执3_与多方会议页设计.md") == "tianfeng"
    assert gcd._seat_of("", "评审_OS统一调度方案_2026-10-22.md") == "zhaoyun"
    assert gcd._seat_of("", "毫不相关文档.md") == "unknown"


def test_scan_excludes_non_council(tmp_path, monkeypatch):
    """P1 到点验证批等交付回执不得混入会议发言流。"""
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path / "d"))
    monkeypatch.setattr(gcd, "REPO", str(tmp_path / "d" / ".."))
    d = tmp_path / "d"; d.mkdir()
    (d / "2026-10-22_互审单001_回执3_x.md").write_text("# 回执3", encoding="utf-8")
    (d / "2026-10-22_P1到点验证批_回执.md").write_text("# P1", encoding="utf-8")
    (d / "2026-10-22_普通交付.md").write_text("# 普通", encoding="utf-8")
    topics = gcd.scan_topics()
    files = [s["file"] for t in topics for s in t["speeches"]]
    assert any("互审单001_回执3" in f for f in files)
    assert not any("P1" in f for f in files)
    assert not any("普通" in f for f in files)


def test_probe_seats_honest_unverifiable(monkeypatch):
    """不可测席位诚实标 unknowable，不得假装在线。"""
    def fake_run(*a, **k):
        class R: returncode = 0; stdout = b"True"
        return R()
    monkeypatch.setattr(gcd.subprocess, "run", fake_run)
    seats = gcd.probe_seats()
    zhuge = [s for s in seats if s["seat"] == "zhuge"][0]
    assert zhuge["verifiable"] is False and zhuge["online"] is False
    tf = [s for s in seats if s["name"].startswith("田丰")][0]
    assert tf["verifiable"] is True and tf["online"] is True


def test_payload_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path))
    monkeypatch.setattr(gcd, "REPO", str(tmp_path))
    monkeypatch.setattr(gcd, "OUT_PATH", str(tmp_path / "out" / "council.json"))
    monkeypatch.setattr(gcd, "scan_topics", lambda: [])
    monkeypatch.setattr(gcd, "probe_seats", lambda: [])
    assert gcd.main() == 0
    payload = json.load(open(tmp_path / "out" / "council.json", encoding="utf-8"))
    assert payload["schema"] == "os_council/v2"
    assert "generated_at" in payload and "note" in payload
    assert "stage_view" in payload and "route_suggestions" in payload  # v2 新增


def test_stage_view_and_routes(tmp_path, monkeypatch):
    """六阶段视图与路由建议联动（v2 核心）。"""
    monkeypatch.setattr(gcd, "TOPIC_REGISTRY", [
        {"topic": "试点任务", "tags": ["dev"], "stage": "表态",
         "originator": "laotian", "summary": "SOP 试点"}])
    monkeypatch.setattr(gcd, "DELIVERABLES", str(tmp_path))
    view = gcd.build_stage_view()
    assert view[0]["stage_index"] == 1 and view[0]["stages"][0] == "立题"
    # dev 标签应产出路由建议（lead=workbuddy）
    routes = gcd.route_suggestions()
    assert routes and routes[0]["route"]["lead"] == "workbuddy"
