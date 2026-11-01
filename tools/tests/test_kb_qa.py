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
    result = qa.ask(QUESTION, topk=5, search_fn=fake_search, chat_fn=fake_chat_ok, mode="single")
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

    result = qa.ask(QUESTION, search_fn=lambda q, n: [], chat_fn=must_not_call, mode="single")
    assert result["answer"] == qa.REFUSAL_ANSWER
    assert result["citations"] == [] and result["confidence"]["level"] == "none"
    assert result["retrieved"] == []


def test_ask_llm_failure_falls_back_to_extractive_answer():
    def failing_chat(prompt, model=None, timeout=None):
        raise qa.KbQaError("Ollama 被占满（测试注入）")

    result = qa.ask(QUESTION, search_fn=fake_search, chat_fn=failing_chat, mode="single")
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


# ---------------------------------------------------------------------------
# 7. multi 默认模式（wave18 AZ）：默认 multi / single 向后兼容 / 回退 / 参数校验
# ---------------------------------------------------------------------------
class FakeMultiModule:
    """模拟 kb_qa_multi 模块（不调 LLM/不检索真实库）。"""

    def __init__(self):
        self.calls = {"retrieve": 0, "answer": 0}

    def retrieve_clusters(self, question, k=8, search_fn=None):
        self.calls["retrieve"] += 1
        return [{"label": "M9 覆铜板", "book": "rp_a", "chapter": "图表目录", "max_score": 1.1,
                 "units": [{"unit_id": "u_multi_1", "book": "rp_a", "chapter": "图表目录",
                            "page": 2, "text": "多单元证据 A", "fused_score": 1.1},
                           {"unit_id": "u_multi_2", "book": "rp_b", "chapter": "第二章",
                            "page": 3, "text": "多单元证据 B", "fused_score": 0.8}]}]

    def answer_multi(self, question, k=8, clusters=None, search_fn=None, chat_fn=None,
                     model=None, timeout=None):
        self.calls["answer"] += 1
        return {"question": question,
                "answer": "【直接事实】多单元综合结论 [u_multi_1] p.2",
                "citations": [{"unit_id": "u_multi_1", "book": "rp_a", "chapter": "图表目录",
                               "page": 2, "text": "多单元证据 A"}],
                "confidence": {"level": "high", "reasons": ["1 条可核验引用"]},
                "fallback": False, "refusal": False, "cluster_count": len(clusters or []),
                "direct_fact_markers": 1, "inference_markers": 1,
                "citation_check": {"hallucinated_ids": [], "citation_missing": False},
                "llm": {"model": model, "fallback": False, "error": None},
                "generated_at": "2026-11-01T00:00:00+08:00", "elapsed_seconds": 3.0}


class FakeGuardPass:
    """pass-through 守卫：只补 guard 字段，不改答案（用于默认 multi 行为测试）。"""

    def load_kb_ids(self):
        return ({"u_multi_1", "u_multi_2"}, {"rp_a", "rp_b"})

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback"):
        out = dict(result)
        out.update({"guard_passed": True, "guard_action": "pass", "guard_issues": [],
                    "citation_valid": ["u_multi_1"], "citation_invalid": [],
                    "citation_resolvable_rate": 1.0, "inference_ratio": 0.5,
                    "inference_heavy": False, "distinct_cited_units": 2})
        return out


def test_default_mode_is_multi(monkeypatch):
    fake = FakeMultiModule()
    monkeypatch.setattr(qa, "_load_kb_qa_multi", lambda: fake)
    monkeypatch.setattr(qa, "_load_kb_qa_guard", lambda: FakeGuardPass())
    monkeypatch.setattr(qa, "_guard_kb_ids", None, raising=False)
    result = qa.ask("M9 覆铜板对 PCB 的影响？", topk=8)
    assert result["mode"] == "multi"
    assert result["k"] == 8
    assert fake.calls == {"retrieve": 1, "answer": 1}
    assert result["clusters"] and result["citations"]
    assert result["retrieved"][0]["unit_id"] == "u_multi_1"
    assert result["citation_check"]["hallucinated_citations"] == []
    assert result["direct_fact_markers"] == 1 and result["inference_markers"] == 1
    # topk 钳制到 multi 的 5..8
    result2 = qa.ask("另一个问题", topk=20)
    assert 5 <= result2["k"] <= 8


