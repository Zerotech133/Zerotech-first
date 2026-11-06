# -*- coding: utf-8 -*-
"""rag_rerank_eval 单测（task-185，5+）：规则打分/重排稳定性/指标/空输入/降级/数据集。

不联网、不写报告；运行：
  C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_rag_rerank.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import rag_rerank_eval as rre  # noqa: E402


def _ctx(extra=None):
    docs = {
        "C:001": {"doc_id": "C:001", "kind": "card", "concept": "布林线",
                   "text": "什么是「布林线」？ 以均线为中轨、上下轨为 ±k 倍标准差构成的波动带。",
                   "source": "《rp_AP202609301830015406》 研报 20260930"},
        "C:002": {"doc_id": "C:002", "kind": "card", "concept": "其他主题",
                   "text": "无关内容。", "source": "视频号学习_8博主 · 20260103_x.md"},
        "U:learn_a_0001": {"doc_id": "U:learn_a_0001", "kind": "unit", "concept": "",
                            "text": "布林线的中轨是移动平均线，上下轨由标准差决定。",
                            "source": "20261103_a.md"},
    }
    docs.update(extra or {})
    ctx = rre.build_context(list(docs.values()))
    return ctx


# ---------------------------------------------------------------- 1. 文本特征
def test_ngrams_and_query_tokens():
    assert "布林" in rre.cjk_bigrams("布林线")
    assert rre.latin_tokens("BOLL 指标 v2") == ["boll", "v2"]
    toks = rre.query_tokens("什么是布林线 BOLL？")
    assert "布林" in toks and "boll" in toks and "" not in toks


# ---------------------------------------------------------------- 2. 规则打分
def test_rule_score_features():
    ctx = _ctx()
    q = "什么是布林线？"
    score_hit, feats = rre.rule_score(q, ctx["docs"]["C:001"], ctx)
    score_miss, _ = rre.rule_score(q, ctx["docs"]["C:002"], ctx)
    assert 0.0 <= score_hit <= 1.0 and 0.0 <= score_miss <= 1.0
    assert score_hit > score_miss, "相关文档规则分应更高"
    assert feats["coverage"] > 0 and feats["title"] >= 0.5
    # 来源可信/时效：研报 > 视频号
    assert rre.source_trust(ctx["docs"]["C:001"]) > rre.source_trust(ctx["docs"]["C:002"])
    assert 0.0 < rre.freshness(ctx["docs"]["C:001"]) <= 1.0


# ---------------------------------------------------------------- 3. 重排稳定性
def test_rerank_order_stable_and_promotes():
    ctx = _ctx()
    q = "什么是布林线？"
    # 相关文档原排名靠后 → 重排应前置；无关文档顺序保持稳定
    cand = ["C:002", "U:learn_a_0001", "C:001"]
    reranked = rre.rerank_ids(q, cand, ctx)
    assert reranked[0] in ("C:001", "U:learn_a_0001")
    assert reranked[-1] == "C:002"
    # 确定性：同输入两次结果相同
    assert rre.rerank_ids(q, cand, ctx) == reranked
    # 全部同分时保持原顺序（tie 稳定）
    empty_ctx = rre.build_context([{"doc_id": "X:1", "text": "", "source": ""},
                                   {"doc_id": "X:2", "text": "", "source": ""}])
    assert rre.rerank_ids("zzz", ["X:1", "X:2"], empty_ctx) == ["X:1", "X:2"]


# ---------------------------------------------------------------- 4. 指标计算
def test_metrics_math():
    gold = ["C:001"]
    assert rre.metrics(["C:001", "X", "Y"], gold) == {
        "rerank@5": 1.0, "recall@5": 1.0, "mrr": 1.0, "gold_top3": 1.0}
    # 第 2 名命中：gold_top3=1，MRR=0.5
    m = rre.metrics(["X", "C:001", "Y"], gold)
    assert m["gold_top3"] == 1.0 and m["mrr"] == 0.5
    # 第 4 名命中：gold_top3=0，MRR=0.25
    m2 = rre.metrics(["X", "Y", "Z", "C:001"], gold)
    assert m2["gold_top3"] == 0.0 and m2["mrr"] == 0.25
    # 多 gold：命中 2/2
    m3 = rre.metrics(["C:001", "C:002"], ["C:001", "C:002"])
    assert m3["recall@5"] == 1.0 and m3["mrr"] == 1.0
    # 未命中
    assert rre.metrics(["X"], gold)["mrr"] == 0.0
    # gold 为空（不可答）→ {}，不计检索指标
    assert rre.metrics(["C:001"], []) == {}


# ---------------------------------------------------------------- 5. 空候选 / 聚合
def test_empty_candidates_and_aggregate():
    assert rre.metrics([], ["C:001"]) == {"rerank@5": 0.0, "recall@5": 0.0,
                                          "mrr": 0.0, "gold_top3": 0.0}  # 无候选 → 全 0
    assert rre.rerank_ids("q", [], _ctx()) == []     # 空候选重排不崩溃
    rows = [
        {"baseline": {"rerank@5": 1.0, "recall@5": 1.0, "mrr": 1.0, "gold_top3": 1.0},
         "rerank": {"rerank@5": 1.0, "recall@5": 1.0, "mrr": 1.0, "gold_top3": 1.0}},
        {"baseline": {"rerank@5": 0.0, "recall@5": 0.0, "mrr": 0.0, "gold_top3": 0.0},
         "rerank": {"rerank@5": 1.0, "recall@5": 1.0, "mrr": 0.5, "gold_top3": 1.0}},
    ]
    agg = rre.aggregate(rows)
    assert agg["n"] == 2
    assert agg["delta"]["gold_top3"] == 0.5 and agg["delta"]["mrr"] == 0.25
    assert rre.aggregate([])["n"] == 0


# ---------------------------------------------------------------- 6. reranker 降级
def test_reranker_degrade_rule_only(monkeypatch):
    def _no_net(*a, **k):
        raise OSError("no network")
    # 被测代码走 safe_urlopen（回环禁代理，见 tools/safe_urlopen.py），
    # 只 patch urllib.request.urlopen 已拦不住回环请求，必须 patch 模块实际使用的符号。
    monkeypatch.setattr(rre, "safe_urlopen", _no_net)
    st = rre.reranker_status()
    assert st["available"] is False and st["mode"] == "rule_only"
    assert "reason" in st and "ollama" in st["reason"]


# ---------------------------------------------------------------- 7. 数据集加载（只读产物）
def test_datasets_load_readonly():
    w13 = rre.load_w13_cases()
    assert w13["total"] == 40 and w13["evaluated"] == 30 and len(w13["unanswerable"]) == 10
    assert w13["mapped_hybrid"] == 30, "W13 可答 30 题应全部映射到 hybrid top10 候选"
    assert all(c["candidates"] for c in w13["cases"]), "候选池不得为空"
    # 30 可答中 3 题无 citation_gold（不参与检索指标），其余 27 题有 gold
    assert w13["retrieval_evaluated"] == 27 and w13["no_gold"] == 3
    assert set(w13["no_gold_qids"]) == {"A21", "A27", "A29"}
    held = rre.load_heldout_cases()
    assert held["total"] == 50 and held["evaluated"] == 50
    assert all(c["gold_doc_ids"] and c["candidates"] for c in held["cases"])
    json.dumps(w13, ensure_ascii=False)  # 可序列化
