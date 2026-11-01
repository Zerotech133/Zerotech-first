# -*- coding: utf-8 -*-
"""guard 标记解析（marker_mode）契约测试 —— 纯离线、不调 LLM。

覆盖：
  1. strict_line 排除 markdown 段标题（`## 推断`）与「【推断】如下」标题行；
  2. strict_line 统计行首/项目符号/编号 + 【事实】/【推断】标记，支持「事实：」「推断：」；
  3. legacy 兼容旧子串行为（会把标题里的「推断」也计入）；
  4. 中文标点/多行复杂答案：few-shot/示例行与 ``` 代码块被排除；
  5. apply_guard 两模式差异（strict 通过 vs legacy inference_heavy）+ 非法 marker_mode 报错。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_guard_marker_under_test", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gd = _load()
UNIT_IDS = {"aaaa1111bbbb", "cccc2222dddd"}
BOOK_IDS = {"rp_book"}


def test_strict_line_excludes_section_headings():
    answer = (
        "## 推断\n"
        "【推断】如下：\n"
        "这是对结论的说明文字，提到推断但不应计数。\n"
        "【事实】M9 覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。\n"
        "【推断】综合看价值量提升（依据：[aaaa1111bbbb]、[cccc2222dddd]）。\n"
    )
    direct, infer = gd.count_markers(answer, "strict_line")
    assert (direct, infer) == (1, 1)
    # legacy 会把标题「推断」「【推断】如下」也计入
    l_direct, l_infer = gd.count_markers(answer, "legacy")
    assert l_infer >= 3


def test_strict_line_line_prefix_and_bullets():
    answer = (
        "- 【事实】第一条事实（[aaaa1111bbbb] p.2）\n"
        "2. 【事实】第二条事实（[cccc2222dddd] p.3）\n"
        "事实：第三条事实（[aaaa1111bbbb] p.4）\n"
        "推断：综合判断（依据：[cccc2222dddd] p.3）\n"
    )
    direct, infer = gd.count_markers(answer, "strict_line")
    assert direct == 3 and infer == 1
    assert gd.count_markers(answer, "legacy")[0] == 2  # legacy 只匹配【事实】标记（不含「事实：」前缀）


def test_strict_line_skips_fewshot_and_code_blocks():
    answer = (
        "【事实】真实事实（[aaaa1111bbbb] p.2）\n"
        "【示例（few-shot）】\n"
        "【事实】示例事实不应计入\n"
        "【推断】示例推断不应计入\n"
        "```\n"
        "【事实】代码块内不应计入\n"
        "【推断】代码块内不应计入\n"
        "```\n"
        "【推断】真实推断（依据：[cccc2222dddd] p.3）\n"
    )
    direct, infer = gd.count_markers(answer, "strict_line")
    assert (direct, infer) == (1, 1)
    # legacy 不排除任何行
    assert gd.count_markers(answer, "legacy")[1] >= 3


def test_marker_mode_validation_legacy_fallback():
    try:
        gd.count_markers("x", "bogus")
    except gd.GuardError:
        pass
    else:  # pragma: no cover
        raise AssertionError("非法 marker_mode 应抛 GuardError")
    # legacy 模式基于提供的 marker 字段（兼容旧结果），无字段时退化为 legacy 子串计数
    result = {"answer": "【推断】标题\n【事实】事实", "citations": []}
    m_legacy = gd.compute_guard_metrics(result, UNIT_IDS, BOOK_IDS, marker_mode="legacy")
    m_strict = gd.compute_guard_metrics(result, UNIT_IDS, BOOK_IDS, marker_mode="strict_line")
    assert m_legacy["inference_markers"] == 1 and m_strict["inference_markers"] == 1
    assert m_strict["marker_mode"] == "strict_line"


def test_apply_guard_marker_mode_changes_outcome():
    # 答案含「段标题：推断」与 2 条事实 → legacy 认为推断多（heavy），strict 只按行首显式标记
    answer = (
        "【事实】事实一（[aaaa1111bbbb] p.2）\n"
        "【事实】事实二（[cccc2222dddd] p.3）\n"
        "关于推断：以下为综合说明（说明文字不应被计为推断标记）\n"
    )
    raw = {"answer": answer, "citations": [{"unit_id": "aaaa1111bbbb"}, {"unit_id": "cccc2222dddd"}],
           "retrieved": [{"unit_id": "aaaa1111bbbb"}, {"unit_id": "cccc2222dddd"}],
           "fallback": False, "refusal": False}
    legacy = gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="mark", marker_mode="legacy")
    strict = gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="mark", marker_mode="strict_line")
    assert strict["guard_passed"] is True
    assert legacy["inference_ratio"] > strict["inference_ratio"]
    # 两模式下都保留原始答案（mark 不替换）
    assert strict["answer"] == answer and legacy["answer"] == answer
