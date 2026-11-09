# -*- coding: utf-8 -*-
"""D1 决策 outcome 台账单测（不联网）。

覆盖：first_next_bar/五档 hit/去重/指标渲染/账本 model-fallback/CLI 兼容；
K-ORG-20261031-D1WRITER 增量：decision_id（与 engine/decision_policy 同公式）、
legacy 行（无 decision_id）兼容、--no-write 不写、写后按 run_id 幂等、
不伪造 hit/model。
"""
import hashlib
import importlib.util
import json
import pathlib
import sys


HERE = pathlib.Path(__file__).resolve().parent
PLATFORM = HERE.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import decision_policy as policy  # noqa: E402

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


# ---------------------------------------------------------------- decision_id
DECISION_CASES = [
    ("600519", "2026-10-30", "df51a1059157"),
    ("000858", "2026-10-27", "5d66cc48a2f2"),
    ("300324", "2026-10-30", "run-x-001"),
]


def test_decision_id_matches_policy_single_source():
    """outcome 的 decision_id 必须与 engine/decision_policy 单一真源完全一致。"""
    for symbol, date, run_id in DECISION_CASES:
        assert m.decision_id(symbol, date, run_id) == policy.decision_id(
            symbol, date, run_id)
    # 与 W3 预检已复算的锚点一致（task-17 D1-20261030-6d46c0beb1）
    assert m.decision_id("600519", "2026-10-30", "df51a1059157") == \
        "D1-20261030-6d46c0beb1"
    expected = "D1-%s-%s" % (
        "20261030",
        hashlib.sha256(b"600519|2026-10-30|df51a1059157").hexdigest()[:10])
    assert m.decision_id("600519", "2026-10-30", "df51a1059157") == expected


def test_fallback_decision_id_matches_policy():
    """engine 不可用时的回退实现必须与 policy 同公式（防止双实现漂移）。"""
    for symbol, date, run_id in DECISION_CASES:
        assert m._fallback_decision_id(symbol, date, run_id) == \
            policy.decision_id(symbol, date, run_id)


def test_compute_outcome_carries_decision_id():
    o = m.compute_outcome(_row(), lambda s, n: BARS)
    assert o["decision_id"] == m.decision_id("600519", "2026-10-27", "r1")
    # 原有 hit/model 语义不变
    assert o["hit"] == 1
    assert o["model"] == "glm/glm-5.3" and o["fallback"] is False


def test_legacy_outcome_row_without_decision_id_compatible(tmp_path):
    """历史 outcome 行（无 decision_id）可读、可统计、可幂等去重，不重写、不崩。"""
    old_row = {"ts": 1.0, "run_id": "r0", "date": "2026-10-20",
               "symbol": "600519", "recommendation": "WATCH",
               "hit": None, "model": "glm/glm-5.3"}
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps(old_row, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    rows = m._load_jsonl(str(out))
    assert len(rows) == 1 and rows[0]["run_id"] == "r0"
    assert "decision_id" not in rows[0]
    summary = m.summarize(rows, [_row(run_id="r0")], week="2026-W43")
    assert summary["outcomes"] == 1
    existing = {str(r["run_id"]) for r in rows}
    assert m.compute_all([_row(run_id="r0")], lambda s, n: BARS, existing) == []
    assert out.read_text(encoding="utf-8").count("\n") == 1  # 未重写


def test_no_write_does_not_create_out_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_default_price_fetcher", lambda symbol, n=60: BARS)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(_row(), ensure_ascii=False) + "\n",
                      encoding="utf-8")
    out = tmp_path / "out.jsonl"
    assert m.main(["--no-write", "--ledger", str(ledger),
                   "--out", str(out)]) == 0
    assert not out.exists(), "--no-write 不得创建/写 sidecar"


def test_no_write_preserves_existing_out_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_default_price_fetcher", lambda symbol, n=60: BARS)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(_row(run_id="r2"), ensure_ascii=False) + "\n",
                      encoding="utf-8")
    out = tmp_path / "out.jsonl"
    out.write_text('{"run_id": "legacy", "decision_id": "legacy-kept"}\n',
                   encoding="utf-8")
    before = out.read_bytes()
    assert m.main(["--no-write", "--ledger", str(ledger),
                   "--out", str(out)]) == 0
    assert out.read_bytes() == before


def test_write_appends_decision_id_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_default_price_fetcher", lambda symbol, n=60: BARS)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(_row(), ensure_ascii=False) + "\n",
                      encoding="utf-8")
    out = tmp_path / "out.jsonl"
    assert m.main(["--ledger", str(ledger), "--out", str(out)]) == 0
    rows = m._load_jsonl(str(out))
    assert len(rows) == 1
    assert rows[0]["decision_id"] == m.decision_id("600519", "2026-10-27", "r1")
    # 同一 run_id 再跑：幂等，不重复追加
    assert m.main(["--ledger", str(ledger), "--out", str(out)]) == 0
    assert len(m._load_jsonl(str(out))) == 1


def test_no_fabricated_hit_or_model():
    """无方向裁决 hit 必须 null；缺 model 不得补默认值（decision_id 仍应落）。"""
    for rec in ("HOLD", "WATCH", ""):
        o = m.compute_outcome(_row(recommendation=rec), lambda s, n: BARS)
        assert o["hit"] is None, rec
        assert o["decision_id"], rec
    row = _row()
    row.pop("model")
    row.pop("fallback")
    o = m.compute_outcome(row, lambda s, n: BARS)
    assert o["decision_id"]
    assert o["model"] is None and o["fallback"] is None
