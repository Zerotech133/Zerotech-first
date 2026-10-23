"""K-MDC-E2 tests: dossier endpoint — honest empty, candidate-only, traceable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "quant_backtest"))
from api_intel_graph import build_view, dossier  # noqa: E402


def test_dossier_returns_candidate_only_with_traceable_keys():
    g = build_view(limit=60)
    stats = g.stats()
    assert stats["edges"] > 0 and stats["stocks"] > 0
    some_stock = next(iter(g._by_stock))  # noqa: SLF001 — 测试取任一有边票码
    d = g.dossier(some_stock)
    assert d["verdict"] == "candidate_only" and d["total_edges"] >= 1
    for e in d["edges"]:
        assert e["evidence_key"] and e["question"]


def test_dossier_empty_is_honest_for_unknown_code():
    d = dossier("999999.XX")
    assert d["total_edges"] == 0 and d["edges"] == [] and not d["truncated"]


def test_bad_input_rejected_softly():
    assert dossier("")["error"]
    assert dossier(None)["error"]


def test_all_edges_carry_question_not_fact_words():
    g = build_view(limit=50)
    for idx_list in list(g._by_stock.values())[:5]:  # noqa: SLF001
        for i in idx_list:
            e = g._edges[i]  # noqa: SLF001
            assert e.question and ("是否" in e.question or "须" in e.question)
