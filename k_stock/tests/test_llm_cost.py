# -*- coding: utf-8 -*-
"""K-STOCK-101 M2：llm_cost 模块测试（tmp 目录隔离，monkeypatch DATA_DIR）。"""

from __future__ import annotations

import json
import time

import pytest

from k_stock.paper import llm_cost


@pytest.fixture()
def cost_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(llm_cost, "DATA_DIR", data)
    monkeypatch.setattr(llm_cost, "COST_DIR", data / "llm_cost")
    return data / "llm_cost"


def test_record_and_daily_accumulate(cost_env):
    ts = time.time()
    r1 = llm_cost.record_cost("chaser", "deepseek/deepseek-chat", 1_000_000, 0, ts=ts)
    r2 = llm_cost.record_cost("chaser", "deepseek/deepseek-chat", 0, 500_000, ts=ts)
    r3 = llm_cost.record_cost("other", "deepseek/deepseek-chat", 100, 100, ts=ts)
    assert r1["cost"] == 2.0
    assert r2["cost"] == 1.0
    # 每账本累计
    d = llm_cost.daily_cost("chaser")
    assert d["cost"] == pytest.approx(3.0)
    assert d["calls"] == 2
    # 全部账本
    dall = llm_cost.daily_cost()
    assert dall["cost"] == pytest.approx(3.0 + r3["cost"])
    assert dall["calls"] == 3
    # jsonl 落盘且独立于 portal.db
    files = list(cost_env.glob("*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert set(lines[0]) >= {
        "ts",
        "account",
        "binding",
        "prompt_tokens",
        "completion_tokens",
        "cost",
    }


def test_cap_breach_and_degrade(cost_env, monkeypatch):
    monkeypatch.setattr(
        llm_cost, "_tier_of", lambda a: {"tier": "T1", "daily_cost_cap": 5.0}
    )
    ts = time.time()
    # deepseek 2 元/百万：450 万 token = 9 元 > T1 cap 5
    llm_cost.record_cost("chaser", "deepseek/deepseek-chat", 4_000_000, 500_000, ts=ts)
    d = llm_cost.daily_cost("chaser")
    assert d["breached"] is True
    assert d["remaining"] == 0.0
    flag = llm_cost.check_and_flag("chaser")
    assert flag["breached"] is True
    assert flag["degrade_to"] == "ollama/qwen3.5:9b"
    # 未触线场景
    monkeypatch.setattr(
        llm_cost, "_tier_of", lambda a: {"tier": "T3", "daily_cost_cap": 100.0}
    )
    assert llm_cost.check_and_flag("chaser") == {"breached": False}


def test_ollama_free_and_unknown_estimated(cost_env):
    r1 = llm_cost.record_cost("chaser", "ollama/qwen3.5:9b", 1200, 300)
    assert r1["cost"] == 0.0
    assert "estimated" not in r1
    # 全价表未知
    r2 = llm_cost.record_cost("chaser", "acme/whatever", 1_000_000, 0)
    assert r2["estimated"] is True
    assert r2["cost"] == 50.0
    # provider 前缀命中（qwen/* ≈20）也标 estimated
    r3 = llm_cost.record_cost("chaser", "qwen/qwen2.5-72b", 1_000_000, 0)
    assert r3["estimated"] is True
    assert r3["cost"] == 20.0


def test_tier_fallback_default_cap(cost_env, monkeypatch):
    # 档案取不到 → T2 上限 20 元
    monkeypatch.setattr(llm_cost, "_tier_of", lambda a: None)
    d = llm_cost.daily_cost("nobody")
    assert d["daily_cost_cap"] == llm_cost.DEFAULT_TIER_CAP
    assert d["breached"] is False
