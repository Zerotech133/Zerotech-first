# -*- coding: utf-8 -*-
"""WO-LEGION-S24 Z2 对照实验卡测试。

验收（《赵云进化层V3》Z2）：手工跑通一次三卡并列实验，产出对比表。
另覆盖：Z5 前置拦截、无签字拒绝结案、无证据拒绝采纳、pass-through 反证。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))
sys.path.insert(0, str(_PLATFORM / "engine"))

from engine import experiment_card as E  # noqa: E402


def _card(**kw):
    base = dict(hypothesis="候选判据是否优于基线",
                candidate_diff={"criteria/rg.md": "样本外验证条款"},
                verifier=["样本外达标", "回滚预案"])
    base.update(kw)
    return E.create_card(**base)


# ---------------------------------------------------------------- 建卡与结构

def test_create_three_arms():
    card = _card(adversary_diff={"criteria/rg.md": "反向改动"})
    assert set(card.arms) == {"baseline", "candidate", "adversary"}
    assert card.status == "draft"
    assert card.verifier


def test_create_requires_hypothesis_and_verifier():
    with pytest.raises(E.CardError):
        E.create_card(hypothesis="", candidate_diff={"a": "b"}, verifier=["v"])
    with pytest.raises(E.CardError):
        E.create_card(hypothesis="h", candidate_diff={"a": "b"}, verifier=[])


def test_create_requires_candidate():
    with pytest.raises(E.CardError):
        E.create_card(hypothesis="h", candidate_diff=None, verifier=["v"])


# ---------------------------------------------------------------- Z5 前置（防穿越进不了实验）

def test_candidate_diff_with_future_column_blocked():
    with pytest.raises(E.CardError) as ei:
        _card(candidate_diff={"forward_return": "加一列未来收益"})
    assert "lookahead" in str(ei.value)


def test_candidate_diff_with_leak_path_blocked():
    with pytest.raises(E.CardError):
        _card(candidate_diff={"skill": "/data/.grader/rules.py"})


def test_clean_diff_passes():
    card = _card(candidate_diff={"skills/scanner/SKILL.md": "收紧阈值描述"})
    assert card.arms["candidate"].diff


# ---------------------------------------------------------------- 对比与结案

def test_compare_marks_better_by_direction():
    card = _card()
    E.record_metrics(card, "baseline", {"misblock_rate_lower_better": 0.18, "oos_return": 0.004})
    E.record_metrics(card, "candidate", {"misblock_rate_lower_better": 0.11, "oos_return": 0.009})
    t = E.compare(card)
    assert t["candidate_better"] is True
    assert t["arms"]["candidate"]["misblock_rate_lower_better"]["better"] is True
    assert t["arms"]["candidate"]["oos_return"]["delta_vs_baseline"] == pytest.approx(0.005)


def test_compare_requires_baseline_metrics():
    card = _card()
    E.record_metrics(card, "candidate", {"x": 1})
    with pytest.raises(E.CardError):
        E.compare(card)


def test_decide_requires_signature():
    card = _card()
    E.record_metrics(card, "baseline", {"x": 1})
    E.record_metrics(card, "candidate", {"x": 2})
    with pytest.raises(E.SignatureRequired):
        E.decide(card, adopt=True, signed_by="")


def test_adopt_without_evidence_rejected():
    card = _card()
    E.record_metrics(card, "baseline", {"x": 2})
    E.record_metrics(card, "candidate", {"x": 1})  # 更差
    with pytest.raises(E.CardError):
        E.decide(card, adopt=True, signed_by="laotian")


def test_adopt_creates_experience_candidate():
    card = _card()
    E.record_metrics(card, "baseline", {"x": 1})
    E.record_metrics(card, "candidate", {"x": 2})
    v = E.decide(card, adopt=True, signed_by="laotian", reason="x +1")
    assert card.status == "adopted"
    assert v["adopt"] is True
    ec = card.experience_candidate
    assert set(ec) >= {"cue", "lesson", "evidence", "confidence", "signed_by"}
    assert "Z4" in ec["note"]


def test_reject_also_records_candidate():
    card = _card()
    E.record_metrics(card, "baseline", {"x": 2})
    E.record_metrics(card, "candidate", {"x": 1})
    E.decide(card, adopt=False, signed_by="laotian", reason="变差")
    assert card.status == "rejected"
    assert card.experience_candidate["confidence"] == 0.4


# ---------------------------------------------------------------- 表格与落盘

def test_markdown_table_contains_arms():
    card = _card(adversary_diff={"criteria/rg.md": "反向"})
    E.record_metrics(card, "baseline", {"x": 1})
    E.record_metrics(card, "candidate", {"x": 2})
    E.record_metrics(card, "adversary", {"x": 1.5})
    md = E.to_markdown(card)
    for arm in ("baseline", "candidate", "adversary"):
        assert arm in md
    assert "核验项" in md


def test_save_and_reload(tmp_path):
    card = _card()
    E.record_metrics(card, "baseline", {"x": 1})
    p = E.save(card, root=tmp_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["card_id"] == card.card_id
    assert data["arms"]["candidate"]["diff"]


def test_demo_runs_end_to_end(capsys):
    assert E._demo() == 0
    out = capsys.readouterr().out
    assert "对照实验" in out and "experience_candidate" in out
