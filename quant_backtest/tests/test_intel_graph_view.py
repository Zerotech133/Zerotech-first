"""K-MDC-G2 tests: dossier query — edges traceable, truncation visible, candidate-only."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_graph_view import GraphView  # noqa: E402
from intel_relation_rules import RelationCandidate, RelationRejected, co_occurrence, holding  # noqa: E402


def build() -> GraphView:
    g = GraphView()
    g.add(co_occurrence("席位A", "席位B", "600001.SH", "20261021", "lhb:1"), stock_code="600001.SH")
    g.add(holding("p:张三", "o:某国资", "20260930", 5.0, "top10:1"), stock_code="600001.SH")
    g.add(co_occurrence("席位C", "席位D", "600002.SH", "20261021", "lhb:2"), stock_code="600002.SH")
    return g


def test_dossier_returns_edges_with_traceable_source():
    d = build().dossier("600001.SH")
    assert d["total_edges"] == 2 and d["verdict"] == "candidate_only"
    for e in d["edges"]:
        assert e["evidence_key"] and e["question"]  # 每边可回源可调查


def test_dossier_empty_state_is_honest():
    d = build().dossier("600999.SH")
    assert d["total_edges"] == 0 and d["edges"] == [] and not d["truncated"]


def test_truncation_visible_at_20():
    g = GraphView()
    for i in range(25):
        g.add(RelationCandidate("co_occurrence", f"席位{i}", f"席位X{i}",
                                f"lhb:{i}", f"q{i}", f"basis{i}"), stock_code="600003.SH")
    d = g.dossier("600003.SH")
    assert d["total_edges"] == 25 and d["shown"] == 20 and d["truncated"] is True


def test_neighborhood_bidirectional():
    g = build()
    assert len(g.neighborhood("席位B")) == 1
    assert len(g.neighborhood("p:张三")) == 1
    assert g.neighborhood("无名氏") == []


def test_add_rejects_non_candidate_and_bad_stock():
    g = GraphView()
    with pytest.raises(RelationRejected, match="RelationCandidate"):
        g.add("not-a-candidate")
    with pytest.raises(RelationRejected, match="stock_code"):
        g.add(co_occurrence("A", "B", "c", "d", "k"), stock_code=" ")


def test_stats_counts():
    s = build().stats()
    assert s == {"edges": 3, "entities": 6, "stocks": 2}
