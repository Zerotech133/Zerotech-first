# -*- coding: utf-8 -*-
"""引用校验器测试（task-184，离线/确定性，不联网）。

覆盖：
  1) 支撑句：引用 ctx 与句子 bigram 覆盖高 → valid；
  2) 不支撑句：引用 ctx 与句子无关 → unsupported；
  3) 多引用：取被引用 ctx 的最大支撑度；
  4) 无引用句：记 weak；
  5) 空答案：empty=True、rate=0；
  6) 引用越界：citation_out_of_range；
  7) 混合答案聚合：valid_rate/weak_rate/unsupported 统计自洽；
  8) 阈值可配：提高阈值使临界句失效。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "citation_verifier.py"

_spec = importlib.util.spec_from_file_location("cv_test", SCRIPT)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)  # type: ignore[union-attr]

CTX = [
    {"doc_id": "C:000", "text": "均线粘合度衡量多条均线的收敛程度，收敛后常伴随方向选择。"},
    {"doc_id": "U:u1", "text": "煤电具有兜底价值与灵活调节能力，在极端天气下保供。"},
    {"doc_id": "U:u2", "text": "仓位管理强调单笔风险预算与止损纪律。"},
]


def test_supported_sentence():
    r = m.verify_answer("均线粘合度衡量多条均线的收敛程度 [1]。", CTX)
    assert r["sentences"] == 1 and r["cited_sentences"] == 1
    assert r["citation_valid_rate"] == 1.0 and r["all_supported"] is True
    assert r["per_sentence"][0]["support"] >= 0.25


def test_unsupported_sentence():
    r = m.verify_answer("光伏组件的转换效率已超过百分之三十 [1]。", CTX)
    assert r["citation_valid_rate"] == 0.0
    assert r["unsupported_count"] == 1
    assert r["unsupported_sentences"][0]["reason"] == "low_support"


def test_multi_citation():
    # 引用 [1][2]，仅 [1] 支撑 → 取最大支撑度仍 valid
    r = m.verify_answer("均线粘合度衡量多条均线收敛程度 [1][2]。", CTX)
    assert r["citation_valid_rate"] == 1.0 and r["all_supported"] is True
    # 两个引用都与句子无关 → 不支撑
    r2 = m.verify_answer("量子纠缠的贝尔不等式推导过程 [1][2]。", CTX)
    assert r2["citation_valid_rate"] == 0.0 and r2["unsupported_count"] == 1


def test_no_citation_weak():
    r = m.verify_answer("均线粘合度用于判断趋势。", CTX)
    assert r["weak_sentences"] == 1 and r["weak_rate"] == 1.0
    assert r["cited_sentences"] == 0 and r["citation_valid_rate"] == 0.0
    assert r["per_sentence"][0]["reason"] == "no_citation"


def test_empty_answer():
    r = m.verify_answer("", CTX)
    assert r["empty"] is True and r["sentences"] == 0
    assert r["citation_valid_rate"] == 0.0 and r["weak_rate"] == 0.0
    assert r["all_supported"] is False


def test_out_of_range():
    r = m.verify_answer("均线粘合度衡量多条均线收敛程度 [9]。", CTX)
    assert r["citation_valid_rate"] == 0.0
    assert r["unsupported_sentences"][0]["reason"] == "citation_out_of_range"


def test_mixed_aggregate_and_threshold():
    answer = ("均线粘合度衡量多条均线收敛程度 [1]。"      # 支撑
              "光伏组件效率超过百分之三十 [2]。"          # 不支撑
              "仓位管理强调风险预算。")                    # weak
    r = m.verify_answer(answer, CTX)
    assert r["sentences"] == 3 and r["cited_sentences"] == 2
    assert r["weak_sentences"] == 1
    assert r["citation_valid_rate"] == 0.5 and r["weak_rate"] == round(1 / 3, 4)
    assert r["all_supported"] is False and r["unsupported_count"] == 1
    # 阈值提高到 0.99：支撑句失效
    r2 = m.verify_answer("均线粘合度衡量多条均线收敛程度 [1]。", CTX, threshold=0.99)
    assert r2["citation_valid_rate"] == 0.0
