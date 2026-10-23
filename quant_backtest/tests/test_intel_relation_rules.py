"""K-MDC-D2 tests: relation rules — candidate-only 铁律与三型覆盖."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_relation_rules import (  # noqa: E402
    RelationRejected, batch_co_occurrence, co_occurrence, holding, supply_chain,
)


def test_co_occurrence_generates_question_not_fact():
    c = co_occurrence("作手新一席位", "陈小群席位", "600XXX.SH", "20261021", "lhb:1")
    assert c.rel_type == "co_occurrence"
    assert "是否同一操盘团体" in c.question and c.basis.startswith("lhb_same_day")
    assert "坐实" not in c.question and "证实" not in c.question


def test_holding_requires_sane_ratio():
    c = holding("p:张三", "o:某某公司", "20260930", 5.2, "top10:1")
    assert "一致行动" in c.question and "人审" in c.question
    for bad in (0, 100.1, -1, True):
        with pytest.raises(RelationRejected, match="ratio"):
            holding("p:x", "o:y", "d", bad, "k")


def test_supply_chain_words_are_not_facts():
    c = supply_chain("o:英伟达链主", "o:供应商X", "20261020", "ann:1")
    assert "公告提及" in c.question and "实质业务往来" in c.question


def test_batch_pairs_same_day_same_stock_only():
    rows = [
        {"trade_date": "20261021", "ts_code": "600001.SH", "seat": "席位A", "evidence_key": "k1"},
        {"trade_date": "20261021", "ts_code": "600001.SH", "seat": "席位B", "evidence_key": "k1"},
        {"trade_date": "20261021", "ts_code": "600002.SH", "seat": "席位A", "evidence_key": "k2"},
        {"trade_date": "20261022", "ts_code": "600001.SH", "seat": "席位A", "evidence_key": "k3"},
    ]
    out = batch_co_occurrence(rows)
    assert len(out) == 1  # 只有 (1021,600001) 同日同票两席成对
    assert {out[0].left, out[0].right} == {"席位A", "席位B"}


def test_batch_big_group_collapses_to_one_question():
    rows = [{"trade_date": "20261021", "ts_code": "600009.SH",
             "seat": f"席位{i}", "evidence_key": "k"} for i in range(6)]
    out = batch_co_occurrence(rows)
    assert len(out) == 1 and "6席群体" in out[0].right and "群体行为" in out[0].question


def test_batch_rejects_missing_fields():
    with pytest.raises(RelationRejected, match="missing"):
        batch_co_occurrence([{"trade_date": "20261021", "ts_code": "x"}])


def test_self_co_occurrence_rejected_and_type_guard():
    with pytest.raises(RelationRejected, match="self"):
        co_occurrence("A", "A", "c", "d", "k")
    from intel_relation_rules import RelationCandidate
    with pytest.raises(RelationRejected, match="not supported"):
        RelationCandidate("causal", "a", "b", "k", "q", "basis")
