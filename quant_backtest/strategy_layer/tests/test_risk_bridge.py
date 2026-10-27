# -*- coding: utf-8 -*-
"""risk_bridge 单测：账本聚合/快照解析/降级语义（K-STOCK-421）。"""

import json

import pytest

from strategy_layer import risk_bridge


def _write_ledger(tmp_path, rows):
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return str(p)


def _row(rid, action, ts, score=0.5, reason=""):
    return {
        "ts": ts,
        "rule_id": rid,
        "action": action,
        "mode": "shadow",
        "score": score,
        "security": "000001.SZ",
        "reason": reason,
    }


def test_agg_rule_counts_and_market_gate(tmp_path):
    rows = [
        _row("R1-market-quality", "pass", "2026-10-10 19:10:00", 0.4),
        _row(
            "R1-market-quality",
            "shadow_block",
            "2026-10-11 19:10:00",
            0.05,
            "健康占比0.05; 滚动P10分位阈0.31; 市场级闸(低健康分位日)",
        ),
        _row("R2-liquidity", "pass", "2026-10-11 19:10:01", 0.6),
    ]
    p = _write_ledger(tmp_path, rows)
    tail = risk_bridge._parse(risk_bridge._tail_lines(p))
    r1 = risk_bridge._agg_rule(tail, "R1-market-quality")
    assert r1["pass"] == 1 and r1["block"] == 1
    assert r1["market_gate"]["action"] == "shadow_block"
    assert r1["market_gate"]["score"] == 0.05
    r2 = risk_bridge._agg_rule(tail, "R2-liquidity")
    assert r2["pass"] == 1 and r2["market_gate"] is None


def test_risk_context_missing_ledger_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(
        risk_bridge, "LEDGER_PATH", str(tmp_path / "no_such_ledger.jsonl")
    )
    monkeypatch.setattr(risk_bridge, "SNAPSHOT_GLOB", str(tmp_path / "no_snap_*.csv"))
    ctx = risk_bridge.risk_context()
    assert ctx["available"] is False
    assert ctx["snapshot"]["exhausted"] == []


def test_risk_context_happy_path(tmp_path, monkeypatch):
    rows = [_row("R1-market-quality", "pass", "2026-10-17 19:10:00", 0.35)]
    monkeypatch.setattr(risk_bridge, "LEDGER_PATH", _write_ledger(tmp_path, rows))
    snap = tmp_path / "z4-fourstate-snapshot-20261017.csv"
    snap.write_text(
        "\ufeffsecurity,state,n_sup,n_con,density,density_h1,"
        "density_h2,n_hyp,basis\n"
        '000001.SZ,衰竭,3,9,0.75,0.9,0.6,1,"x"\n'
        '000002.SZ,酝酿,0,0,0.4,0.7,0.1,0,"y"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(risk_bridge, "SNAPSHOT_GLOB", str(snap))
    ctx = risk_bridge.risk_context()
    assert ctx["available"] is True
    assert ctx["snapshot"]["day"] == "20261017"
    assert ctx["snapshot"]["exhausted_n"] == 1
    assert ctx["snapshot"]["exhausted"][0]["security"] == "000001.SZ"


def test_bad_json_lines_skipped(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        "{broken\n"
        + json.dumps(
            _row("R2-liquidity", "pass", "2026-10-17 19:10:00"), ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    tail = risk_bridge._parse(risk_bridge._tail_lines(str(p)))
    assert len(tail) == 1


def test_reject_path_traversal_still_safe(tmp_path):
    # 尾部读取对任意路径都只读不写；缺失一律降级
    monkey = pytest.MonkeyPatch()
    monkey.setattr(risk_bridge, "LEDGER_PATH", str(tmp_path / ".." / "x"))
    ctx = risk_bridge.risk_context()
    monkey.undo()
    assert ctx["available"] is False
