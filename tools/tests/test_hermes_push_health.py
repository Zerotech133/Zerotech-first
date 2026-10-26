# -*- coding: utf-8 -*-
"""Hermes 推送健康判级单测（K-ORG-20261026-HERMESFIX）。

只测纯函数 _hermes_push_level / _parse_ts_local，不读写真实 push_log。
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gen_health_card as hc  # noqa: E402


def test_level_green_when_recent_success():
    rec = {"available": True, "last_ok_age_h": 2.0, "win7_total": 5, "win7_ok": 5,
           "win7_rate": 1.0, "last": {"ok": True, "elapsed_s": 90}, "dlq_count": 0}
    level, detail = hc._hermes_push_level(rec)
    assert level == "green", detail
    assert "最近成功 2.0h 前" in detail


def test_level_red_when_never_success():
    rec = {"available": True, "last_ok_age_h": None, "win7_total": 3, "win7_ok": 0,
           "win7_rate": 0.0, "last": {"ok": False, "elapsed_s": 121}, "dlq_count": 3}
    level, detail = hc._hermes_push_level(rec)
    assert level == "red", detail
    assert "从未成功" in detail


def test_level_red_when_stale_beyond_48h():
    rec = {"available": True, "last_ok_age_h": 50.0, "win7_total": 4, "win7_ok": 2,
           "win7_rate": 0.5, "last": {"ok": False, "elapsed_s": 121}, "dlq_count": 0}
    assert hc._hermes_push_level(rec)[0] == "red"


def test_level_yellow_when_stale_and_dlq():
    rec = {"available": True, "last_ok_age_h": 40.0, "win7_total": 2, "win7_ok": 1,
           "win7_rate": 0.5, "last": {"ok": False, "elapsed_s": 121}, "dlq_count": 1}
    level, detail = hc._hermes_push_level(rec)
    assert level == "yellow", detail
    assert "DLQ 1 条" in detail


def test_level_yellow_low_success_rate():
    rec = {"available": True, "last_ok_age_h": 10.0, "win7_total": 10, "win7_ok": 3,
           "win7_rate": 0.3, "last": {"ok": False, "elapsed_s": 120}, "dlq_count": 0}
    assert hc._hermes_push_level(rec)[0] == "yellow"


def test_level_red_when_log_missing():
    rec = {"available": False, "log_path": "x.jsonl"}
    level, detail = hc._hermes_push_level(rec)
    assert level == "red"
    assert "不存在" in detail


def test_parse_ts_local_handles_iso_and_bad():
    ts = hc._parse_ts_local("2026-10-25T22:59:42")
    assert ts is not None and ts.year == 2026
    assert hc._parse_ts_local("") is None
    assert hc._parse_ts_local("不是时间") is None
