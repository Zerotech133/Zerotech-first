# -*- coding: utf-8 -*-
"""WO-LEGION-S25 Z3 匿名横评测试。

验收（《赵云进化层V3》Z3）：一次真实横评，输出采纳/否决**建议**（决定仍须签字）。
另覆盖：匿名期不泄来源、解盲映射、与 Z2 联动、评审者不得结案。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))
sys.path.insert(0, str(_PLATFORM / "engine"))

from engine import anonymous_review as R  # noqa: E402
from engine import experiment_card as E  # noqa: E402


def _card():
    card = E.create_card(
        hypothesis="候选判据是否优于基线",
        candidate_diff={"criteria/rg.md": "样本外验证条款"},
        adversary_diff={"criteria/rg.md": "反向改动"},
        verifier=["样本外达标", "回滚预案"],
    )
    E.record_metrics(
        card, "baseline", {"misblock_lower_better": 0.18, "oos_return": 0.004}
    )
    E.record_metrics(
        card, "candidate", {"misblock_lower_better": 0.11, "oos_return": 0.009}
    )
    E.record_metrics(
        card, "adversary", {"misblock_lower_better": 0.17, "oos_return": 0.005}
    )
    return card


# ---------------------------------------------------------------- 组池与匿名


def test_pool_excludes_baseline():
    pool = R.build_pool(_card())
    origins = {c.origin for c in pool}
    assert origins == {"candidate", "adversary"}
    assert all(c.anon_id == "" for c in pool)


def test_pool_requires_metrics():
    card = E.create_card(hypothesis="h", candidate_diff={"a": "b"}, verifier=["v"])
    with pytest.raises(R.ReviewError):
        R.build_pool(card)


def test_anon_ids_hide_origin():
    card = _card()
    pool = R.assign_anon_ids(R.build_pool(card), salt=card.card_id)
    for c in pool:
        assert c.anon_id.startswith("C-") and len(c.anon_id) == 8
        assert "candidate" not in c.anon_id.lower()
        assert "adversary" not in c.anon_id.lower()


def test_anon_ids_deterministic():
    card = _card()
    a = [c.anon_id for c in R.assign_anon_ids(R.build_pool(card), salt=card.card_id)]
    b = [c.anon_id for c in R.assign_anon_ids(R.build_pool(card), salt=card.card_id)]
    assert a == b


def test_anon_ids_differ_across_cards():
    c1, c2 = _card(), _card()
    a = [x.anon_id for x in R.assign_anon_ids(R.build_pool(c1), salt=c1.card_id)]
    b = [x.anon_id for x in R.assign_anon_ids(R.build_pool(c2), salt=c2.card_id)]
    assert a != b


# ---------------------------------------------------------------- 横评


def test_review_winner_is_better_candidate():
    card = _card()
    r = R.review(card)
    assert len(r.scores) == 2
    assert r.ranking[0] == r.winner
    # 匿名期 rationale 只含匿名 ID
    for k in r.rationale:
        assert k.startswith("C-")
    assert not r.revealed


def test_winner_must_map_to_better_arm():
    """承重：candidate 两指标均优于 adversary → 解盲后胜出必须映射到 candidate。

    （变异「打分恒 0」必须让本用例转红，否则横评等于没排序。）
    """
    card = _card()
    r = R.review(card)
    m = R.reveal(r)
    assert m[r.winner] == "candidate"
    assert (
        r.scores[m and r.winner]
        > r.scores[[k for k, v in m.items() if v == "adversary"][0]]
    )


def test_review_rationale_no_origin_leak():
    card = _card()
    r = R.review(card)
    blob = " ".join(r.rationale.values()).lower()
    assert "candidate" not in blob and "adversary" not in blob


def test_reveal_maps_back():
    card = _card()
    r = R.review(card)
    m = R.reveal(r)
    assert set(m.values()) == {"candidate", "adversary"}
    assert r.revealed is True
    # 幂等
    assert R.reveal(r) == m


def test_custom_judge_injection():
    card = _card()

    def judge(pool, c):
        return ({p.anon_id: 1.0 for p in pool}, {p.anon_id: "custom" for p in pool})

    r = R.review(card, judge=judge)
    assert r.judge == "judge"
    assert len(r.scores) == 2


def test_baseline_metrics_required_for_judging():
    card = E.create_card(hypothesis="h", candidate_diff={"a": "b"}, verifier=["v"])
    E.record_metrics(card, "candidate", {"x": 1})
    with pytest.raises(R.ReviewError):
        R.review(card)


# ---------------------------------------------------------------- 与 Z2 联动 / 结案边界


def test_apply_to_card_does_not_adopt():
    card = _card()
    r = R.review(card)
    R.apply_to_card(card, r)
    rev = card.verdict["review"]
    assert rev["winner"] == r.winner
    assert "adopt" not in card.verdict
    assert card.status == "probing"  # 未结案
    assert rev["revealed"] is False


def test_review_alone_cannot_close_case():
    """评审者不得结案：review 结果不含签字，decide 仍需 signed_by。"""
    card = _card()
    R.apply_to_card(card, R.review(card))
    with pytest.raises(E.SignatureRequired):
        E.decide(card, adopt=True, signed_by="")


def test_revealed_flag_carried_into_card():
    card = _card()
    r = R.review(card)
    R.reveal(r)
    R.apply_to_card(card, r)
    assert card.verdict["review"]["revealed"] is True
    assert card.verdict["review"]["reveal_map"]


def test_report_marks_unrevealed():
    card = _card()
    r = R.review(card)
    md = R.report_markdown(card, r)
    assert "未解盲" in md and "建议" in md
    R.reveal(r)
    assert "已解盲" in R.report_markdown(card, r)


def test_demo_runs(capsys):
    assert R._demo() == 0
    out = capsys.readouterr().out
    assert "匿名横评" in out and "建议胜出" in out
