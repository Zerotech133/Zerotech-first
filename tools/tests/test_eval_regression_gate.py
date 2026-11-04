# -*- coding: utf-8 -*-
"""tools/tests/test_eval_regression_gate.py · 评测回归门禁（离线）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "finetune"))

import eval_regression_gate as g  # noqa: E402


def _payload(**over):
    agg = {"citation_accuracy": 0.90, "groundedness": 0.65,
           "correct_refusal_rate": 1.0, "overclaim_rate": 0.0, "length_anomaly_rate": 0.0}
    agg.update(over)
    return {"aggregate": agg}


def test_thresholds_pass():
    r = g.evaluate(_payload())
    assert r["ok"] is True
    assert all(c["pass"] for c in r["checks"])


def test_thresholds_fail_citation():
    r = g.evaluate(_payload(citation_accuracy=0.80))
    assert r["ok"] is False
    bad = [c for c in r["checks"] if c["kind"] == "threshold" and not c["pass"]]
    assert bad and bad[0]["metric"] == "citation_accuracy"


def test_regression_detected():
    base = _payload(citation_accuracy=0.90, groundedness=0.66)
    new = _payload(citation_accuracy=0.85, groundedness=0.66)
    r = g.evaluate(new, base)
    assert r["ok"] is False
    assert any(c["kind"] == "regression" and c["metric"] == "citation_accuracy" for c in r["checks"])


def test_regression_within_tolerance_pass():
    base = _payload(citation_accuracy=0.90)
    new = _payload(citation_accuracy=0.89)
    r = g.evaluate(new, base, tolerance=0.02)
    assert r["ok"] is True


def test_lower_better_metric_regression():
    base = _payload(overclaim_rate=0.0)
    new = _payload(overclaim_rate=0.04)
    r = g.evaluate(new, base)
    assert r["ok"] is False
    assert any(c["kind"] == "regression" and c["metric"] == "overclaim_rate" for c in r["checks"])


def test_main_cli_exit_code(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_payload()), encoding="utf-8")
    assert g.main(["--payload", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_payload(citation_accuracy=0.5)), encoding="utf-8")
    assert g.main(["--payload", str(bad)]) == 1
