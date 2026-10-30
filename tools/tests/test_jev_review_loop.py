# -*- coding: utf-8 -*-
"""JEV review 闭环纯函数单测。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_review_loop as rl  # noqa: E402


def test_validate_label_ok_and_bad():
    row = rl.validate_label({"news_id": "n1", "human_bias": "BEAR", "horizon": "days"})
    assert row["human_bias"] == "bear" and row["horizon"] == "days"
    for bad in ({}, {"news_id": "n1"}, {"news_id": "n1", "human_bias": "strong_buy"}):
        try:
            rl.validate_label(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("应拒绝: %r" % (bad,))


def test_agreement_metrics():
    labels = [
        {"model_bias": "bull", "human_bias": "bull"},
        {"model_bias": "bull", "human_bias": "bear"},
        {"model_bias": "neutral", "human_bias": "neutral"},
    ]
    r = rl.agreement(labels)
    assert r["n"] == 3 and r["agreement"] == round(2 / 3, 4)
    assert r["directional_n"] == 2 and r["directional_agreement"] == 0.5
    assert r["by_model_bias"]["bull"]["n"] == 2


def test_ingest_idempotent(tmp_path):
    labels = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    labels.write_text('{"news_id":"a","human_bias":"bull"}\n'
                      '{"news_id":"b","human_bias":"bear"}\n', encoding="utf-8")
    r1 = rl.ingest(labels, out)
    r2 = rl.ingest(labels, out)
    assert r1["recorded"] == 2 and r2["recorded"] == 0 and r2["skipped"] == 2
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


def test_cli_entry_present():
    src = Path(rl.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src