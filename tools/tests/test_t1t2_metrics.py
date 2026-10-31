# -*- coding: utf-8 -*-
"""tests for tools/t1t2_metrics.py（T1/T2 A/B 指标只读聚合，不造数）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "t1t2_metrics.py"
spec = importlib.util.spec_from_file_location("t1t2_metrics", MOD)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

_RUNS = {
    "baseline": {"run_ids": ["base01"], "label": "基线", "source": "test"},
    "variant": {"run_ids": ["var01"], "label": "变体", "source": "test"},
}
_FULL = {
    "overallTrend": "up", "selectedIndicators": [], "keySignals": ["x"],
    "supportLevel": 1.0, "resistanceLevel": 2.0, "confidence": 0.6,
    "summaryTable": [],
}


def _node(agent, complete=True, duration=1000.0, chars=100):
    return {"run_id": "r", "node_key": "%s_market" % agent.lower(),
            "agent_id": agent, "execution_mode": "normal" if complete else "degraded",
            "status": "normal" if complete else "degraded",
            "duration_ms": duration, "output": "x" * chars,
            "structured": dict(_FULL) if complete else {"error": "no_json_block_found"}}


def _write_run(data_dir, run_id, nodes, total_ms=None):
    path = Path(data_dir) / ("%s.jsonl" % run_id)
    path.write_text("\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
                    encoding="utf-8")
    summary = {"duration_ms_total": total_ms if total_ms is not None
               else sum(n.get("duration_ms", 0) for n in nodes)}
    (Path(data_dir) / ("%s_summary.json" % run_id)).write_text(
        json.dumps(summary), encoding="utf-8")


def test_missing_data_is_unavailable(tmp_path):
    rep = m.aggregate(tmp_path, runs=_RUNS)
    assert rep["available"] is False
    assert "baseline_trace_missing" in rep["unavailable_reasons"]
    assert rep["unavailable_fields"]
    assert rep["gate"]["passed"] is None


def test_baseline_only_partial_metrics_no_fabrication(tmp_path):
    _write_run(tmp_path, "base01", [_node("T1", complete=False, duration=120000),
                                    _node("T2", complete=True, duration=100000)])
    rep = m.aggregate(tmp_path, runs=_RUNS)
    assert rep["available"] is False
    base = rep["arms"]["baseline"]
    assert base["runs_found"] == ["base01"]
    assert base["json_complete_rate"] == 0.5
    assert base["mean_latency_ms"] == 110000.0
    # 变体无 trace：不得凭空产生指标
    assert rep["arms"]["variant"]["json_complete_rate"] is None
    assert rep["arms"]["variant"]["runs_found"] == []
    assert any("variant_trace_missing:variant" == r
               for r in rep["unavailable_reasons"])


def test_full_ab_gate_pass(tmp_path):
    _write_run(tmp_path, "base01", [_node("T1", complete=False, duration=120000),
                                    _node("T2", complete=False, duration=100000)])
    _write_run(tmp_path, "var01", [_node("T1", True, 100000), _node("T2", True, 90000),
                                   _node("T1", True, 90000)])
    rep = m.aggregate(tmp_path, runs=_RUNS)
    assert rep["available"] is True
    assert rep["unavailable_reasons"] == []
    var = rep["arms"]["variant"]
    assert var["json_complete_rate"] == 1.0
    assert var["mean_latency_ms"] == round((100000 + 90000 + 90000) / 3, 1)
    assert rep["gate"]["passed"] is True
    assert rep["gate"]["baseline_mean_latency_ms"] == 110000.0


def test_full_ab_gate_fail_on_json_rate(tmp_path):
    _write_run(tmp_path, "base01", [_node("T1", True, 100000)])
    # 3 节点中 1 个不完整 → 2/3 = 0.667 < 0.90
    _write_run(tmp_path, "var01", [_node("T1", True, 90000), _node("T2", True, 90000),
                                   _node("T2", False, 90000)])
    rep = m.aggregate(tmp_path, runs=_RUNS)
    assert rep["available"] is True
    check = rep["gate"]["checks"][0]
    assert check["passed"] is False
    assert check["json_complete_rate_ok"] is False
    assert rep["gate"]["passed"] is False


def test_full_ab_gate_fail_on_latency(tmp_path):
    _write_run(tmp_path, "base01", [_node("T1", True, 100000)])
    _write_run(tmp_path, "var01", [_node("T1", True, 150000)])
    rep = m.aggregate(tmp_path, runs=_RUNS)
    check = rep["gate"]["checks"][0]
    assert check["json_complete_rate_ok"] is True
    assert check["latency_not_worse"] is False
    assert check["passed"] is False


def test_node_extraction_ignores_non_t1t2_nodes(tmp_path):
    extra = [{"agent_id": "N1", "node_key": "n1_news", "execution_mode": "normal",
              "duration_ms": 10000, "output": "y"}]
    _write_run(tmp_path, "base01", [_node("T1", True, 100000)] + extra)
    rep = m.aggregate(tmp_path, runs={"baseline": {"run_ids": ["base01"]}},
                      )
    assert rep["arms"]["baseline"]["runs"][0]["t1t2_node_count"] == 1