def test_mode_single_backward_compatible(monkeypatch):
    def boom():
        raise AssertionError("single 模式不应加载 multi/guard 模块")

    monkeypatch.setattr(qa, "_load_kb_qa_multi", boom)
    monkeypatch.setattr(qa, "_load_kb_qa_guard", boom)
    result = qa.ask(QUESTION, topk=5, search_fn=fake_search, chat_fn=fake_chat_ok, mode="single")
    assert result["mode"] == "single" and result["clusters"] == []
    assert result["k"] == 5 and result["topk"] == 5
    assert len(result["citations"]) == 2 and result["confidence"]["level"] == "high"
    assert result["llm"]["fallback"] is False
    assert "multi_fallback" not in result
    # 旧字段仍在（向后兼容）
    for key in ("question", "answer", "citations", "confidence", "retrieved", "citation_check",
                "llm", "generated_at", "elapsed_seconds"):
        assert key in result, key


def test_multi_failure_falls_back_to_single(monkeypatch):
    def boom():
        raise qa.KbQaError("kb_qa_multi 不可用（测试注入）")

    monkeypatch.setattr(qa, "_load_kb_qa_multi", boom)
    result = qa.ask(QUESTION, topk=5, search_fn=fake_search, chat_fn=fake_chat_ok)  # 默认 multi
    assert result["mode"] == "single"
    assert result["multi_fallback"] is True
    assert "kb_qa_multi 不可用" in result["multi_fallback_error"]
    assert result["citations"]
    assert result["guard_enabled"] is False and result["guard_action"] == "multi_fallback_single"


def test_invalid_mode_raises():
    with pytest.raises(qa.KbQaError):
        qa.ask(QUESTION, mode="bogus")


def test_cli_mode_flags_and_single_alias():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "ask", "--help"],
                          capture_output=True, text=True, timeout=60,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    for flag in ("--mode", "--k", "--single", "multi"):
        assert flag in proc.stdout
    proc2 = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "ask", "M9 覆铜板",
                            "--single", "--retrieval-only", "--json"],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
    assert proc2.returncode == 0, proc2.stderr
    data = json.loads(proc2.stdout)
    assert data["mode"] == "retrieval" and data["retrieved"]
    proc3 = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "ask", "M9 覆铜板",
                            "--mode", "single", "--retrieval-only", "--json"],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
    assert proc3.returncode == 0
    assert json.loads(proc3.stdout)["mode"] == "retrieval"


# ---------------------------------------------------------------------------
# 8. guard 接入 multi 出口（wave20 CA）
# ---------------------------------------------------------------------------
class FakeGuardFail:
    """模拟守卫生成「不通过」并执行 mark/fallback/refuse。"""

    def __init__(self):
        self.calls = []

    def load_kb_ids(self):
        return ({"u_multi_1", "u_multi_2"}, {"rp_a"})

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback"):
        self.calls.append(on_fail)
        out = dict(result)
        out.update({"citation_valid": ["u_multi_1"], "citation_invalid": ["deadbeefcafe"],
                    "citation_resolvable_rate": 0.5, "inference_ratio": 0.5,
                    "inference_heavy": False, "distinct_cited_units": 1,
                    "guard_passed": False, "guard_issues": ["citation_invalid"],
                    "guard_action": on_fail})
        if on_fail == "fallback":
            out.update({"answer": "（质量守卫回退：多单元答案引用/集中度未达标，摘录可核验知识单元）摘录", "fallback": True,
                        "citations": [{"unit_id": "u_multi_1", "page": 2, "text": "摘录"}],
                        "guarded": True})
        elif on_fail == "refuse":
            out.update({"answer": "知识库中没有足够依据回答该问题。", "citations": [],
                        "refusal": True, "confidence": {"level": "none"}})
        return out


