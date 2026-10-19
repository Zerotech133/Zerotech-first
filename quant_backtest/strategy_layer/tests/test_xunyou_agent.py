# -*- coding: utf-8 -*-
"""荀攸盘后作业运行器单测（内存路径，绝不调用 run()/写库）。"""
import json

from strategy_layer.xunyou import build_pool

DAY = "20261019"


def row(symbol, action, score, conf=0.7, divergence=False, sup=0):
    contributions = {"chan_monitor": {"score": score, "weight": 0.5,
                                      "action": action}}
    if sup:
        contributions["chan_monitor"]["superseded"] = [
            {"action": "sell", "score": 20.0, "dt": "2026-07-06",
             "rationale": "旧卖"}] * sup
    return {"trade_date": DAY, "symbol": symbol, "action": action,
            "score": score, "confidence": conf,
            "divergence": divergence, "rationale": "r-" + symbol,
            "contributions": contributions}


def test_pool_keeps_buy_only_sorted_by_score():
    combined = [row("000001.SZ", "sell", 15.0),
                row("600519.SH", "buy", 85.0),
                row("000002.SZ", "buy", 92.5),
                row("00700.HK", "hold", 50.0)]
    pool = build_pool(DAY, combined, [])
    assert [p["symbol"] for p in pool["pool"]] == ["000002.SZ", "600519.SH"]
    assert all(p["action"] == "buy" for p in pool["pool"])
    assert pool["stats"]["combined_total"] == 4
    assert pool["stats"]["buy_total"] == 2
    assert pool["cutover_verdict"] == "BLOCK_CUTOVER"
    assert pool["scope"] == "RESEARCH_ONLY_NOT_PRODUCTION"


def test_pool_records_superseded_and_divergence_stats():
    combined = [row("600519.SH", "buy", 85.0, sup=2),
                row("000002.SZ", "buy", 70.0, divergence=True)]
    pool = build_pool(DAY, combined, [{"symbol": "000002.SZ"}])
    assert pool["stats"]["superseded_total"] == 2
    assert pool["stats"]["divergence_total"] == 1


def test_pool_empty_result_is_wellformed():
    pool = build_pool(DAY, [], [])
    assert pool["pool"] == []
    assert pool["stats"]["buy_total"] == 0
    json.dumps(pool, ensure_ascii=False)  # 可序列化


def test_collect_a4_comparison_degrades_on_failure(monkeypatch):
    """探针异常 → DEGRADED 降级（不阻断池产出），不抛出。"""
    from strategy_layer import xunyou

    def boom(trade_date):
        raise RuntimeError("pool missing")

    monkeypatch.setattr(
        "strategy_layer.a4_readonly_probe.inspect", boom)
    out = xunyou.collect_a4_comparison(DAY)
    assert out["status"] == "DEGRADED"
    assert "pool missing" in out["reason"]


def test_collect_a4_comparison_passes_summary(monkeypatch):
    """探针与契约正常时透传最小摘要，固定 BLOCK_CUTOVER。"""
    from strategy_layer import xunyou

    obs = {"history": {"status": "MATCHED"}, "signals_a": 3,
           "unique_a": 3, "issues": ["X"]}
    comp = {"observation_comparability": "COMPARABLE_WITHIN_PROBE",
            "reasons": ["R1"]}
    monkeypatch.setattr(
        "strategy_layer.a4_readonly_probe.inspect", lambda td: obs)
    monkeypatch.setattr(
        "strategy_layer.a4_comparison_contract.evaluate_comparison",
        lambda o: comp)
    out = xunyou.collect_a4_comparison(DAY)
    assert out == {"status": "OK", "history_status": "MATCHED",
                   "signals_a": 3, "unique_a": 3,
                   "comparability": "COMPARABLE_WITHIN_PROBE",
                   "reasons": ["R1"], "issues": ["X"],
                   "cutover_verdict": "BLOCK_CUTOVER"}
