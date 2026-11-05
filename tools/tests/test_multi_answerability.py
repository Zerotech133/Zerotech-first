# -*- coding: utf-8 -*-
"""v4d multi 可答性门控（answerability gate）契约测试 —— mock 不调真 LLM。

覆盖：
  1. build_answerability_prompt：包含问题/资料/判定标准，超长截断；
  2. parse_answerability：可答/不可答/无法识别/空响应；
  3. assess_answerability：不可答、可答、调用异常 fail-open、无 chat_fn、无材料；
  4. answer_multi：门控关闭默认 1 次生成调用；开启且不可答 → 只调判定、直接拒答；
     开启且可答 → 判定+生成；判定异常 → fail-open 继续生成；payload 含 answerability 元数据。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MULTI_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_multi_answerability_under_test", MULTI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mm = _load()

CLUSTERS = [{"label": "M9 覆铜板", "book": "b", "chapter": "章", "max_score": 1.0,
             "units": [{"unit_id": "aaaa1111bbbb", "page": 2,
                        "text": "M9 覆铜板支持 224Gbps。"},
                       {"unit_id": "cccc2222dddd", "page": 5,
                        "text": "单颗 GPU 配套 PCB 价值量三倍。"}]}]
ANSWER = "【事实】M9 覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。"


def _chat(decision: str = "可答", answer: str = ANSWER):
    """返回 (chat_fn, prompts)：判定 prompt 返回 decision，其余返回 answer。"""
    prompts: list[str] = []

    def chat(prompt: str, model=None, timeout=None) -> str:
        prompts.append(prompt)
        if "可答性判定器" in prompt:
            return decision
        return answer

    return chat, prompts


# ---------------------------------------------------------------------------
# 1. 判定 prompt
# ---------------------------------------------------------------------------
def test_build_answerability_prompt_is_domain_scope_only():
    """v2：只判问题主题领域范围，不把检索材料交给判定器。"""
    prompt = mm.build_answerability_prompt("M9 覆铜板价值量？", CLUSTERS)
    assert "M9 覆铜板价值量？" in prompt
    assert "研报知识库" in prompt
    assert "可答" in prompt and "不可答" in prompt
    assert "aaaa1111bbbb" not in prompt
    assert "资料：" not in prompt
    assert len(prompt) < 800


# ---------------------------------------------------------------------------
# 2. 判定解析
# ---------------------------------------------------------------------------
def test_parse_answerability_variants():
    assert mm.parse_answerability("不可答：资料未涉及该主题")[0] == "unanswerable"
    assert mm.parse_answerability("可答。资料直接给出结论")[0] == "answerable"
    assert mm.parse_answerability(" 不可答：无材料")[0] == "unanswerable"
    assert mm.parse_answerability("无法判断")[0] == "unknown"
    assert mm.parse_answerability("") == ("unknown", "空响应")


def test_parse_answerability_prefix_priority():
    # 前缀「不可答」优先于后续出现的「可答」子串
    assert mm.parse_answerability("不可答：资料里没有可答依据")[0] == "unanswerable"


# ---------------------------------------------------------------------------
# 3. assess_answerability
# ---------------------------------------------------------------------------
def test_assess_answerability_unanswerable():
    chat, prompts = _chat("不可答：资料未涉及量子纠缠")
    info = mm.assess_answerability("贝尔不等式？", CLUSTERS, chat, model="v4d", timeout=10)
    assert info["decision"] == "unanswerable"
    assert "量子纠缠" in info["reason"]
    assert info["error"] is None
    assert len(prompts) == 1


def test_assess_answerability_answerable():
    chat, _ = _chat("可答：材料直接给出 224Gbps")
    info = mm.assess_answerability("M9 覆铜板？", CLUSTERS, chat)
    assert info["decision"] == "answerable"


def test_assess_answerability_fail_open_on_error():
    def boom(prompt, model=None, timeout=None):
        raise RuntimeError("router down")

    info = mm.assess_answerability("问题", CLUSTERS, boom)
    assert info["decision"] == "unknown"
    assert "fail-open" in info["reason"]
    assert "router down" in (info["error"] or "")


def test_assess_answerability_without_chat_fn_is_unknown():
    info = mm.assess_answerability("问题", CLUSTERS, None)
    assert info["decision"] == "unknown"


def test_assess_answerability_no_clusters_is_unanswerable():
    info = mm.assess_answerability("问题", [], None)
    assert info["decision"] == "unanswerable"
    assert "无材料" in info["reason"]


# ---------------------------------------------------------------------------
# 4. answer_multi 集成
# ---------------------------------------------------------------------------
def test_answer_multi_gate_off_does_not_classify():
    chat, prompts = _chat()
    result = mm.answer_multi("M9 覆铜板？", clusters=CLUSTERS, chat_fn=chat,
                             citation_retry=False)
    assert len(prompts) == 1 and "可答性判定器" not in prompts[0]
    assert "answerability" not in result
    assert result["refusal"] is False


def test_answer_multi_gate_unanswerable_returns_refusal_without_generation():
    chat, prompts = _chat("不可答：资料未涉及世界杯")
    result = mm.answer_multi("2026 世界杯冠军？", clusters=CLUSTERS, chat_fn=chat,
                             citation_retry=False, answerability_gate=True)
    assert len(prompts) == 1 and "可答性判定器" in prompts[0]
    assert result["answer"] == mm.REFUSAL_ANSWER
    assert result["refusal"] is True
    assert result["confidence"]["level"] == "none"
    assert result["answerability"]["decision"] == "unanswerable"
    assert result["citations"] == []


def test_answer_multi_gate_answerable_generates_and_records_metadata():
    chat, prompts = _chat("可答：材料直接给出 224Gbps", ANSWER)
    result = mm.answer_multi("M9 覆铜板？", clusters=CLUSTERS, chat_fn=chat,
                             citation_retry=False, answerability_gate=True)
    assert len(prompts) == 2
    assert result["answer"] == ANSWER
    assert result["refusal"] is False
    assert result["answerability"]["decision"] == "answerable"


def test_answer_multi_gate_unknown_fails_open_and_generates():
    def chat(prompt, model=None, timeout=None):
        if "可答性判定器" in prompt:
            raise RuntimeError("classifier timeout")
        return ANSWER

    result = mm.answer_multi("M9 覆铜板？", clusters=CLUSTERS, chat_fn=chat,
                             citation_retry=False, answerability_gate=True)
    assert result["answer"] == ANSWER
    assert result["answerability"]["decision"] == "unknown"
    assert "classifier timeout" in (result["answerability"]["error"] or "")