def _fake_multi_result():
    return {"question": "问题", "answer": "【直接事实】x [u_multi_1] [deadbeefcafe]",
            "citations": [{"unit_id": "u_multi_1", "page": 2, "text": "x"},
                          {"unit_id": "deadbeefcafe", "page": 3, "text": "y"}],
            "confidence": {"level": "high"}, "fallback": False, "refusal": False,
            "cluster_count": 3, "direct_fact_markers": 1, "inference_markers": 1,
            "citation_check": {"hallucinated_ids": ["deadbeefcafe"], "citation_missing": False},
            "llm": {"model": "m", "fallback": False, "error": None},
            "generated_at": "2026-11-01T00:00:00+08:00", "elapsed_seconds": 2.0}


def _ask_with_fake_multi(monkeypatch, guard_action="fallback", guard_disabled=False,
                         guard_unavailable=False, **ask_kw):
    fake = FakeMultiModule()
    monkeypatch.setattr(qa, "_load_kb_qa_multi", lambda: fake)
    guard = FakeGuardFail()

    def _loader():
        if guard_unavailable:
            raise qa.KbQaError("guard 不可用（测试注入）")
        return guard

    monkeypatch.setattr(qa, "_load_kb_qa_guard", _loader)
    monkeypatch.setattr(qa, "_guard_kb_ids", None, raising=False)
    result = qa.ask("问题", search_fn=fake_search, chat_fn=fake_chat_ok,
                    on_guard_fail=guard_action, **ask_kw)
    return result, guard


def test_guard_default_fallback_and_payload_fields(monkeypatch):
    result, guard = _ask_with_fake_multi(monkeypatch, guard_action="fallback")
    # 默认 guard=True + on_guard_fail=fallback
    assert guard.calls == ["fallback"]
    for key in ("guard_passed", "guard_issues", "citation_valid", "citation_invalid",
                "inference_ratio", "distinct_cited_units", "guard_action", "guard_enabled"):
        assert key in result, key
    assert result["guard_enabled"] is True and result["guard_passed"] is False
    assert result["guard_action"] == "fallback"
    assert result["guard_issues"] == ["citation_invalid"]
    assert result["fallback"] is True and "质量守卫回退" in str(result["answer"])
    assert [c["unit_id"] for c in result["citations"]] == ["u_multi_1"]


def test_guard_actions_mark_and_refuse(monkeypatch):
    marked, g1 = _ask_with_fake_multi(monkeypatch, guard_action="mark")
    assert g1.calls == ["mark"] and marked["guard_action"] == "mark"
    assert marked["guard_passed"] is False and "质量守卫回退" not in str(marked["answer"])
    refused, g2 = _ask_with_fake_multi(monkeypatch, guard_action="refuse")
    assert g2.calls == ["refuse"] and refused["guard_action"] == "refuse"
    assert refused["refusal"] is True and refused["citations"] == []


def test_guard_disabled_bypass(monkeypatch):
    def boom():
        raise AssertionError("guard=False 时不应加载 guard 模块")

    monkeypatch.setattr(qa, "_load_kb_qa_multi", lambda: FakeMultiModule())
    monkeypatch.setattr(qa, "_load_kb_qa_guard", boom)
    result = qa.ask("问题", search_fn=fake_search, chat_fn=fake_chat_ok, guard=False)
    assert result["guard_enabled"] is False and result["guard_action"] == "disabled"
    assert result["guard_passed"] is None
    assert result["guard_issues"] == []
    assert "质量守卫回退" not in str(result["answer"])


def test_guard_unavailable_does_not_block(monkeypatch):
    result, _ = _ask_with_fake_multi(monkeypatch, guard_unavailable=True)
    assert result["guard_enabled"] is False
    assert result["guard_action"] == "guard_unavailable"
    assert "guard 不可用" in str(result.get("guard_error"))
    assert result["citations"]  # 原答案保留


def test_guard_invalid_action_raises(monkeypatch):
    monkeypatch.setattr(qa, "_load_kb_qa_multi", lambda: FakeMultiModule())
    with pytest.raises(qa.KbQaError):
        qa.ask("问题", search_fn=fake_search, chat_fn=fake_chat_ok, on_guard_fail="bogus")


def test_cli_guard_flags():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(QA_PATH), "ask", "--help"],
                          capture_output=True, text=True, timeout=60,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    for flag in ("--no-guard", "--on-guard-fail", "fallback"):
        assert flag in proc.stdout, flag
