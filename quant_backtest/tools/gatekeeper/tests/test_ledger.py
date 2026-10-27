#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger 测试：原子追加（行完整、崩溃语义）、(market,date) 查询、
损坏行容错。"""

import json
import os

from gatekeeper import GateDecision
from gatekeeper.ledger import Ledger


def _dec(market, date, safe=True):
    return GateDecision(
        market=market,
        date=date,
        safe=safe,
        conditions={},
        features={"breadth": 0.5},
        policy_ver="v3_breadth40_med20",
        policy_sha="a" * 16,
        feeder="qpzm_parquet",
        ts="2026-10-15T09:00:00",
    )


def test_atomic_append_lines(tmp_path):
    """追加 N 行 → 每行完整 JSON、行数一致、无半截行（原子性）。"""
    fp = str(tmp_path / "dec.jsonl")
    led = Ledger(fp)
    for i in range(5):
        led.append(_dec("cn", "2026101%d" % (i + 1)))
    with open(fp, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert len(lines) == 5
    for ln in lines:  # 每行独立可解析 = 无半截写入
        assert json.loads(ln)["policy_ver"] == "v3_breadth40_med20"
    assert led.count() == 5


def test_append_creates_nested_dir(tmp_path):
    fp = str(tmp_path / "a" / "b" / "dec.jsonl")
    Ledger(fp).append(_dec("cn", "20261014"))
    assert os.path.exists(fp)


def test_query_by_market_and_date(tmp_path):
    led = Ledger(str(tmp_path / "dec.jsonl"))
    led.append(_dec("cn", "20261013"))
    led.append(_dec("cn", "20261014"))
    led.append(_dec("us", "20261014"))
    rows = led.query("cn")
    assert len(rows) == 2 and all(r["market"] == "cn" for r in rows)
    rows = led.query("cn", "20261014")
    assert len(rows) == 1 and rows[0]["date"] == "20261014"
    assert led.query("us", "20261013") == []
    assert led.query("hk") == []


def test_query_tolerates_corrupt_lines(tmp_path):
    """半截/损坏行跳过，不炸查询。"""
    fp = str(tmp_path / "dec.jsonl")
    led = Ledger(fp)
    led.append(_dec("cn", "20261014"))
    with open(fp, "a", encoding="utf-8") as f:
        f.write('{"market": "cn", "date": "20261\n')  # 模拟崩溃残留（含换行）
    led.append(_dec("cn", "20261015"))
    assert led.count() == 2
    assert [r["date"] for r in led.query("cn")] == ["20261014", "20261015"]


def test_query_missing_file(tmp_path):
    assert Ledger(str(tmp_path / "nope.jsonl")).query("cn") == []
    assert Ledger(str(tmp_path / "nope.jsonl")).count() == 0
