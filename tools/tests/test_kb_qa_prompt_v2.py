# -*- coding: utf-8 -*-
"""kb_qa_prompt_v2（prompt v2 A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. prompt v2 构建：引用紧跟/原文优先/推断≤40%/≥2 单元/不确定拒答/材料注入；
  2. A/B 统计：可答率/guard 通过率/fallback/引用数/推断比例/耗时；
  3. 结论判定：merge / expand / keep / hold + delta；
  4. v1 结果复用解析；v2 answer 失败降级；
  5. 全流程 schema + JSON/MD 落盘 + CLI（mock answer 函数）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V2_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_prompt_v2.py"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_prompt_v2_under_test", V2_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pv = _load()


def row(rid, status="success", guard=True, cites=2, infer=0.2, elapsed=10.0, error=None):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么", "status": status,
            "success": status == "success", "no_evidence": status == "no_evidence",
            "fallback": status == "fallback", "guard_passed": guard,
            "guard_action": "pass" if guard else "fallback", "guard_issues": [],
            "citations": cites, "inference_ratio": infer, "elapsed_seconds": elapsed,
            "error": error}


# ---------------------------------------------------------------------------
# 1. prompt v2 构建
# ---------------------------------------------------------------------------
def test_build_prompt_v2_rules_and_materials():
    clusters = [{"label": "M9 覆铜板", "book": "rp_a", "chapter": "章", "max_score": 1.0,
                 "units": [{"unit_id": "u1", "page": 2, "text": "M9 覆铜板支持 224Gbps。"},
                           {"unit_id": "u2", "page": 3, "text": "价值量提升。"}]}]
    prompt = pv.build_prompt_v2("M9 覆铜板对 PCB 的影响？", clusters)
    assert "M9 覆铜板对 PCB 的影响？" in prompt
    assert "每条论断必须紧跟" in prompt
    assert "原文" in prompt
    assert "【推断】" in prompt and "40%" in prompt
    assert "至少 2 个不同知识单元" in prompt
    assert "知识库中没有足够依据回答该问题" in prompt
    assert "u1" in prompt and "p.2" in prompt
    # 空材料也不崩
    assert "无检索材料" in pv.build_prompt_v2("q", [])


# ---------------------------------------------------------------------------
# 2. A/B 统计
# ---------------------------------------------------------------------------
def test_arm_stats_aggregation():
    rows = [
        row("1", "success", True, cites=3, infer=0.2, elapsed=10.0),
        row("2", "success", True, cites=1, infer=0.0, elapsed=12.0),
        row("3", "fallback", False, cites=2, infer=0.6, elapsed=11.0),
        row("4", "no_evidence", False, cites=0, infer=0.0, elapsed=9.0),
    ]
    s = pv.arm_stats(rows)
    assert s["total"] == 4
    assert s["success"] == 2 and s["no_evidence"] == 1 and s["fallback"] == 1
    assert s["answerable_rate"] == 0.5 and s["fallback_rate"] == 0.25
    assert s["guard_passed"] == 2 and s["guard_pass_rate"] == 0.5
    assert s["avg_citations"] == 1.5
    assert s["avg_inference_ratio"] == round((0.2 + 0 + 0.6 + 0) / 4, 4)
    assert s["avg_elapsed_seconds"] == 10.5
    assert s["errors"] == []


# ---------------------------------------------------------------------------
# 3. 结论判定
# ---------------------------------------------------------------------------
def test_build_conclusion_cases():
    base = {"total": 12, "success": 6, "no_evidence": 0, "fallback": 6,
            "answerable_rate": 0.5, "fallback_rate": 0.5, "guard_passed": 6,
            "guard_failed": 6, "guard_pass_rate": 0.5, "avg_citations": 2.0,
            "avg_inference_ratio": 0.4, "avg_elapsed_seconds": 10.0, "errors": []}
    better = {**base, "success": 10, "answerable_rate": 0.8333, "guard_passed": 10,
              "guard_pass_rate": 0.8333, "fallback": 2, "fallback_rate": 0.1667}
    assert pv.build_conclusion(base, better)["merge_recommendation"] == "merge"
    slight = {**base, "success": 8, "answerable_rate": 0.6667, "avg_inference_ratio": 0.2}
    assert pv.build_conclusion(base, slight)["merge_recommendation"] in ("expand", "merge")
    worse = {**base, "success": 4, "answerable_rate": 0.3333, "guard_passed": 4,
             "guard_pass_rate": 0.3333, "avg_inference_ratio": 0.5}
    conc = pv.build_conclusion(base, worse)
    assert conc["merge_recommendation"] == "keep"
    assert set(conc["delta"]) == {"success_rate", "guard_pass_rate",
                                  "avg_inference_ratio", "avg_elapsed_seconds"}
    hold = pv.build_conclusion(base, {**base, "errors": ["boom"]})
    assert hold["merge_recommendation"] == "hold"
    assert conc["risks"] and conc["verdict"]


# ---------------------------------------------------------------------------
# 4. v1 复用 + v2 降级
# ---------------------------------------------------------------------------
def test_load_v1_reuse_and_v2_failure_fallback(tmp_path):
    acc = {"cli": {"rows": [{"id": "q1", "question": "问题1", "category": "是什么",
                             "status": "success", "citations": 2, "guard_passed": True,
                             "inference_ratio": 0.0, "elapsed_seconds": 10.0,
                             "citation_resolvable_rate": 1.0, "distinct_cited_units": 2}]}}
    path = tmp_path / "acc.json"
    path.write_text(json.dumps(acc, ensure_ascii=False), encoding="utf-8")
    reuse = pv.load_v1_reuse(path)
    assert "q1" in reuse and reuse["q1"]["reused"] is True
    assert reuse["q1"]["success"] is True
    assert pv.load_v1_reuse(tmp_path / "nope.json") == {}

    # v2 LLM 失败 → 规则摘录降级（fallback=True），不抛异常
    def bad_chat(prompt, model=None, timeout=None):
        raise RuntimeError("LLM 挂起（测试）")

    def mock_search(question, topk=8):
        return [{"unit_id": "u1", "kind": "units", "book": "b", "chapter": "章",
                 "page_start": 2, "text": "材料原文", "fused_score": 1.0}]

    res = pv.answer_v2("问题", k=8, chat_fn=bad_chat, search_fn=mock_search)
    assert res["arm"] == "v2"
    assert res["fallback"] is True and res["status"] in ("fallback", "no_evidence")
    assert res["error"]


# ---------------------------------------------------------------------------
# 5. 全流程（mock answer 函数）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_ab_schema_with_mocks(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么",
                  "theme": "AI算力/半导体"} for i in range(4)]
    v3 = tmp_path / "v3.json"
    v3.write_text(json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8")
    acc = {"cli": {"rows": [{"id": "q0", "question": "问题0", "category": "是什么",
                             "status": "success", "citations": 2, "guard_passed": True,
                             "inference_ratio": 0.1, "elapsed_seconds": 9.0}]}}
    acc_path = tmp_path / "acc.json"
    acc_path.write_text(json.dumps(acc, ensure_ascii=False), encoding="utf-8")

    orig_v1, orig_v2 = pv.answer_v1, pv.answer_v2
    pv.answer_v1 = lambda q, **kw: row("v1", "success", True, 2, 0.4, 10.0)
    pv.answer_v2 = lambda q, **kw: row("v2", "success", True, 3, 0.2, 11.0)
    try:
        payload = pv.run_ab(n=4, reuse_v1=True, v3_bank=v3, acceptance_json=acc_path,
                            clock=lambda: 0.0)
    finally:
        pv.answer_v1, pv.answer_v2 = orig_v1, orig_v2
    assert payload["kind"] == "kb_qa_prompt_v2_ab"
    assert payload["config"]["n_actual"] == 4
    assert payload["config"]["live_v2"] == 4
    assert payload["v1"]["summary"]["total"] == 4 and payload["v2"]["summary"]["total"] == 4
    assert payload["comparison"]["merge_recommendation"] in ("merge", "expand", "keep", "hold")
    jp, mp = pv.write_outputs(payload, tmp_path / "pv2.json", tmp_path / "pv2.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "A/B 对比", "逐条对比", "prompt v2 规则", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["n_actual"] == 4
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V2_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
