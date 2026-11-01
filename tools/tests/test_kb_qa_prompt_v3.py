# -*- coding: utf-8 -*-
"""kb_qa_prompt_v3（先事实后推断 + few-shot A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. prompt v3 构建：事实摘录段在前/推断≤2 条且引用线索/few-shot/≥2 单元/拒答/材料注入；
  2. few-shot 示例结构（2 条事实 + 1 条推断）；
  3. A/B 统计复用与结论判定（merge/expand/keep/hold）；
  4. v1 结果复用解析；v3 答案 LLM 失败降级；
  5. 全流程 schema + JSON/MD 落盘 + CLI（mock v3 生成）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_prompt_v3.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_prompt_v3_under_test", V3_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pv3 = _load()


def row(rid, status="success", guard=True, cites=2, infer=0.2, elapsed=10.0, error=None):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么", "status": status,
            "success": status == "success", "no_evidence": status == "no_evidence",
            "fallback": status == "fallback", "guard_passed": guard,
            "guard_action": "pass" if guard else "fallback", "guard_issues": [],
            "citations": cites, "inference_ratio": infer, "elapsed_seconds": elapsed,
            "error": error}


# ---------------------------------------------------------------------------
# 1. prompt v3 + few-shot
# ---------------------------------------------------------------------------
def test_build_prompt_v3_rules_and_fewshot():
    clusters = [{"label": "M9 覆铜板", "book": "rp_a", "chapter": "章", "max_score": 1.0,
                 "units": [{"unit_id": "u1", "page": 2, "text": "M9 覆铜板支持 224Gbps。"}]}]
    prompt = pv3.build_prompt_v3("M9 覆铜板对 PCB 的影响？", clusters)
    assert "M9 覆铜板对 PCB 的影响？" in prompt
    assert "【事实摘录】" in prompt and "【推断】" in prompt
    assert "最多 2 条" in prompt and "线索" in prompt
    assert "至少 2 个不同单元" in prompt
    assert "知识库中没有足够依据回答该问题" in prompt
    assert pv3.FEWSHOT_EXAMPLE in prompt
    assert "u1" in prompt  # 材料注入


def test_fewshot_example_structure():
    ex = pv3.FEWSHOT_EXAMPLE
    assert "【事实】" in ex and "【推断】" in ex
    assert ex.count("【事实】") == 2, "few-shot 应有 2 条事实"
    assert ex.count("【推断】") == 1, "few-shot 应有 1 条推断"
    assert "[unit_A]" in ex and "p.2" in ex
    assert "依据：" in ex


# ---------------------------------------------------------------------------
# 2. 结论判定
# ---------------------------------------------------------------------------
def test_build_conclusion_v3_cases():
    base = {"total": 12, "success": 6, "no_evidence": 0, "fallback": 6,
            "answerable_rate": 0.5, "fallback_rate": 0.5, "guard_passed": 6,
            "guard_failed": 6, "guard_pass_rate": 0.5, "avg_citations": 2.0,
            "avg_inference_ratio": 0.4, "avg_elapsed_seconds": 10.0, "errors": []}
    better = {**base, "success": 9, "answerable_rate": 0.75, "guard_passed": 9,
              "guard_pass_rate": 0.75, "infer_ratio": 0.2}
    assert pv3.build_conclusion_v3(base, better)["merge_recommendation"] == "merge"
    slight = {**base, "success": 6, "answerable_rate": 0.5, "guard_passed": 8,
              "guard_pass_rate": 0.667, "avg_inference_ratio": 0.2, "fallback": 4}
    assert pv3.build_conclusion_v3(base, slight)["merge_recommendation"] in ("expand", "merge")
    zero = {**base, "success": 0, "answerable_rate": 0.0, "guard_passed": 1,
            "guard_pass_rate": 0.0833}
    conc = pv3.build_conclusion_v3(base, zero)
    assert conc["merge_recommendation"] == "keep"
    hold = pv3.build_conclusion_v3(base, {**base, "errors": ["boom"]})
    assert hold["merge_recommendation"] == "hold"
    assert conc["risks"] and conc["verdict"]


# ---------------------------------------------------------------------------
# 3. v1 复用 + LLM 失败降级
# ---------------------------------------------------------------------------
def test_load_v1_rows_and_v3_failure_fallback(tmp_path):
    v1json = {"v1": {"rows": [{"id": "q1", "question": "问题1", "status": "success",
                               "citations": 2, "guard_passed": True,
                               "inference_ratio": 0.0, "elapsed_seconds": 9.0}]}}
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(v1json, ensure_ascii=False), encoding="utf-8")
    rows = pv3.load_v1_rows(path)
    assert "q1" in rows and rows["q1"]["reused"] is True
    assert pv3.load_v1_rows(tmp_path / "nope.json") == {}

    def bad_chat(prompt, model=None, timeout=None):
        raise RuntimeError("LLM 挂起（测试）")

    def mock_search(question, topk=8):
        return [{"unit_id": "u1", "kind": "units", "book": "b", "chapter": "章",
                 "page_start": 2, "text": "材料原文", "fused_score": 1.0}]

    res = pv3.answer_v3("问题", k=8, chat_fn=bad_chat, search_fn=mock_search)
    assert res["arm"] == "v3"
    assert res["fallback"] is True and res["status"] in ("fallback", "no_evidence")
    assert res["error"]


# ---------------------------------------------------------------------------
# 4. 全流程（mock v3 生成）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_ab_schema_with_mocks(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么"} for i in range(3)]
    v3_bank = tmp_path / "v3.json"
    v3_bank.write_text(json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8")
    v1json = {"v1": {"rows": [{"id": f"q{i}", "question": f"问题{i}", "status": "success",
                               "citations": 2, "guard_passed": True, "inference_ratio": 0.4,
                               "elapsed_seconds": 9.0} for i in range(3)]}}
    v1_path = tmp_path / "v1.json"
    v1_path.write_text(json.dumps(v1json, ensure_ascii=False), encoding="utf-8")

    orig = pv3.answer_v3
    pv3.answer_v3 = lambda q, **kw: row("v3", "success", True, 3, 0.1, 8.0)
    try:
        payload = pv3.run_ab(n=3, v3_bank=v3_bank, v1_json=v1_path, clock=lambda: 0.0)
    finally:
        pv3.answer_v3 = orig
    assert payload["kind"] == "kb_qa_prompt_v3_ab"
    assert payload["config"]["n_actual"] == 3 and payload["config"]["live_v3"] == 3
    assert payload["v1"]["summary"]["total"] == 3 and payload["v3"]["summary"]["total"] == 3
    assert payload["comparison"]["merge_recommendation"] in ("merge", "expand", "keep", "hold")
    assert payload["fewshot_example"]
    jp, mp = pv3.write_outputs(payload, tmp_path / "v3.json", tmp_path / "v3.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "A/B 对比", "逐条对比", "prompt v3 改动点", "few-shot 示例", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["n_actual"] == 3
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V3_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
