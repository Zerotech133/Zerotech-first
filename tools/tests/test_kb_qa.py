# -*- coding: utf-8 -*-
"""kb_qa（研报知识库 RAG 问答）契约测试 —— 不调真实 LLM（检索可用真实知识库只读检索）。

覆盖：
  1. 检索融合纯函数：两路归一化融合、unit 优先、chunk 降噪门槛、去重、topk；
  2. prompt 构建：强制只依据材料 / 引用 [unit_id] 与页码 / 无依据说不知道；
  3. 引用校验与置信说明：可核验引用→high/medium、未知引用剔除、未引用标记、拒答→none、fallback→low；
  4. 问答主流程（mock）：正常作答 schema、无命中拒答且不调 LLM、LLM 失败规则降级、retrieval-only；
  5. 静态样本：5 问含无依据/失败示例、写 research+portal 双份；
  6. CLI --help + `ask --retrieval-only --json`（离线、不调模型）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
QA_PATH = REPO / "tools" / "kb_corpus" / "kb_qa.py"


def _load_qa():
    spec = importlib.util.spec_from_file_location("kb_qa_under_test", QA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qa = _load_qa()

UNITS = [
    {"id": "41692ee59293", "book": "rp_AP202610071830180005", "chapter": "图表目录",
     "page_start": 2, "page_end": 14, "concept": "M9 覆铜板",
     "claim": "M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产。",
     "evidence": "介电损耗低于 0.0015。", "score": 5.0},
    {"id": "28e0346286a2", "book": "rp_AP202610071830188839", "chapter": "PCB",
     "page_start": 3, "page_end": 4, "concept": "PCB 价值量",
     "claim": "单机柜 PCB 价值量成倍增长。", "evidence": "224G 演进推动层数增加。", "score": 3.0},
]
CHUNKS = [
    {"id": "c00000000001", "text": "M9 级覆铜板与 224Gbps PCB 价值量：覆铜板材料升级是关键。", "score": 9.0},
    {"id": "c00000000002", "text": "今天天气不错，评论区大家好。", "score": 9.0},  # 与问题无关 → 降噪丢弃
]
HITS = qa.fuse_hits(UNITS, CHUNKS, topk=8, question="M9 覆铜板与 PCB 价值量")
QUESTION = "M9 覆铜板对 PCB 价值量的影响？"


def fake_search(question, topk):
    return HITS


def fake_chat_ok(prompt, model=None, timeout=None):
    return "M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产，单机柜价值量提升 [41692ee59293] p.2-14；PCB 层数增加 [28e0346286a2] p.3-4。"


# ---------------------------------------------------------------------------
# 1. 检索融合
# ---------------------------------------------------------------------------
def test_fuse_hits_prioritizes_units_filters_noise_and_dedupes():
    fused = qa.fuse_hits(UNITS + [dict(UNITS[0])], CHUNKS, topk=5, question="M9 覆铜板与 PCB 价值量")
    assert fused, "应有融合结果"
    assert fused[0]["kind"] == "unit" and fused[0]["unit_id"] == "41692ee59293"
    ids = [h["unit_id"] for h in fused]
    assert len(ids) == len(set(ids)), "按 unit_id 去重"
    # 无关 chunk（无问题词重叠）被降噪门槛丢弃
    assert "c00000000002" not in ids
    # 相关 chunk 可入选但权重低于 unit（unit 优先）
    assert any(h["kind"] == "chunk" for h in fused)
    assert [h["fused_score"] for h in fused] == sorted([h["fused_score"] for h in fused], reverse=True)
    assert len(qa.fuse_hits(UNITS, [], topk=1, question="M9")) == 1
    assert qa.fuse_hits([], [], topk=5) == []
    # 缺字段的脏数据不崩溃
    assert qa.fuse_hits([{"id": "x"}], [{"id": "y"}], topk=3) == []


# ---------------------------------------------------------------------------
# 2. prompt 构建
# ---------------------------------------------------------------------------
def test_build_prompt_enforces_grounding_and_citation():
    prompt = qa.build_prompt(QUESTION, HITS[:3])
    assert QUESTION in prompt
    assert "只依据【检索材料】回答" in prompt
    assert "不得编造" in prompt
    assert "[unit_id]" in prompt and "页码" in prompt
    assert "知识库中没有足够依据回答该问题" in prompt
    assert "[41692ee59293]" in prompt and "p.2-14" in prompt
    assert "（无检索材料）" in qa.build_prompt(QUESTION, [])


# ---------------------------------------------------------------------------
# 3. 引用校验 + 置信说明
# ---------------------------------------------------------------------------
def test_citation_extraction_and_validation():
    assert qa.extract_citation_ids("结论 [41692ee59293] p.2 与【28e0346286a2】p.3") == \
        ["41692ee59293", "28e0346286a2"]
    cite = qa.build_citations("依据 [41692ee59293] p.2 与 [deadbeefcafe] p.9", HITS)
    assert len(cite["citations"]) == 1
    assert cite["citations"][0]["unit_id"] == "41692ee59293"
    assert cite["citations"][0]["book"] and cite["citations"][0]["page"] == 2
    assert cite["hallucinated_citations"] == ["deadbeefcafe"]
    no_cite = qa.build_citations("没有引用的回答", HITS)
    assert no_cite["citation_missing"] is True and no_cite["citations"] == []


def test_confidence_levels():
    ok_cite = qa.build_citations("A [41692ee59293] B [28e0346286a2]", HITS)
    assert qa.assess_confidence("A B", ok_cite, HITS, False)["level"] == "high"
    one_cite = qa.build_citations("A [41692ee59293]", HITS)
    assert qa.assess_confidence("A", one_cite, HITS, False)["level"] == "medium"
    refusal = qa.build_citations("知识库中没有足够依据回答该问题。", HITS)
    assert qa.assess_confidence("知识库中没有足够依据回答该问题。", refusal, HITS, False)["level"] == "none"
    fallback = qa.assess_confidence("（规则降级）[41692ee59293]", one_cite, HITS, True)
    assert fallback["level"] == "low" and any("降级" in r for r in fallback["reasons"])
    assert qa.assess_confidence("", qa.build_citations("", []), [], False)["level"] == "none"


# ---------------------------------------------------------------------------
# 4. 问答主流程（mock）
# ---------------------------------------------------------------------------
def test_ask_schema_with_mock_llm():
    result = qa.ask(QUESTION, topk=5, search_fn=fake_search, chat_fn=fake_chat_ok)
    for key in ("question", "answer", "citations", "confidence", "retrieved", "llm",
                "citation_check", "generated_at", "elapsed_seconds"):
        assert key in result
    assert result["retrieved"] and result["retrieved"][0]["unit_id"] == "41692ee59293"
    assert len(result["citations"]) == 2
    assert result["confidence"]["level"] == "high"
    assert result["llm"]["fallback"] is False
    assert result["citation_check"]["hallucinated_citations"] == []
    roundtrip = json.loads(json.dumps(result, ensure_ascii=False))
    assert roundtrip["answer"]


def test_ask_no_hits_refuses_without_calling_llm():
    def must_not_call(*a, **k):
        raise AssertionError("无检索命中时不应调用 LLM")

    result = qa.ask(QUESTION, search_fn=lambda q, n: [], chat_fn=must_not_call)
    assert result["answer"] == qa.REFUSAL_ANSWER
    assert result["citations"] == [] and result["confidence"]["level"] == "none"
    assert result["retrieved"] == []


def test_ask_llm_failure_falls_back_to_extractive_answer():
    def failing_chat(prompt, model=None, timeout=None):
        raise qa.KbQaError("Ollama 被占满（测试注入）")

    result = qa.ask(QUESTION, search_fn=fake_search, chat_fn=failing_chat)
    assert result["fallback"] is True
    assert "规则降级" in result["answer"]
    assert result["citations"], "规则降级仍应给出可核验引用"
    assert result["llm"]["fallback"] is True and result["llm"]["error"]
    assert result["confidence"]["level"] == "low"


def test_ask_retrieval_only_skips_llm():
    def must_not_call(*a, **k):
        raise AssertionError("retrieval-only 不应调用 LLM")

    result = qa.ask(QUESTION, search_fn=fake_search, chat_fn=must_not_call, retrieval_only=True)
    assert result["answer"] is None and result["citations"] == []
    assert result["retrieved"]
    assert result["confidence"]["level"] == "n/a"


def test_ask_rejects_empty_question():
    with pytest.raises(qa.KbQaError):
        qa.ask("   ")


# ---------------------------------------------------------------------------
# 5. 静态样本
# ---------------------------------------------------------------------------
def test_build_samples_includes_no_evidence_and_failure_demos(tmp_path):
    def fake_ask(question, topk=8, model=None, timeout=None, search_fn=None, chat_fn=None,
                 retrieval_only=False):
        if chat_fn is None and search_fn is None:
            return {"answer": "示范答案 [41692ee59293] p.2", "citations": [{"unit_id": "41692ee59293"}],
                    "confidence": {"level": "high"}, "fallback": False, "retrieved": [{}, {}],
                    "citation_check": {}}
        # 演示路径：模拟空检索 → 拒答；模拟 LLM 异常 → ask 内部降级为规则摘录
        if chat_fn is None:
            return {"answer": qa.REFUSAL_ANSWER, "citations": [], "confidence": {"level": "none"},
                    "fallback": False, "retrieved": [], "citation_check": {}}
        return {"answer": "（规则降级）摘录 [41692ee59293] p.2", "citations": [{"unit_id": "41692ee59293"}],
                "confidence": {"level": "low"}, "fallback": True, "retrieved": [{}, {}],
                "citation_check": {}}

    payload = qa.build_samples(limit=5, ask_fn=fake_ask)
    samples = payload["samples"]
    assert len(samples) == 5
    cats = [s["category"] for s in samples]
    assert "no_evidence_simulated" in cats and "llm_failure_simulated" in cats
    assert all(s.get("note") for s in samples if s["category"] != "answerable")
    assert payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    assert payload["model"] and payload["kb"]
    out, portal = qa.write_samples(payload, tmp_path / "samples.json", tmp_path / "portal.json")
    assert out.exists() and portal.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(portal.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 6. CLI（离线检索模式）
# ---------------------------------------------------------------------------
def test_cli_help_and_retrieval_only_json():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "ask" in proc.stdout and "samples" in proc.stdout
    proc = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "ask",
                           "M9 级覆铜板对 PCB 价值量有什么影响？", "--topk", "5",
                           "--retrieval-only", "--json"],
                          capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["retrieved"], "真实知识库检索应有命中"
    assert data["answer"] is None and data["confidence"]["level"] == "n/a"
