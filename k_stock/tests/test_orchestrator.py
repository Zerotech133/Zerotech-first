# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 编排器自测（mock LLM，全链确定性）

跑法：python -m pytest k_stock/tests/test_orchestrator.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from k_stock.kstock.llm import LLMClient
from k_stock.kstock.orchestrator import Orchestrator, load_team, similarity, topo_waves

TEAM = Path(__file__).resolve().parents[1] / "config" / "team_poc.json"


def test_similarity():
    assert similarity("均线多头排列 量能放大", "量能放大 均线多头排列") > 0.8
    assert similarity("完全不同的话题", "风马牛不相及") < 0.2
    assert similarity("", "abc") == 0.0


def test_topo_waves():
    nodes = load_team(TEAM)
    waves = topo_waves(nodes)
    assert len(waves) == 2  # 第 0 波：T1/T2/N1/F1；第 1 波：D1
    assert {n["node_key"] for n in waves[0]} == {"T1", "T2", "N1", "F1"}
    assert {n["node_key"] for n in waves[1]} == {"D1"}


def test_cycle_detected():
    bad = [{"node_key": "A", "analysis_type": "market_technical", "parent_node_keys": ["B"]},
           {"node_key": "B", "analysis_type": "market_technical", "parent_node_keys": ["A"]}]
    try:
        topo_waves(bad)
        assert False, "应抛环异常"
    except ValueError as e:
        assert "环" in str(e)


def test_full_run_mock():
    """mock 全链：T1/T2 辩论 2 轮 → 并行 N1/F1（已在第 0 波）→ D1 决策。"""
    nodes = load_team(TEAM)
    chat = LLMClient(mock=True).chat
    orch = Orchestrator(nodes, chat)
    result = orch.run(stock="600519.SH 贵州茅台", context="收盘价 1450.00，5/10/20 日均线多头排列；无重大新闻。")
    outs = result["outputs"]
    # 五节点全产出
    assert set(outs) == {"T1", "T2", "N1", "F1", "D1"}
    # 辩论发生（trace 有 debate_done 且轮次>0）
    debate = [t for t in result["trace"] if t["event"] == "debate_done"]
    assert debate and debate[0]["rounds"] >= 1
    # D1 决策契约（mock fixture）
    d1 = outs["D1"]
    assert d1["action"] in {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
    assert d1["stock"] == "600519.SH"
    assert 0 <= d1["confidence"] <= 1
    # lineage 留痕完整
    assert all(k in result["raw"] for k in outs)
    assert result["degraded"] == []


def test_degraded_path():
    """LLM 全挂：五节点全部降级输出，全链不崩，degraded 有记录。"""
    class DeadLLM:
        def chat(self, *a, **kw):
            raise ConnectionError("down")

    orch = Orchestrator(load_team(TEAM), DeadLLM().chat)
    result = orch.run(stock="000001.SZ 平安银行", context="")
    assert len(result["outputs"]) == 5
    assert all(v.get("degraded") for v in result["outputs"].values())
    assert len(result["degraded"]) == 5
    retries = [t for t in result["trace"] if t["event"] == "node_retry"]
    assert retries  # 降级前确实重试过
