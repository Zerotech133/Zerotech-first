# -*- coding: utf-8 -*-
"""JEV-Eval 纯函数单测（JEVEVAL）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_eval_harness as h  # noqa: E402


def _rep(gate=False, stale=1, nd=13):
    return {"data_through": "20261030", "stale_trade_days": stale, "n_trade_days": nd,
            "priced_total": 100, "assessment": {"directional": {
                "edge_vs_baseline": 0.05, "wilson95_lower": 0.52, "gate_pass": gate}}}


def test_calibration_levels():
    assert h.check_calibration(_rep(gate=True))["level"] == "PASS"
    assert h.check_calibration(_rep(gate=False))["level"] == "WARN"
    assert h.check_calibration(_rep(stale=3))["level"] == "WARN"
    assert h.check_calibration(_rep(stale=6))["level"] == "FAIL"


def test_prefilter_and_schema():
    rows = [{"id": "a", "title": "开盘涨停！000678", "source": "sina",
             "bias": "bull", "conf": 0.9, "event_type": "price"},
            {"id": "b", "title": "收盘：沪指涨 0.8%", "source": "sina",
             "bias": "neutral", "conf": 0.5, "event_type": "other"}]
    pf = h.check_prefilter(rows)
    assert pf["level"] in ("PASS", "WARN")

    good = {"jev": json.dumps({"horizon": "days",
                               "prob": {"bull": 0.2, "bear": 0.7, "neutral": 0.1}})}
    bad = {"jev": json.dumps({"horizon": "forever", "prob": {"bull": 1}})}
    s = h.check_judge_schema([good])
    assert s["level"] == "PASS"
    s2 = h.check_judge_schema([bad])
    assert s2["level"] == "WARN"          # 迁移期（0 覆盖）默认 WARN
    monkeypatch = None
    import os
    os.environ["JEV_EVAL_STRICT_SCHEMA"] = "1"
    try:
        assert h.check_judge_schema([bad])["level"] == "FAIL"
    finally:
        os.environ.pop("JEV_EVAL_STRICT_SCHEMA", None)


def test_summarize():
    checks = [{"name": "a", "level": "PASS", "detail": ""},
              {"name": "b", "level": "WARN", "detail": ""}]
    assert h.summarize(checks)["overall"] == "WARN"
    checks.append({"name": "c", "level": "FAIL", "detail": ""})
    assert h.summarize(checks)["overall"] == "FAIL"


def test_cli_entry_present():
    code = Path(h.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in code