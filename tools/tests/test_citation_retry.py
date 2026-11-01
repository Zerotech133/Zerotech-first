# -*- coding: utf-8 -*-
"""v4 引用自检重试（citation_retry）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 引用计数 count_valid_cited_units：2 个有效单元/1 个/幻觉 ID/拒答；
  2. 触发条件 needs_citation_retry：<2 且未拒答未降级、已达 2 不触发、拒答/降级不触发；
  3. 强化 prompt build_citation_retry_prompt：包含「自检修正」「至少 2 个不同单元」「原文」；
  4. answer_multi 行为：retry off 默认不重试（1 次调用）、on 时首轮 1 单元→重试 2 单元（改善）、
     重试仍 1 单元（未改善）、首轮已 2 单元不重试、拒答不重试、payload citation_retry 字段。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MULTI_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_multi_retry_under_test", MULTI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mm = _load()

CLUSTERS = [{"label": "M9 覆铜板", "book": "b", "chapter": "章", "max_score": 1.0,
             "units": [{"unit_id": "aaaa1111bbbb", "page": 2, "text": "M9 覆铜板支持 224Gbps。"},
                       {"unit_id": "cccc2222dddd", "page": 5, "text": "单颗 GPU 配套 PCB 价值量三倍。"}]}]

ONE_UNIT = "【事实】M9 覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。"
TWO_UNITS = ("【事实】M9 覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。\n"
             "【事实】单颗 GPU 配套 PCB 价值量三倍（[cccc2222dddd] p.5）。")
REFUSAL = "知识库中没有足够依据回答该问题。"


# ---------------------------------------------------------------------------
# 1. 引用计数
# ---------------------------------------------------------------------------
def test_count_valid_cited_units():
    assert mm.count_valid_cited_units(TWO_UNITS, CLUSTERS) == 2
    assert mm.count_valid_cited_units(ONE_UNIT, CLUSTERS) == 1
    assert mm.count_valid_cited_units("【事实】x（[deadbeef1234] p.1）", CLUSTERS) == 0
    assert mm.count_valid_cited_units(REFUSAL, CLUSTERS) == 0
    assert mm.count_valid_cited_units("", CLUSTERS) == 0


# ---------------------------------------------------------------------------
# 2. 触发条件
# ---------------------------------------------------------------------------
def test_needs_citation_retry_conditions():
    assert mm.needs_citation_retry({"answer": ONE_UNIT, "refusal": False, "fallback": False,
                                    "citations": [{"unit_id": "aaaa1111bbbb"}]}) is True
    assert mm.needs_citation_retry({"answer": TWO_UNITS, "refusal": False, "fallback": False,
                                    "citations": [{"unit_id": "aaaa1111bbbb"},
                                                  {"unit_id": "cccc2222dddd"}]}) is False
    # 拒答/降级不重试
    assert mm.needs_citation_retry({"answer": REFUSAL, "refusal": True, "fallback": False,
                                    "citations": []}) is False
    assert mm.needs_citation_retry({"answer": ONE_UNIT, "refusal": False, "fallback": True,
                                    "citations": []}) is False
    # 无 citations 字段时按 0 处理
    assert mm.needs_citation_retry({"answer": "无引用答案"}) is True


# ---------------------------------------------------------------------------
# 3. 强化 prompt
# ---------------------------------------------------------------------------
def test_build_citation_retry_prompt():
    base = "原始 prompt 文本"
    out = mm.build_citation_retry_prompt(base)
    assert out.startswith(base)
    assert "自检修正" in out
    assert "至少 2 个不同单元" in out
    assert "原文" in out
    assert mm.CITATION_RETRY_HINT in out
    assert mm.CITATION_RETRY_MIN_UNITS == 2


# ---------------------------------------------------------------------------
# 4. answer_multi 行为
# ---------------------------------------------------------------------------
def _run(chat_fn, citation_retry, retry_chat_fn=None):
    return mm.answer_multi("M9 覆铜板对 PCB 的影响？", clusters=CLUSTERS, chat_fn=chat_fn,
                           citation_retry=citation_retry, retry_chat_fn=retry_chat_fn,
                           prompt_version="v4")


def test_retry_off_default_does_not_retry():
    calls = []

    def chat(prompt, model=None, timeout=None):
        calls.append(prompt)
        return ONE_UNIT

    r = _run(chat, citation_retry=False)
    assert len(calls) == 1
    assert r["citation_retry"]["used"] is False
    assert r["llm"]["retries"] == 0
    assert len(r["citations"]) == 1


def test_retry_on_improves_to_two_units():
    calls = []

    def chat(prompt, model=None, timeout=None):
        calls.append(prompt)
        return ONE_UNIT

    def retry_chat(prompt, model=None, timeout=None):
        calls.append(prompt)
        return TWO_UNITS

    r = _run(chat, citation_retry=True, retry_chat_fn=retry_chat)
    assert len(calls) == 2
    assert "自检修正" in calls[1]
    info = r["citation_retry"]
    assert info == {"used": True, "first_units": 1, "second_units": 2,
                    "improved": True, "error": None}
    assert len(r["citations"]) == 2
    assert r["llm"]["retries"] == 1


def test_retry_still_insufficient_marks_not_improved():
    def chat(prompt, model=None, timeout=None):
        return ONE_UNIT

    r = _run(chat, citation_retry=True)
    info = r["citation_retry"]
    assert info["used"] is True and info["improved"] is False
    assert info["first_units"] == 1 and info["second_units"] == 1
    assert len(r["citations"]) == 1


def test_retry_not_triggered_when_already_two_units_or_refusal():
    calls = []

    def chat_ok(prompt, model=None, timeout=None):
        calls.append(prompt)
        return TWO_UNITS

    r = _run(chat_ok, citation_retry=True)
    assert len(calls) == 1 and r["citation_retry"]["used"] is False
    assert r["citation_retry"]["first_units"] == 2

    calls2 = []

    def chat_refusal(prompt, model=None, timeout=None):
        calls2.append(prompt)
        return REFUSAL

    r2 = _run(chat_refusal, citation_retry=True)
    assert len(calls2) == 1 and r2["citation_retry"]["used"] is False
    assert r2["refusal"] is True
