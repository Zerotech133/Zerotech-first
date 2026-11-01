# -*- coding: utf-8 -*-
"""D1 辩论质量评估测试（2026-11-01 WO-D1-DEBATEQ-20261101）。

全部离线纯函数：不建会议文件、不写账本、不联网。
覆盖：空材料低分 / 四线齐全高分 / 缺一条扣分 / T1-T2 冲突 / 立场倒戈 /
否定式不误判 / 票型分歧与缺席 / 无 dissent / 会议汇总 / 开会议 / 确定性 /
非裁决标记 / 常量与运行时一致 / 非 dict 输入。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import d1_decision as D  # noqa: E402
from agent_platform import d1_debate_quality as Q  # noqa: E402
from agent_platform import d1_meeting as M  # noqa: E402


def _full_record():
    """四线齐全 + 引用 + 反方意见 + 四席票型 + D1 最终裁决。"""
    return {
        "bull": "T1 多方：3买回抽不破中枢，量能放大 1.8 倍，来源：xsec L1 主假设。",
        "bear": "T2 空方：高位放量长阴，出货迹象明显，距 20 日低仅 0.12%（依据：K线 2026-10-31）。",
        "sentiment": "N1 情绪：zscore +2.1，换手 12.6%，情绪过热（来源：涨停池 2026-10-31）。",
        "fundamental": "F1 基本面：PE 18.6、PB 2.4、ROE 12.3%，估值处于中枢（来源：quote_snapshot）。",
        "evidence": ["bridge:L1@2026-10-31", "公告:2026-10-30", "K线:8日"],
        "dissent": ["T2: 量能不足，反弹难持续", "N1: 情绪 zscore 已高于 2"],
        "votes": {
            "T1": {"tier": "BUY", "confidence": 0.72},
            "T2": {"tier": "SELL", "confidence": 0.66},
            "N1": {"tier": "HOLD", "confidence": 0.55},
            "F1": {"tier": "BUY", "confidence": 0.61},
        },
        "final_decision": {"tier": "BUY", "confidence": 0.64},
    }


def _meeting_doc():
    return {
        "meeting_id": "d1m-20261101233000-abcdef",
        "code": "002445.SZ",
        "topic": "D1 投研会测试",
        "status": "finalized",
        "votes": {
            "T1": {"tier": "BUY", "confidence": 0.72,
                   "rationale": "T1 看多，3买回抽不破中枢（来源：K线 2026-10-31）",
                   "evidence": ["K线"]},
            "T2": {"tier": "SELL", "confidence": 0.66,
                   "rationale": "T2 看空，高位放量长阴（来源：公告 2026-10-30）",
                   "evidence": ["公告"]},
            "N1": {"tier": "HOLD", "confidence": 0.55,
                   "rationale": "N1 情绪过热，换手 12.6%（来源：涨停池）",
                   "evidence": ["涨停池"]},
            "F1": {"tier": "HOLD", "confidence": 0.58,
                   "rationale": "F1 估值合理，PE 18.6（来源：quote_snapshot）",
                   "evidence": ["quote"]},
        },
        "final_decision": {
            "tier": "BUY",
            "confidence": 0.64,
            "rationale": "综合四线后买入观察，风险提示见 dissent",
            "evidence": ["bridge"],
            "dissent": ["T2: 量能不足"],
        },
    }


def _codes(flags):
    return [flag["code"] for flag in flags]


def _all_keys(obj):
    if isinstance(obj, dict):
        keys = set(obj)
        for value in obj.values():
            keys |= _all_keys(value)
        return keys
    if isinstance(obj, (list, tuple)):
        keys = set()
        for item in obj:
            keys |= _all_keys(item)
        return keys
    return set()


def test_empty_record_low_score():
    quality = Q.score_debate({})
    assert quality["score"] == 0
    assert quality["grade"] == "D"
    assert quality["coverage"]["coverage"] == 0.0
    assert quality["coverage"]["missing"] == list(Q.LINES)
    assert quality["dimensions"] == {"evidence": 0, "balance": 0,
                                     "specificity": 0, "dissent": 0}
    assert quality["penalties"]["points"] > 0
    assert quality["not_a_verdict"] is True


def test_full_four_lines_high_score():
    quality = Q.score_debate(_full_record())
    assert quality["score"] >= 80
    assert quality["grade"] in ("A", "B")
    assert quality["coverage"]["coverage"] == 1.0
    assert quality["coverage"]["missing"] == []
    assert quality["coverage"]["uncited"] == []
    assert quality["penalties"]["points"] == 0
    for dimension in ("evidence", "balance", "specificity", "dissent"):
        assert quality["dimensions"][dimension] > 0


def test_missing_one_line_deducts():
    full = Q.score_debate(_full_record())
    record = _full_record()
    record.pop("fundamental")
    quality = Q.score_debate(record)
    assert quality["coverage"]["missing"] == ["fundamental"]
    assert quality["coverage"]["coverage"] == 0.75
    assert quality["penalties"]["points"] == Q.PENALTY_MISSING_LINE
    assert quality["dimensions"]["evidence"] < full["dimensions"]["evidence"]
    assert quality["score"] < full["score"]


def test_t1_t2_direction_conflict_flag():
    flags = Q.disagreement_flags(_full_record())
    assert isinstance(flags, list) and flags.not_a_verdict is True
    conflict = [flag for flag in flags if flag["code"] == "t1_t2_direction_conflict"]
    assert len(conflict) == 1
    assert conflict[0]["severity"] == "info"
    assert conflict[0]["observed"] == {"t1": "bullish", "t2": "bearish"}
    assert "非缺陷" in conflict[0]["message"]  # 只描述分歧，不替 D1 定性
    assert {flag["severity"] for flag in flags} <= {"info", "warn"}
    assert all(flag["not_a_verdict"] is True for flag in flags)


def test_stance_collapse_flag_is_soft_warning():
    record = _full_record()
    record["bull"] = "T1：今日转看空，反弹结束，量能不足（来源：K线 2026-10-31）。"
    flags = Q.disagreement_flags(record)
    collapse = [flag for flag in flags if flag["code"] == "stance_collapse"]
    assert len(collapse) == 1
    assert collapse[0]["severity"] == "warn"
    assert collapse[0]["observed"]["seats"] == ["T1"]
    assert "不代表其论据不成立" in collapse[0]["message"]


def test_negated_stance_not_flagged():
    record = _full_record()
    record["bull"] = "T1 多方：不看空，中枢不破继续持有（来源：K线 2026-10-31）。"
    coverage = Q.evidence_coverage(record)
    assert coverage["lines"]["bull"]["direction"] == "bullish"
    assert "stance_collapse" not in _codes(Q.disagreement_flags(record))


def test_votes_tier_divergence_and_missing_seats():
    record = _full_record()
    record["votes"] = {"T1": {"tier": "BUY"}, "T2": {"tier": "SELL"},
                       "N1": {"tier": "HOLD"}}
    flags = Q.disagreement_flags(record)
    missing = [flag for flag in flags if flag["code"] == "votes_missing_seats"]
    assert len(missing) == 1
    assert missing[0]["observed"]["missing"] == ["F1"]
    assert missing[0]["severity"] == "warn"
    divergence = [flag for flag in flags if flag["code"] == "votes_tier_divergence"]
    assert len(divergence) == 1
    assert divergence[0]["observed"]["tiers"] == {"BUY": 1, "SELL": 1, "HOLD": 1}
    assert "votes_direction_opposed" in _codes(flags)


def test_no_dissent_zero_dimension_and_lower_score():
    record = _full_record()
    record.pop("dissent")
    record["votes"] = {"T1": {"tier": "BUY"}, "T2": {"tier": "BUY"},
                       "N1": {"tier": "HOLD"}, "F1": {"tier": "BUY"}}
    quality = Q.score_debate(record)
    assert quality["dimensions"]["dissent"] == 0
    assert quality["score"] < Q.score_debate(_full_record())["score"]


def test_dissent_not_recorded_flag():
    record = _full_record()
    record.pop("dissent")
    flags = Q.disagreement_flags(record)
    flag = [item for item in flags if item["code"] == "dissent_not_recorded"]
    assert len(flag) == 1
    assert flag[0]["severity"] == "warn"
    # 明确分歧存在时，同一记录补上 dissent 就不再提示
    record["dissent"] = ["T2: 量能不足"]
    assert "dissent_not_recorded" not in _codes(Q.disagreement_flags(record))


def test_evaluate_meeting_summary():
    out = Q.evaluate_meeting(_meeting_doc())
    assert out["not_a_verdict"] is True
    assert out["replaces_d1"] is False and out["advisory"] is True
    assert out["final_tier"] == "BUY"
    assert out["final_confidence"] == 0.64
    assert out["vote_count"] == 4
    assert out["votes_missing"] == []
    assert out["tier_distribution"] == {"BUY": 1, "SELL": 1, "HOLD": 2}
    assert out["quality"]["score"] == out["score"] > 0
    assert out["quality"]["coverage"]["missing"] == []
    assert out["quality"]["coverage"]["coverage"] == 1.0
    assert [flag["code"] for flag in out["flags"]]


def test_evaluate_meeting_accepts_record_like_dict():
    out = Q.evaluate_meeting(_full_record())
    assert out["final_tier"] == "BUY"
    assert out["quality"]["coverage"]["coverage"] == 1.0
    assert out["quality"]["dimensions"]["dissent"] == 80
    assert out["votes_missing"] == []


def test_evaluate_open_meeting_has_no_final_tier():
    out = Q.evaluate_meeting({"meeting_id": "d1m-20261101233000-abcdef",
                              "code": "002445.SZ", "status": "open", "votes": {}})
    assert out["final_tier"] is None
    assert out["votes_missing"] == list(Q.VOTERS)
    assert out["score"] == 0
    assert out["quality"]["coverage"]["missing"] == list(Q.LINES)


def test_determinism_and_no_input_mutation():
    record = _full_record()
    before = copy.deepcopy(record)
    first = Q.score_debate(record)
    second = Q.score_debate(record)
    assert first == second
    assert record == before
    payload = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert json.loads(payload) == first  # 可落账/可回读
    assert "generated_at" not in _all_keys(first)
    assert "created_at" not in _all_keys(first)


def test_outputs_declare_not_a_verdict():
    record = _full_record()
    coverage = Q.evidence_coverage(record)
    quality = Q.score_debate(record)
    meeting = Q.evaluate_meeting(_meeting_doc())
    for output in (coverage, quality, meeting):
        assert output["not_a_verdict"] is True
        assert output["advisory"] is True
        assert output["replaces_d1"] is False
        assert output["readonly"] is True
        assert output["side_effects"] == []
    flags = Q.disagreement_flags(record)
    assert isinstance(flags, list) and flags.not_a_verdict is True
    assert all(flag["not_a_verdict"] is True for flag in flags)
    report = Q.disagreement_report(record)
    assert report["not_a_verdict"] is True
    assert report["count"] == len(flags)
    assert report["by_severity"] == {"info": 3, "warn": 0}


def test_non_dict_input_is_empty_material():
    for raw in (None, "", [], "bull", 42):
        quality = Q.score_debate(raw)
        assert quality["score"] == 0
        assert quality["coverage"]["missing"] == list(Q.LINES)


def test_seats_and_tiers_align_with_runtime():
    assert Q.VOTERS == M.VOTERS
    assert Q.TIERS == D.TIERS
    assert [Q.LINE_SEATS[name] for name in Q.LINES] == list(M.VOTERS)
    assert all(Q.LINE_SEATS[name] == seat for name, seat in zip(Q.LINES, M.VOTERS))
