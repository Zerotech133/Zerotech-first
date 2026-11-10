# -*- coding: utf-8 -*-
"""score_guard 测试（B1 评分口径强制，2026-10-26）。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import score_guard as g  # noqa: E402
import score_registry as sr  # noqa: E402


def test_core_is_five_and_registered():
    ids = g.core_ids()
    assert len(ids) == 5, ids
    assert set(ids) == {
        "rps50",
        "nine_dim_score",
        "jev_signal",
        "chan_signal",
        "kg_resonance",
    }
    for mid in ids:
        assert sr.get(mid), mid


def test_full_core_annotations_ok():
    anns = [sr.annotate(mid, 1, as_of="2026-10-26 15:00") for mid in g.core_ids()]
    r = g.validate_items(anns)
    assert r["ok"], r
    assert not r["missing"]


def test_missing_core_detected():
    anns = [sr.annotate(mid, 1, as_of="2026-10-26 15:00") for mid in g.core_ids()][1:]
    r = g.validate_items(anns)
    assert not r["ok"]
    assert set(r["missing"]) == {g.core_ids()[0]}


def test_unregistered_metric_rejected():
    r = g.validate_items([{"metric_id": "nope", "value": 1}])
    assert not r["ok"] and r["errors"]


def test_push_forbidden_metric_rejected():
    # focus_p: allow_push=false
    ann = sr.annotate("focus_p", 0.03, as_of="2026-10-26")
    r = g.validate_items([ann], for_push=True)
    assert not r["ok"]
    assert any("allow_push" in e for e in r["errors"])


def test_decision_requires_allow_decision_and_ab():
    ann = sr.annotate("rps50", 88, as_of="2026-10-26")
    r = g.validate_items([ann], for_decision=True, require_all_core=False)
    assert r["ok"], r
    c = sr.annotate("jev_signal", 0.5, as_of="2026-10-26")
    r2 = g.validate_items([c], for_decision=True, require_all_core=False)
    assert not r2["ok"]  # C 级 + allow_decision=false


def test_render_and_parse_roundtrip():
    line = g.render_line("rps50", 88, as_of="2026-10-26 15:00")
    assert "RPS50" in line and "rps50" in line
    assert g.parse_registry_line(line) == "rps50"
