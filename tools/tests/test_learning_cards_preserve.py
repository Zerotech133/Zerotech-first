# -*- coding: utf-8 -*-
"""learning_cards.merge_cards：重跑生成器不得覆盖人工合并/修订卡。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("learning_cards", REPO / "tools/kb_corpus/learning_cards.py")
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)


def test_existing_cards_preserved():
    existing = [{"concept": "A", "q": "q1"}, {"concept": "A", "q": "q1-dup"}, {"concept": "B", "q": "q2"}]
    generated = [{"concept": "B", "q": "q2"}, {"concept": "C", "q": "q3"}]
    merged = lc.merge_cards(existing, generated)
    assert len(merged) == 4  # 现有 3 张全保留（含重复），新增 C
    assert [c["q"] for c in merged[:3]] == ["q1", "q1-dup", "q2"]
    assert merged[-1]["q"] == "q3"


def test_generated_all_existing_keeps_count():
    existing = [{"concept": f"C{i}", "q": f"q{i}"} for i in range(70)]
    generated = [{"concept": f"C{i}", "q": f"q{i}"} for i in range(20)]
    assert len(lc.merge_cards(existing, generated)) == 70


def test_no_existing_returns_generated():
    gen = [{"concept": "X", "q": "q"}]
    assert lc.merge_cards([], gen) == gen


def test_norm_concept_strips_whitespace_case():
    existing = [{"concept": "中大 盘股", "q": "q1"}]
    generated = [{"concept": "中大 盘股", "q": "q2"}]
    assert len(lc.merge_cards(existing, generated)) == 1
