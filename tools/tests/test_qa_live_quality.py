# -*- coding: utf-8 -*-
"""8091 线上 multi 质量抽检契约测试 —— mock 不真调 8091/LLM。

覆盖：
  1. 完整度评分 completeness_score（1-5 启发式）；
  2. 单条构建 build_sample（引用/可解析/推断/guard/fallback 检测、异常响应容错）；
  3. 汇总 summarize（线上指标、与离线 v4 基线对比、结论与需修项）；
  4. 全流程 run_review（mock fetch + 固定 unit_ids）→ schema/JSON/MD 落盘 + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "kb_corpus" / "qa_live_quality_review.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"

UNITS = {"aaaa1111bbbb", "cccc2222dddd"}
ANSWER = ("【事实摘录】\n"
          "【事实】M9 级覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。\n"
          "【事实】单颗 GPU 配套 PCB 价值量约为传统方案三倍（[cccc2222dddd] p.5）。\n"
          "【推断】综合看价值量提升（依据：[aaaa1111bbbb] p.2、[cccc2222dddd] p.5）。")


def _load():
    spec = importlib.util.spec_from_file_location("qa_live_quality_under_test", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()


def envelope(answer=ANSWER, citations=None, fallback=False, refusal=False, prompt="v4",
             marker="segment", retry=True, status_meta=None):
    citations = citations if citations is not None else [{"unit_id": "aaaa1111bbbb"},
                                                         {"unit_id": "cccc2222dddd"}]
    return {"ok": True,
            "meta": {"mode": "multi", "k": 8, "clusters": 2, "topk": 8, "llm_called": True,
                     "prompt_version": prompt, "guard_marker": marker, "citation_retry": retry,
                     "citation_retry_info": {"used": False}, "cache_hit": False,
                     "guard": {"enabled": True, "applicable": True, "applied": True,
                               "on_fail": "fallback", "marker_mode": marker},
                     "rate_limit": status_meta or {"limit": 6}},
            "data": {"answer": answer, "citations": citations,
                     "retrieved": [{"unit_id": "aaaa1111bbbb"}, {"unit_id": "cccc2222dddd"}],
                     "confidence": {"level": "high"}, "refusal": refusal,
                     "fallback": fallback, "mode": "multi", "topk": 8, "k": 8}}


def question(i=0):
    return {"id": f"q{i}", "question": f"问题{i}", "category": "是什么", "theme": "AI算力/半导体"}


# ---------------------------------------------------------------------------
# 1. 完整度评分
# ---------------------------------------------------------------------------
def test_completeness_score():
    assert m.completeness_score(ANSWER, 2, 1.0, True, 0.2) == 5
    assert m.completeness_score("", 0, 0.0, False, 1.0) == 1
    assert m.completeness_score("短答案", 1, 0.0, False, 0.9) == 1
    # 80 字 + 2 引用 + guard 通过，但推断比高 → 4
    assert m.completeness_score("x" * 100, 2, 1.0, True, 0.8) == 4


# ---------------------------------------------------------------------------
# 2. 单条构建
# ---------------------------------------------------------------------------
def test_build_sample_success_and_fallback():
    s = m.build_sample(question(), 200, envelope(), 12.5, UNITS)
    assert s["status"] == "success" and s["http_status"] == 200
    assert s["citation_count"] == 2 and s["citation_resolvable_rate"] == 1.0
    assert s["inference_ratio"] > 0 and s["guard_passed_recomputed"] is True
    assert s["fallback"] is False and s["refusal"] is False
    assert s["completeness_score"] >= 4
    assert s["human_score"] is None
    assert s["verdict"].startswith("☐通过")

    fb = m.build_sample(question(), 200,
                        envelope(answer="（质量守卫回退：多单元答案引用/集中度/推断比例未达标…）\n- [aaaa1111bbbb] p.2 摘录", 
                                 fallback=True), 8.0, UNITS)
    assert fb["status"] == "fallback" and fb["fallback"] is True

    # 异常响应（非 dict / 超时）容错
    bad = m.build_sample(question(), 0, "URLError: timeout", 30.0, UNITS)
    assert bad["http_status"] == 0 and bad["answer_length"] == 0
    assert bad["status"] in ("no_evidence", "fallback")


# ---------------------------------------------------------------------------
# 3. 汇总
# ---------------------------------------------------------------------------
def test_summarize_with_baseline():
    samples = [
        m.build_sample(question(0), 200, envelope(), 12.0, UNITS),
        m.build_sample(question(1), 200, envelope(), 14.0, UNITS),
        m.build_sample(question(2), 200, envelope(fallback=True,
                                                   answer="（质量守卫回退：…）"), 8.0, UNITS),
    ]
    baseline = {"total": 30, "answerable_rate": 0.8333, "guard_pass_rate": 0.8667,
                "fallback_rate": 0.1333, "avg_citations": 2.2667,
                "avg_citation_resolvable_rate": 1.0, "avg_inference_ratio": 0.4105,
                "avg_elapsed_seconds": 23.9}
    s = m.summarize(samples, baseline)
    assert s["live"]["total"] == 3 and s["live"]["http_200"] == 3
    assert s["live"]["success"] == 2 and s["live"]["fallback"] == 1
    assert s["live"]["guard_pass_rate"] == round(2 / 3, 4)
    assert s["compare"]["answerable_rate"]["offline_v4"] == 0.8333
    assert "fixes_needed" in s and "verdict" in s
    assert s["risks"]
    # 全通过场景 → 可接受
    ok_samples = [m.build_sample(question(i), 200, envelope(), 10.0, UNITS) for i in range(3)]
    s2 = m.summarize(ok_samples, baseline)
    assert s2["live_ok"] is True and "可接受" in s2["verdict"]


# ---------------------------------------------------------------------------
# 4. 全流程（mock fetch）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_review_schema_with_mocks(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么",
                  "theme": f"主题{i}"} for i in range(3)]

    def fake_fetch(url, timeout=180):
        assert "mode=multi" in url and "cache=0" in url
        return 200, envelope(), 9.5

    payload = m.run_review(n=3, fetch_fn=fake_fetch, unit_ids=UNITS, questions=questions)
    assert payload["kind"] == "qa_live_quality_review"
    assert payload["config"]["llm_calls"] == 3
    assert len(payload["samples"]) == 3
    assert payload["summary"]["live"]["http_200"] == 3
    assert payload["summary"]["compare"]["guard_pass_rate"]
    jp, mp = m.write_outputs(payload, tmp_path / "q.json", tmp_path / "q.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "汇总", "逐条人工可审清单", "完整度评分", "人工结论", "风险"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["llm_calls"] == 3
    proc = subprocess.run([sys.executable, "-X", "utf8", str(MOD_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
