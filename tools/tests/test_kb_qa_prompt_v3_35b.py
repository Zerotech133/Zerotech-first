# -*- coding: utf-8 -*-
"""kb_qa_prompt_v3_35b（35b 模型 prompt v3 A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 35b+v3 生成链路：复用 v3 prompt（含 few-shot/事实段规则）、同 guard strict_line、模型参数；
  2. 结论阈值：optional_model / optional_model_limited / keep_v1 / hold；
  3. 9b 基线加载（v1 rows / wave27 strict_line rows）+ 缺失容错；
  4. 全流程 schema + JSON/MD 落盘 + CLI（mock 35b 生成）；LLM 失败降级。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
M_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_prompt_v3_35b.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_prompt_v3_35b_under_test", M_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m35 = _load()


def sample_prompt_answer():
    return (
        "【事实摘录】\n"
        "【事实】M9 级覆铜板支持 224Gbps（[u1] p.2）。\n"
        "【事实】单颗 GPU 配套 PCB 价值量约为传统三倍（[u2] p.5）。\n"
        "【推断】综合看价值量提升（依据：[u1] p.2、[u2] p.5）。\n"
    )


def mock_search(question, topk=8):
    return [{"unit_id": "u1", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 2, "text": "M9 覆铜板支持 224Gbps。", "fused_score": 1.0},
            {"unit_id": "u2", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 5, "text": "单颗 GPU 配套 PCB 价值量三倍。", "fused_score": 0.8}]


# ---------------------------------------------------------------------------
# 1. 生成链路（prompt / 模型 / guard）
# ---------------------------------------------------------------------------
def test_answer_v3_35b_uses_v3_prompt_and_model():
    captured = {}

    def chat(prompt, model=None, timeout=None):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["timeout"] = timeout
        return sample_prompt_answer()

    res = m35.answer_v3_35b("M9 覆铜板对 PCB 的影响？", chat_fn=chat, search_fn=mock_search,
                            model="qwen3.6:35b-a3b", timeout=120)
    assert captured["model"] == "qwen3.6:35b-a3b"
    assert captured["timeout"] == 120
    assert "【事实摘录】" in captured["prompt"] and "few-shot" in captured["prompt"]
    assert res["arm"] == "35b_v3" and res["model"] == "qwen3.6:35b-a3b"
    assert "guard_passed" in res and "inference_ratio" in res
    assert res["citations"] >= 1


def test_answer_v3_35b_llm_failure_fallback():
    def bad_chat(prompt, model=None, timeout=None):
        raise RuntimeError("35b 超时（测试）")

    res = m35.answer_v3_35b("问题", chat_fn=bad_chat, search_fn=mock_search)
    assert res["fallback"] is True
    assert res["status"] in ("fallback", "no_evidence")
    assert res["error"]


# ---------------------------------------------------------------------------
# 2. 结论阈值
# ---------------------------------------------------------------------------
def test_compare_arms_thresholds():
    v1 = {"guard_pass_rate": 0.5, "answerable_rate": 0.5}
    v3_9b = {"guard_pass_rate": 0.1, "answerable_rate": 0.1,
             "avg_inference_ratio": 0.8, "avg_citations": 2.5, "avg_elapsed_seconds": 10.0}
    strong = {"guard_pass_rate": 0.6, "answerable_rate": 0.5,
              "avg_inference_ratio": 0.3, "avg_citations": 2.0, "avg_elapsed_seconds": 40.0,
              "errors": []}
    assert m35.compare_arms(v1, v3_9b, strong)["recommendation"] == "optional_model"
    limited = {"guard_pass_rate": 0.35, "answerable_rate": 0.3,
               "avg_inference_ratio": 0.5, "avg_citations": 2.0, "avg_elapsed_seconds": 40.0,
               "errors": []}
    assert m35.compare_arms(v1, v3_9b, limited)["recommendation"] in ("optional_model_limited", "optional_model")
    weak = {"guard_pass_rate": 0.1, "answerable_rate": 0.0, "avg_inference_ratio": 0.9,
            "avg_citations": 2.0, "avg_elapsed_seconds": 40.0, "errors": []}
    assert m35.compare_arms(v1, v3_9b, weak)["recommendation"] == "keep_v1"
    hold = {"guard_pass_rate": 0.0, "answerable_rate": 0.0, "avg_inference_ratio": 0.0,
            "avg_citations": 0, "avg_elapsed_seconds": 0, "errors": ["boom"]}
    assert m35.compare_arms(v1, v3_9b, hold)["recommendation"] == "hold"
    assert m35.compare_arms(v1, v3_9b, weak)["risks"]


# ---------------------------------------------------------------------------
# 3. 基线加载
# ---------------------------------------------------------------------------
def test_baseline_loading(tmp_path):
    v1 = {"v1": {"rows": [
        {"id": "q1", "status": "success", "guard_passed": True, "citations": 2,
         "inference_ratio": 0.0, "elapsed_seconds": 9.0},
        {"id": "q2", "status": "fallback", "guard_passed": False, "citations": 1,
         "inference_ratio": 0.8, "elapsed_seconds": 11.0}]}}
    p1 = tmp_path / "v1.json"
    p1.write_text(json.dumps(v1), encoding="utf-8")
    rows = m35.load_9b_v1_rows({"q1", "q2"}, p1)
    assert set(rows) == {"q1", "q2"} and rows["q1"]["success"] is True
    assert m35.load_9b_v1_rows({"q1"}, tmp_path / "nope.json") == {}

    v3 = {"rows": [
        {"id": "q1", "elapsed_seconds": 12.0,
         "strict_line": {"status_after": "success", "guard_passed": True,
                         "citations_after": 3, "inference_ratio": 0.0}}]}
    p3 = tmp_path / "v3.json"
    p3.write_text(json.dumps(v3), encoding="utf-8")
    rows3 = m35.load_9b_v3_rows({"q1"}, p3)
    assert rows3["q1"]["citations"] == 3 and rows3["q1"]["success"] is True
    assert "q2" not in rows3


# ---------------------------------------------------------------------------
# 4. 全流程（mock 35b）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_ab_schema_with_mocks(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么"} for i in range(3)]
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    v1 = {"v1": {"rows": [{"id": f"q{i}", "status": "success", "guard_passed": True,
                           "citations": 2, "inference_ratio": 0.0, "elapsed_seconds": 9.0}
                          for i in range(3)]}}
    p1 = tmp_path / "v1.json"
    p1.write_text(json.dumps(v1), encoding="utf-8")
    v3 = {"rows": [{"id": f"q{i}", "elapsed_seconds": 10.0,
                    "strict_line": {"status_after": "fallback", "guard_passed": False,
                                    "citations_after": 1, "inference_ratio": 0.8}}
                   for i in range(3)]}
    p3 = tmp_path / "v3.json"
    p3.write_text(json.dumps(v3), encoding="utf-8")

    orig = m35.answer_v3_35b
    m35.answer_v3_35b = lambda q, **kw: {"arm": "35b_v3", "status": "success", "success": True,
                                         "no_evidence": False, "fallback": False,
                                         "guard_passed": True, "citations": 2,
                                         "inference_ratio": 0.1, "elapsed_seconds": 40.0,
                                         "error": None}
    try:
        payload = m35.run_ab(n=3, v3_bank=bank, v1_baseline=p1, v3_baseline=p3,
                             clock=lambda: 0.0)
    finally:
        m35.answer_v3_35b = orig
    assert payload["kind"] == "kb_qa_prompt_v3_35b_ab"
    assert payload["config"]["n_actual"] == 3
    for arm in ("9b_v1", "9b_v3", "35b_v3"):
        assert arm in payload["arms"] and payload["arms"][arm]["summary"]["total"] == 3
    assert len(payload["rows"]) == 3
    assert payload["comparison"]["recommendation"] in (
        "optional_model", "optional_model_limited", "keep_v1", "hold")
    jp, mp = m35.write_outputs(payload, tmp_path / "out.json", tmp_path / "out.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "三臂对比", "逐条对比", "风险与说明"):
        assert anchor in md, anchor
    proc = subprocess.run([sys.executable, "-X", "utf8", str(M_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
