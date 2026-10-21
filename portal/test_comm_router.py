# -*- coding: utf-8 -*-
"""阚泽部通信路由核心单测（K1.1/K1.2/K1.3，WO-FS-148-2/3/4）。

覆盖：正则前置（不调 LLM）→ LLM 兜底（桩注入）→ 降级三态 →
域路由表完整性 → cost_aware 三档调度 → 留痕（不存原文）。

LLM 一律桩注入：单测不依赖 Ollama 在线；真实往返由
tools/comm_router_smoke.py 承担（--real 时才连模型）。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comm_router as cr  # noqa: E402


# ── 桩：LLM 返回固定文本 ──────────────────────────────
class _StubLLM:
    def __init__(self, text, status="normal"):
        self.text = text
        self.status = status
        self.calls = []

    def chat(self, system, user, model=None):
        self.calls.append((system, user, model))
        return self.text, self.status


class _BoomLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, system, user, model=None):
        self.calls += 1
        raise RuntimeError("ollama down")


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "route_log.jsonl"
    monkeypatch.setenv("COMM_ROUTE_LOG", str(p))
    return p


# ── K1.1 正则前置：命中不调 LLM（成本纪律）─────────────
def test_01_complaint_regex_no_llm(log_path):
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.9}')
    r = cr.route_message("昨天的问题到现在没人回，太失望了！", llm=llm)
    assert r["intent"] == "complaint"
    assert r["channel"] == "regex"
    assert r["llm_ok"] is False
    assert llm.calls == [], "正则命中不得调 LLM"


def test_02_subscription_regex(log_path):
    r = cr.route_message("我的订阅还剩几天？额度用了多少？")
    assert r["intent"] == "subscription_qa"
    assert r["channel"] == "regex"


def test_03_billing_regex(log_path):
    r = cr.route_message("这个月扣款怎么多了一笔，我要发票")
    assert r["intent"] == "billing"
    assert r["channel"] == "regex"


def test_04_stock_consult_regex_with_code(log_path):
    r = cr.route_message("帮我看看 605100 现在能进吗")
    assert r["intent"] == "stock_consult"
    assert r["entities"].get("code") == "605100"
    assert r["channel"] == "regex"


def test_05_watch_setup_regex(log_path):
    r = cr.route_message("帮我盯一下 300750，跌破 180 就提醒我")
    assert r["intent"] in ("watch_setup", "stock_consult")
    # 双关键词（盯/提醒 + 代码）必须判 watch_setup
    assert r["intent"] == "watch_setup"
    assert r["entities"].get("code") == "300750"


def test_06_feed_long_text(log_path):
    r = cr.route_message("这是一段很长的行业分析文字，" * 6)
    assert r["intent"] == "feed"
    assert r["channel"] == "regex"


# ── K1.1 LLM 兜底 ────────────────────────────────
def test_07_llm_fallback_for_uncovered(log_path):
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.82,'
                   ' "entities": {}}')
    r = cr.route_message("你们这个机器人是真人在回吗", llm=llm)
    assert r["intent"] == "smalltalk"
    assert r["channel"] == "llm"
    assert r["llm_ok"] is True
    assert len(llm.calls) == 1


def test_08_llm_down_degrades(log_path):
    llm = _BoomLLM()
    r = cr.route_message("你们这个机器人是真人在回吗", llm=llm)
    assert r["intent"] == "unclear"
    assert r["channel"] == "fallback"
    assert r["llm_ok"] is False
    assert llm.calls == 1


def test_09_llm_bad_json_degrades(log_path):
    llm = _StubLLM("这不是 JSON，模型胡说")
    r = cr.route_message("你们这个机器人是真人在回吗", llm=llm)
    assert r["intent"] == "unclear"
    assert r["channel"] == "fallback"
    assert r["llm_ok"] is False


def test_10_llm_illegal_intent_degrades(log_path):
    llm = _StubLLM('{"intent": "hack_the_planet", "confidence": 0.99}')
    r = cr.route_message("随便问点什么", llm=llm)
    assert r["intent"] == "unclear"
    assert r["channel"] == "fallback"


def test_11_llm_low_confidence_to_unclear(log_path):
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.31}')
    r = cr.route_message("嗯……那个", llm=llm)
    assert r["intent"] == "unclear"
    assert r["channel"] == "llm"


def test_12_llm_code_fence_tolerated(log_path):
    llm = _StubLLM('```json\n{"intent": "smalltalk", "confidence": 0.8}\n```')
    r = cr.route_message("在吗", llm=llm)
    assert r["intent"] == "smalltalk"
    assert r["llm_ok"] is True


# ── K1.2 域路由表 ────────────────────────────────
def test_13_route_table_covers_all_intents():
    assert set(cr.ROUTE_TABLE) == set(cr.INTENTS)


def test_14_complaint_route_top_priority(log_path):
    r = cr.route_message("我要投诉，太差了")
    rt = r["route"]
    assert rt["target"] == "tianfeng"
    assert rt["gate"] == "no_gate"
    assert rt["priority"] == 1


def test_15_stock_route_to_simayi(log_path):
    r = cr.route_message("帮我看看 605100 能进吗")
    assert r["route"]["target"] == "simayi"
    assert r["route"]["gate"] == "subscription"
    assert r["route"]["domain"] == "quant"


def test_16_subscription_route_self_serve(log_path):
    r = cr.route_message("我的订阅还剩几天")
    assert r["route"]["target"] == "self"
    assert r["route"]["gate"] == "no_gate"


# ── K1.3 cost_aware 三档调度 ───────────────────────
def test_17_low_confidence_cloud_tier(log_path):
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.55}')
    r = cr.route_message("嗯那个事情", llm=llm)
    assert r["model"]["tier"] == "cloud"
    assert r["model"]["binding"].startswith("glm/")


def test_18_complaint_uses_template(log_path):
    r = cr.route_message("我要投诉你们")
    assert r["model"]["tier"] == "template"


def test_19_default_fast_local(log_path):
    r = cr.route_message("我的订阅还剩几天")
    assert r["model"]["tier"] == "fast"
    assert r["model"]["binding"] == "ollama/qwen3.5:9b"


def test_20_deep_tier_constant():
    assert cr.DEEP_MODEL == "ollama/qwen3.6:35b-a3b"
    assert cr.FAST_MODEL == "ollama/qwen3.5:9b"


# ── 留痕：不存原文 ────────────────────────────────
def test_21_trace_logged_without_raw_text(log_path):
    text = "帮我看看 605100 现在能进吗"
    r = cr.route_message(text, log=True)
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["intent"] == r["intent"]
    assert entry["trace_id"] == r["trace_id"]
    assert "text" not in entry, "留痕不得存原文"
    assert "605100" not in line, "留痕不得含原文片段（含代码）"
    assert entry["text_len"] == len(text)
    assert len(entry["text_md5"]) == 32


def test_22_trace_ids_unique(log_path):
    a = cr.route_message("投诉", log=True)["trace_id"]
    b = cr.route_message("投诉", log=True)["trace_id"]
    assert a != b


def test_23_log_disabled_no_write(log_path):
    cr.route_message("投诉", log=False)
    assert not log_path.exists()


# ── 共 8 意图词表闭集 ─────────────────────────────
def test_24_intent_closed_set():
    assert cr.INTENTS == (
        "stock_consult", "watch_setup", "subscription_qa", "billing",
        "complaint", "feed", "smalltalk", "unclear",
    )
