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


def test_main_rerun_keeps_1096_cards_and_merge_info(tmp_path, monkeypatch):
    """重跑生成器：已导入的 1096 张卡不减少，且 merge_info 批次台账保留。"""
    import json
    cards = [{"concept": f"C{i}", "q": f"q{i}", "a": f"a{i}", "source": "s", "topic": "交易体系", "evidence": "e"} for i in range(1096)]
    merge_info = {
        "video_corpus_top100_20261108": {"count": 72},
        "video_corpus_batch28_20261109": {"count": 81},
        "video_corpus_gapfill_20261109": {"count": 54},
        "video_corpus_mix500_20261110": {"count": 383},
        "chanlun108_20261111": {"count": 106},
    }
    out = tmp_path / "kb_learning_cards.json"
    out.write_text(json.dumps({"cards": cards, "merge_info": merge_info}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(lc, "OUT_JSON", out)
    monkeypatch.setattr(lc, "CARDS_DIR", tmp_path / "cards")
    # 生成卡与现有卡同 concept，确保不新增（重跑后应恰好 1096）
    monkeypatch.setattr(lc, "load_units", lambda: [{"book": "b", "chapter": "c", "concept": "C0", "claim": "x", "evidence": "e"}])
    assert lc.main(["--n", "1"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert len(doc["cards"]) == 1096
    assert doc["merge_info"] == merge_info
    assert [c["q"] for c in doc["cards"][:3]] == ["q0", "q1", "q2"]
