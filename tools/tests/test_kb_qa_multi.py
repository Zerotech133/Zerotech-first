# -*- coding: utf-8 -*-
"""kb_qa_multi（多单元聚合回答 MVP）契约测试 —— mock 不调真模型。

覆盖：
  1. 聚类纯函数：unit 优先/按 concept 聚类/簇排序/簇数与簇内上限/空输入；
  2. 多单元 prompt：聚类材料、[unit_id]、直接事实/推断区分、拒答规则；
  3. 引用校验与置信：有效/幻觉引用、直接事实/推断计数、拒答、fallback 降级；
  4. answer_multi 全链路（mock 检索+生成）：schema、空材料拒答不调 LLM、LLM 失败规则降级；
  5. eval_compare：v3 基线对比 schema、delta、结论、逐条状态；JSON/MD 落盘；
  6. CLI --help 与异常容错。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MULTI_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi.py"


def _load_multi():
    spec = importlib.util.spec_from_file_location("kb_qa_multi_under_test", MULTI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mk = _load_multi()


def unit_hit(uid, concept, book="book_a", chapter="第一章", score=1.0):
    return {"kind": "units", "unit_id": uid, "concept": concept, "book": book,
            "chapter": chapter, "page_start": 2, "text": f"{concept} 的原文证据。",
            "fused_score": score}


def chunk_hit(uid, score=0.5):
    return {"kind": "chunk", "unit_id": uid, "concept": "", "book": "video", "chapter": "",
            "page_start": 1, "text": "语料片段", "fused_score": score}


HITS = [
    unit_hit("aaaa1111bbbb", "M9 覆铜板", "rp_a", "第一章", 1.1),
    unit_hit("cccc2222dddd", "M9 覆铜板", "rp_b", "第二章", 0.9),
    unit_hit("eeee3333ffff", "PCB 价值量", "rp_a", "第一章", 0.8),
    unit_hit("999988887777", "球形硅微粉", "rp_c", "第三章", 0.3),
    chunk_hit("chunk0001", 0.2),
]

MOCK_CALLS: list[str] = []


def mock_chat(prompt, model=None, timeout=None):
    MOCK_CALLS.append(prompt)
    ids = re.findall(r"\[([0-9a-f]{8,12})\]", prompt)
    cid = ids[0] if ids else "unknown000"
    return f"【直接事实】M9 覆铜板支持高速传输 [{cid}] p.2。【推断】价值量将提升 [{cid}] p.2。"


def mock_search(question, topk=8):
    return HITS[:topk]


# ---------------------------------------------------------------------------
# 1. 聚类
# ---------------------------------------------------------------------------
def test_cluster_hits_priority_and_limits():
    clusters = mk.cluster_hits(HITS, max_clusters=2, max_units=2)
    assert 1 <= len(clusters) <= 2
    assert all(c["units"] for c in clusters)
    # 优先 units（语料 chunk 不单独成簇）
    all_ids = [u["unit_id"] for c in clusters for u in c["units"]]
    assert "chunk0001" not in all_ids
    # 按最高融合分排序
    scores = [c["max_score"] for c in clusters]
    assert scores == sorted(scores, reverse=True)
    # 每簇单元不超过上限
    assert all(len(c["units"]) <= 2 for c in clusters)
    # 空输入/纯 chunk 也不崩溃
    assert mk.cluster_hits([]) == []
    assert mk.cluster_hits([chunk_hit("c1")])[0]["units"][0]["unit_id"] == "c1"


# ---------------------------------------------------------------------------
# 2. prompt
# ---------------------------------------------------------------------------
def test_build_multi_prompt_contains_clusters_and_rules():
    clusters = mk.cluster_hits(HITS, max_clusters=3)
    prompt = mk.build_multi_prompt("M9 覆铜板对 PCB 价值量的影响？", clusters)
    assert "【直接事实】" in prompt and "【推断】" in prompt
    assert "[unit_id]" in prompt and "页码" in prompt
    assert "知识库中没有足够依据回答该问题" in prompt
    assert "【簇1】" in prompt
    assert "M9 覆铜板" in prompt
    assert "没有足够依据" in mk.build_multi_prompt("q", [])


# ---------------------------------------------------------------------------
# 3. 引用校验 + 置信
# ---------------------------------------------------------------------------
def test_validate_multi_answer_and_confidence():
    clusters = mk.cluster_hits(HITS, max_clusters=2)
    ans = ("【直接事实】M9 支持高速传输 [aaaa1111bbbb] p.2。"
           "【推断】价值量提升 [deadbeefcafe] p.3。")
    check = mk.validate_multi_answer(ans, clusters)
    assert check["cited_ids"] == ["aaaa1111bbbb"]
    assert check["hallucinated_ids"] == ["deadbeefcafe"]
    assert len(check["citations"]) == 1
    assert check["direct_fact_markers"] >= 1 and check["inference_markers"] >= 1
    assert check["refusal"] is False
    # 置信：有效引用 1 → medium；拒答 → none；fallback → low
    assert mk.assess_confidence(ans, check, clusters, False)["level"] == "medium"
    refusal_ans = "知识库中没有足够依据回答该问题。"
    check_r = mk.validate_multi_answer(refusal_ans, clusters)
    assert check_r["refusal"] is True
    assert mk.assess_confidence(refusal_ans, check_r, clusters, False)["level"] == "none"
    assert mk.assess_confidence(ans, check, clusters, True)["level"] == "low"
    assert mk.assess_confidence(ans, check, [], False)["level"] == "none"


# ---------------------------------------------------------------------------
# 4. answer_multi（mock）
# ---------------------------------------------------------------------------
def test_answer_multi_schema_and_fallback():
    MOCK_CALLS.clear()
    result = mk.answer_multi("M9 覆铜板对 PCB 的影响？", k=8, search_fn=mock_search, chat_fn=mock_chat)
    for key in ("question", "answer", "citations", "confidence", "clusters", "cluster_count",
                "direct_fact_markers", "inference_markers", "fallback", "elapsed_seconds"):
        assert key in result, key
    assert result["cluster_count"] >= 1 and result["citations"]
    assert result["fallback"] is False and result["confidence"]["level"] in ("high", "medium")

    # 空检索材料 → 拒答，不调 LLM
    def must_not_call(*a, **k):
        raise AssertionError("无材料时不应调用 LLM")

    empty = mk.answer_multi("问题", search_fn=lambda q, topk=8: [], chat_fn=must_not_call)
    assert empty["refusal"] is True and empty["confidence"]["level"] == "none"

    # LLM 失败 → 规则式多单元摘录降级
    def bad_chat(*a, **k):
        raise mk.MultiError("Ollama 被占满（测试）")

    fb = mk.answer_multi("M9 覆铜板对 PCB 的影响？", search_fn=mock_search, chat_fn=bad_chat)
    assert fb["fallback"] is True and "规则降级" in fb["answer"]
    assert fb["confidence"]["level"] == "low"
    # 分类状态
    assert mk.classify_status(fb)["status"] == "fallback"
    assert mk.classify_status(empty)["status"] == "no_evidence"
    assert mk.classify_status(result)["status"] == "success"


# ---------------------------------------------------------------------------
# 5. eval_compare 对比 + 落盘
# ---------------------------------------------------------------------------
def test_eval_compare_schema_and_outputs(tmp_path):
    baseline = {"answer_validation": {
        "requested": 3, "success": 1, "no_evidence": 2, "fallback": 0,
        "answerable_rate": 0.3333, "avg_citations": 1.0, "elapsed_seconds": 30.0,
        "samples": [
            {"id": "q1", "question": "M9 覆铜板对 PCB 的影响？", "category": "影响如何",
             "status": "no_evidence", "citations": []},
            {"id": "q2", "question": "固态电池量产时间表？", "category": "关键数据",
             "status": "success", "citations": [{"unit_id": "x"}]},
            {"id": "q3", "question": "资本市场改革成效？", "category": "是什么",
             "status": "no_evidence", "citations": []},
        ]}}
    baseline_path = tmp_path / "v3.json"
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    payload = mk.eval_compare(n=3, k=8, ask_fn=mock_chat, search_fn=mock_search,
                              baseline_path=baseline_path, clock=lambda: 0.0)
    assert payload["kind"] == "kb_qa_multi_eval"
    for key in ("single", "multi", "delta", "comparison", "conclusion", "config"):
        assert key in payload, key
    assert payload["single"]["answerable_rate"] == 0.3333
    assert payload["multi"]["requested"] == 3
    assert len(payload["comparison"]) == 3
    assert payload["multi"]["success"] + payload["multi"]["no_evidence"] + payload["multi"]["fallback"] == 3
    assert payload["conclusion"]["verdict"] and payload["conclusion"]["risks"]
    assert isinstance(payload["conclusion"]["multi_unit_breaks_bottleneck"], bool)
    jp, mp = mk.write_outputs(payload, tmp_path / "m.json", tmp_path / "m.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("汇总对比", "结论：多单元是否突破瓶颈", "逐条对比"):
        assert anchor in md, anchor
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["multi"]["requested"] == 3


def test_load_baseline_errors_and_cli_help(tmp_path):
    with pytest_raises(mk.MultiError):
        mk.load_v3_baseline(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"answer_validation": {}}', encoding="utf-8")
    with pytest_raises(mk.MultiError):
        mk.load_v3_baseline(bad)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(MULTI_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "ask" in proc.stdout and "eval" in proc.stdout


class pytest_raises:
    """轻量上下文管理器（避免依赖 pytest fixture 之外的导入）。"""

    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
