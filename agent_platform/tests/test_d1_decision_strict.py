# -*- coding: utf-8 -*-
"""D1 决策 strict JSON 契约测试（2026-10-29 · 鲁肃复盘整改）。

覆盖：合法 JSON、散文/围栏、嵌套花括号、多对象、非法枚举、
confidence 越界/非数值、缺 rationale/l1_basis、空输出、prompt 输出纪律。
"""
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))

import run_shadow  # noqa: E402


def _dec(**kw):
    d = {
        "recommendation": "WATCH",
        "confidence": 0.4,
        "rationale": "多空未形成有效共识，按 L4 存疑降档",
        "l1_basis": "T1/T2 攻防未定 + N1/F1 佐证不足",
    }
    d.update(kw)
    return d


def test_valid_decision_parsed_and_score():
    import json

    d = run_shadow.parse_decision(json.dumps(_dec(recommendation="buy", confidence="0.62")))
    assert d is not None
    assert d["recommendation"] == "BUY"
    assert d["confidence"] == 0.62
    assert d["score"] == 0.62


def test_prose_and_fence_parsed():
    import json

    text = "综合裁决如下：\n```json\n%s\n```\n以上。" % json.dumps(_dec())
    assert run_shadow.parse_decision(text) is not None


def test_nested_braces_in_rationale():
    import json

    text = "结论：" + json.dumps(_dec(rationale="空头论据 {估值泡沫} vs 多头 {景气回升}"))
    d = run_shadow.parse_decision(text)
    assert d is not None and "{估值泡沫}" in d["rationale"]


def test_multiple_objects_takes_decision():
    import json

    text = json.dumps({"note": "非决策"}) + "\n" + json.dumps(_dec())
    assert run_shadow.parse_decision(text) is not None


def test_invalid_recommendation_rejected():
    import json

    assert run_shadow.parse_decision(json.dumps(_dec(recommendation="STRONG"))) is None


def test_confidence_out_of_range_rejected():
    import json

    assert run_shadow.parse_decision(json.dumps(_dec(confidence=1.5))) is None


def test_confidence_non_numeric_rejected():
    import json

    assert run_shadow.parse_decision(json.dumps(_dec(confidence="high"))) is None


def test_missing_rationale_or_basis_rejected():
    import json

    assert run_shadow.parse_decision(json.dumps(_dec(rationale=""))) is None
    d = _dec()
    del d["l1_basis"]
    assert run_shadow.parse_decision(json.dumps(d)) is None


def test_empty_or_none_rejected():
    assert run_shadow.parse_decision("") is None
    assert run_shadow.parse_decision(None) is None


def test_validate_decision_errors():
    assert run_shadow.validate_decision(_dec()) == []
    errs = run_shadow.validate_decision({"recommendation": "X", "confidence": 2})
    assert any("recommendation" in e for e in errs)
    assert any("confidence" in e for e in errs)
    assert any("rationale" in e for e in errs)


def test_prompt_requires_single_json():
    p = PLATFORM / "prompts" / "d1_decision.md"
    text = p.read_text(encoding="utf-8")
    assert "只输出**一个 JSON 对象**" in text
    assert "不要 markdown 围栏" in text
