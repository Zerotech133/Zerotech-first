# -*- coding: utf-8 -*-
"""kb_qa_prompt_v4（prompt v4 + guard 分段解析）契约测试 —— mock 不调真 LLM。

覆盖：
  1. prompt v4 构建：每事实行【事实】开头+引用紧随、推断【推断】≤2 条、完整示例、拒答；
  2. guard segment 分段解析：事实段/推断段标题块计数、标题行不计、行首标记优先、
     few-shot/说明/代码块跳过、无标题退化；
  3. segment 指标与守卫：分段计数注入 guard 口径后 ratio/guard_passed/fallback 正确；
  4. 结论判定：merge / expand / keep / hold；
  5. 全流程 schema + JSON/MD 落盘 + CLI（mock v4 生成）；LLM 失败降级。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V4_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_prompt_v4.py"
UNIT_IDS = {"u1", "u2"}
BOOK_IDS = set()


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_prompt_v4_under_test", V4_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pv4 = _load()


def mock_search(question, topk=8):
    return [{"unit_id": "u1", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 2, "text": "M9 覆铜板支持 224Gbps。", "fused_score": 1.0},
            {"unit_id": "u2", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 5, "text": "单颗 GPU 配套 PCB 价值量三倍。", "fused_score": 0.8}]


def arm_row(rid, status="success", guard=True, cites=2, infer=0.2, elapsed=10.0, error=None):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么", "status": status,
            "success": status == "success", "no_evidence": status == "no_evidence",
            "fallback": status == "fallback", "guard_passed": guard,
            "citations": cites, "inference_ratio": infer, "elapsed_seconds": elapsed,
            "error": error}


# ---------------------------------------------------------------------------
# 1. prompt v4
# ---------------------------------------------------------------------------
def test_build_prompt_v4_rules_and_example():
    clusters = [{"label": "M9 覆铜板", "book": "rp_a", "chapter": "章", "max_score": 1.0,
                 "units": [{"unit_id": "u1", "page": 2, "text": "M9 覆铜板支持 224Gbps。"}]}]
    prompt = pv4.build_prompt_v4("M9 覆铜板对 PCB 的影响？", clusters)
    assert "M9 覆铜板对 PCB 的影响？" in prompt
    assert "每一条事实**单独一行、以【事实】开头**" in prompt
    assert "引用紧跟" in prompt
    assert "最多 2 条" in prompt and "【推断】开头" in prompt
    assert "完整示例" in prompt and pv4.FULL_EXAMPLE in prompt
    assert "至少 2 个不同单元" in prompt
    assert "知识库中没有足够依据回答该问题" in prompt
    assert "u1" in prompt  # 材料注入


# ---------------------------------------------------------------------------
# 2. segment 分段解析
# ---------------------------------------------------------------------------
def test_segment_markers_sections():
    answer = (
        "【事实摘录】\n"
        "- M9 覆铜板支持 224Gbps（[u1] p.2）。\n"        # 无标记但属事实段 → fact
        "2. 单颗 GPU 配套 PCB 价值量三倍（[u2] p.5）。\n"  # fact
        "【推断】\n"
        "综合看价值量提升（依据：[u1] p.2、[u2] p.5）。\n"  # infer
        "【事实】补充事实（[u1] p.3）。\n"                 # 行首标记优先 → fact
    )
    direct, infer = pv4.segment_markers(answer)
    assert (direct, infer) == (3, 1)


def test_segment_markers_skip_titles_examples_code():
    answer = (
        "## 事实摘录\n"
        "【事实】真实事实一（[u1] p.2）。\n"
        "【示例】\n"
        "【事实】示例事实不应计入\n"
        "```\n"
        "【事实】代码块不应计入\n"
        "```\n"
        "【事实】真实事实二（[u2] p.5）。\n"
        "## 推断\n"
        "【推断】真实推断（依据：[u1] p.2）。\n"
    )
    assert pv4.segment_markers(answer) == (2, 1)
    # 无任何标题/段：只统计行首显式标记
    plain = "这是一段无标记说明。\n【事实】事实（[u1] p.2）。\n【推断】推断（依据：[u1]）。\n"
    assert pv4.segment_markers(plain) == (1, 1)
    # FULL_EXAMPLE 结构：2 事实 + 1 推断（含（示例完）跳过）
    assert pv4.segment_markers(pv4.FULL_EXAMPLE) == (2, 1)


# ---------------------------------------------------------------------------
# 3. segment 指标 / 守卫
# ---------------------------------------------------------------------------
def test_guard_metrics_segment_and_apply():
    answer = (
        "【事实摘录】\n"
        "【事实】M9 覆铜板支持 224Gbps（[u1] p.2）。\n"
        "【事实】单颗 GPU 配套 PCB 价值量三倍（[u2] p.5）。\n"
        "【推断】\n"
        "【推断】综合看价值量提升（依据：[u1] p.2、[u2] p.5）。\n"
    )
    raw = {"answer": answer,
           "citations": [{"unit_id": "u1"}, {"unit_id": "u2"}],
           "retrieved": [{"unit_id": "u1"}, {"unit_id": "u2"}],
           "fallback": False, "refusal": False}
    m = pv4.guard_metrics_segment(raw, UNIT_IDS, BOOK_IDS)
    assert m["direct_fact_markers"] == 2 and m["inference_markers"] == 1
    assert abs(m["inference_ratio"] - 0.3333) < 0.001
    assert m["inference_heavy"] is False
    assert m["citation_valid_count"] == 2 and m["guard_passed"] is True
    # 回退路径：无有效引用 → citation_missing + fallback
    empty = {"answer": "【事实摘录】\n【事实】无引用事实。", "citations": [], "retrieved": [],
             "fallback": False, "refusal": False}
    g = pv4.apply_guard_segment(empty, UNIT_IDS, BOOK_IDS, on_fail="fallback")
    assert g["guard_passed"] is False and "citation_missing" in g["guard_issues"]
    assert g["fallback"] is True and g["guard_action"] == "fallback"


# ---------------------------------------------------------------------------
# 4. 结论判定
# ---------------------------------------------------------------------------
def test_build_conclusion_v4_cases():
    v1 = {"total": 12, "success": 6, "no_evidence": 0, "fallback": 6,
          "answerable_rate": 0.5, "fallback_rate": 0.5, "guard_passed": 6,
          "guard_failed": 6, "guard_pass_rate": 0.5, "avg_citations": 2.3,
          "avg_inference_ratio": 0.45, "avg_elapsed_seconds": 14.0, "errors": []}
    merge = {**v1, "success": 8, "answerable_rate": 0.667, "guard_passed": 8,
             "guard_pass_rate": 0.667, "avg_inference_ratio": 0.25}
    assert pv4.build_conclusion(v1, merge)["merge_recommendation"] == "merge"
    same = {**v1, "guard_passed": 7, "guard_pass_rate": 0.583, "avg_inference_ratio": 0.3}
    assert pv4.build_conclusion(v1, same)["merge_recommendation"] in ("merge", "expand")
    better_guard = {**v1, "success": 4, "answerable_rate": 0.333, "guard_passed": 7,
                    "guard_pass_rate": 0.583}
    assert pv4.build_conclusion(v1, better_guard)["merge_recommendation"] in ("expand", "merge")
    weak = {**v1, "success": 2, "answerable_rate": 0.167, "guard_passed": 2,
            "guard_pass_rate": 0.167, "avg_inference_ratio": 0.6}
    assert pv4.build_conclusion(v1, weak)["merge_recommendation"] == "keep"
    assert pv4.build_conclusion(v1, {**v1, "errors": ["boom"]})["merge_recommendation"] == "hold"
    assert pv4.build_conclusion(v1, weak)["risks"]


# ---------------------------------------------------------------------------
# 5. 全流程（mock v4）+ 落盘 + CLI / 失败降级
# ---------------------------------------------------------------------------
def test_answer_v4_failure_fallback():
    def bad_chat(prompt, model=None, timeout=None):
        raise RuntimeError("LLM 挂起（测试）")

    res = pv4.answer_v4("问题", chat_fn=bad_chat, search_fn=mock_search)
    assert res["arm"] == "v4" and res["guard_marker_mode"] == "segment"
    assert res["fallback"] is True and res["status"] in ("fallback", "no_evidence")
    assert res["error"]


def test_run_ab_schema_with_mocks(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么"} for i in range(4)]
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    v1 = {"v1": {"rows": [{"id": f"q{i}", "status": "success", "guard_passed": True,
                           "citations": 2, "inference_ratio": 0.4, "elapsed_seconds": 9.0,
                           "answer_length": 100} for i in range(4)]}}
    p1 = tmp_path / "v1.json"
    p1.write_text(json.dumps(v1), encoding="utf-8")

    orig = pv4.answer_v4
    pv4.answer_v4 = lambda q, **kw: arm_row("v4", "success", True, 3, 0.1, 11.0)
    try:
        payload = pv4.run_ab(n=4, v3_bank=bank, v1_baseline=p1, clock=lambda: 0.0)
    finally:
        pv4.answer_v4 = orig
    assert payload["kind"] == "kb_qa_prompt_v4_ab"
    assert payload["config"]["n_actual"] == 4 and payload["config"]["guard_marker_mode"] == "segment"
    assert payload["v1"]["summary"]["total"] == 4 and payload["v4"]["summary"]["total"] == 4
    assert payload["comparison"]["merge_recommendation"] in ("merge", "expand", "keep", "hold")
    assert payload["full_example"]
    jp, mp = pv4.write_outputs(payload, tmp_path / "v4.json", tmp_path / "v4.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "A/B 对比", "逐条对比", "prompt v4 规则", "完整示例", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["n_actual"] == 4
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V4_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
