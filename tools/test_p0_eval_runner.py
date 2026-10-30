# -*- coding: utf-8 -*-
"""P0 eval judge 规则回归：拒绝答案不应被 citation_required 误判。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import p0_eval_runner as r  # noqa: E402


def test_refusal_without_citation_passes():
    case = {"checks": {"citation_required": True}}
    out = r.judge(case, "资料未取得，无法确认。")
    assert out["pass"] is True
    assert out["refused"] is True


def test_non_refusal_without_citation_fails():
    case = {"checks": {"citation_required": True}}
    out = r.judge(case, "8795 网关提供 LLM 调用入口。")
    assert out["pass"] is False
    assert "citation_missing" in out["reasons"]


def test_factual_answer_with_citation_passes():
    case = {"checks": {"citation_required": True, "any_of": ["8795"]}}
    out = r.judge(case, "8795 提供调用入口 [S1]。")
    assert out["pass"] is True
