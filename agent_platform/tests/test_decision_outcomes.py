# -*- coding: utf-8 -*-
"""D1 决策 outcome 台账单测（不联网）。"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PLATFORM = HERE.parent
sys.path.insert(0, str(PLATFORM))

spec = importlib.util.spec_from_file_location("decision_outcomes", PLATFORM / "decision_outcomes.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BARS = [
    ["2026-10-27", "1", "100", "1", "1", "1"],
    ["2026-10-28", "1", "102", "1", "1", "1"],
    ["2026-10-29", "1", "104", "1", "1", "1"],
]


def _row(**kw):
    r = {"status": "done", "date": "2026-10-27", "symbol": "600519",
         "run_id": "r1", "price_at_decision": "100", "recommendation": "BUY",
         "confidence": 0.8, "latency_s": 10.0, "parse_ok": True,
         "model": "glm/glm-5.3", "fallback": False}
    r.update(kw)
    return r


def test_first_next_bar():
    assert m.first_next_bar(BARS, "2026-10-27") == ("2026-10-28", 102.0)
    assert m.first_next_bar(BARS, "2026-10-29") == (None, None)


def test_buy_positive_hit():
    o = m.compute_outcome(_row(), lambda s, n: BARS)
    assert o["hit"] == 1
    assert abs(o["next_day_return"] - 0.02) < 1e-9
    assert o["model"] == "glm/glm-5.3" and o["fallback"] is False


def test_buy_negative_miss():
    bars = [["2026-10-27", "1", "100", "1", "1", "1"],
            ["2026-10-28", "1", "98", "1", "1", "1"]]
    assert m.compute_outcome(_row(), lambda s, n: bars)["hit"] == 0


def test_sell_inverse_hit():
    bars = [["2026-10-27", "1", "100", "1", "1", "1"],
            ["2026-10-28", "1", "98", "1", "1", "1"]]
    assert m.compute_outcome(_row(recommendation="SELL"), lambda s, n: bars)["hit"] == 1


def test_watch_hit_none():
    assert m.compute_outcome(_row(recommendation="WATCH"), lambda s, n: BARS)["hit"] is None


def test_missing_or_no_next_bar():
    assert m.compute_outcome(_row(price_at_decision=None), lambda s, n: BARS) is None
    assert m.compute_outcome(_row(date="2026-10-30"), lambda s, n: BARS) is None


def test_compute_all_dedupe():
    existing = {"r1"}
    out = m.compute_all([_row()], lambda s, n: BARS, existing)
    assert out == []


def test_summarize_metrics():
    ledger = [_row(run_id="a"), _row(run_id="b", recommendation="WATCH", confidence=0.4),
              _row(run_id="c", parse_ok=False, recommendation=None, confidence=None)]
    out = [
        {"run_id": "a", "recommendation": "BUY", "confidence": 0.8, "hit": 1,
         "next_day_return": 0.02},
        {"run_id": "b", "recommendation": "WATCH", "confidence": 0.4, "hit": None,
         "next_day_return": -0.01},
    ]
    s = m.summarize(out, ledger, week="2026-W44")
    assert s["ledger_done"] == 3 and s["outcomes"] == 2
    assert s["coverage"] == round(2 / 3, 3)
    assert s["parse_ok_rate"] == round(2 / 3, 3)
    assert s["directional_n"] == 1 and s["hit_rate"] == 1.0
    assert s["brier"] == round((0.8 - 1) ** 2, 4)
    assert s["watch_ratio"] == round(1 / 3, 3)
    assert s["models"].get("glm/glm-5.3") == 3
    assert s["fallback"]["false"] == 3


def test_render_markdown():
    s = m.summarize([], [], week="2026-W44")
    md = m.render_markdown(s)
    assert "D1 决策 outcome" in md
    assert "不阻断" in md


def test_build_ledger_row_model_fallback():
    import run_shadow

    r = run_shadow.build_ledger_row(
        "2026-10-19", "600519", "贵州茅台", "rid", 1500.0,
        {"recommendation": "BUY", "confidence": 0.8, "score": 0.8}, True,
        {"web": "normal"}, 3.2, model="glm/glm-5.3", fallback=True,
    )
    assert r["model"] == "glm/glm-5.3" and r["fallback"] is True


def test_cli_compute_and_report_flags(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text("", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    assert m.main(["--compute", "--no-write", "--ledger", str(led),
                   "--out", str(out)]) == 0
    assert m.main(["--report", "--no-write", "--ledger", str(led),
                   "--out", str(out)]) == 0
