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
           "correct_refusal_rate": 1.0, "gold_similarity": 0.35,
           "overclaim_rate": 0.0, "length_anomaly_rate": 0.0,
           "questions_total": 40, "errors": 0}
    agg.update(over)
    return {"mode": "api", "aggregate": agg}


def test_thresholds_pass():
    r = g.evaluate(_payload())
    assert r["ok"] is True
    assert all(c["pass"] for c in r["checks"] if c["kind"] == "threshold")


def test_thresholds_fail_citation():
    r = g.evaluate(_payload(citation_accuracy=0.88))
    assert r["ok"] is False
    bad = [c for c in r["checks"] if c["kind"] == "threshold" and not c["pass"]]
    assert bad and bad[0]["metric"] == "citation_accuracy"


def test_gold_similarity_threshold():
    r = g.evaluate(_payload(gold_similarity=0.10))
    assert r["ok"] is False
    assert any(c["kind"] == "threshold" and c["metric"] == "gold_similarity" and not c["pass"]
               for c in r["checks"])


def test_dry_run_rejected():
    p = _payload()
    p["mode"] = "dry-run"
    r = g.evaluate(p)
    assert r["ok"] is False
    assert any(c["kind"] == "validity" and "mode" in c["detail"] for c in r["checks"])


def test_mock_rejected_by_default_and_allowed_with_flag():
    p = _payload()
    p["mode"] = "mock"
    assert g.evaluate(p)["ok"] is False
    r = g.evaluate(p, allow_mock=True)
    assert r["ok"] is True
    assert any("mock" in w for w in r["warnings"])


def test_small_sample_rejected():
    r = g.evaluate(_payload(questions_total=5))
    assert r["ok"] is False
    assert any(c["kind"] == "validity" and "questions_total" in c["detail"] for c in r["checks"])


def test_errors_rejected():
    r = g.evaluate(_payload(errors=2))
    assert r["ok"] is False
    assert any(c["kind"] == "validity" and "errors" in c["detail"] for c in r["checks"])


def test_regression_detected():
    base = _payload(citation_accuracy=0.95, groundedness=0.66)
    new = _payload(citation_accuracy=0.91, groundedness=0.66)
    r = g.evaluate(new, base)
    assert r["ok"] is False
    assert any(c["kind"] == "regression" and c["metric"] == "citation_accuracy" for c in r["checks"])


def test_regression_within_tolerance_pass():
    base = _payload(citation_accuracy=0.95)
    new = _payload(citation_accuracy=0.94)
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
