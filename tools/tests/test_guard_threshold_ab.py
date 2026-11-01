# -*- coding: utf-8 -*-
"""guard_threshold_ab（0.6 vs 0.7 扩样 A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 阈值拦截判定：inference 0.6 vs 0.7 差异、min units、invalid/missing；
  2. 离线模拟统计：通过/拦截、原因分布、代理质量（fallback 保守假设）；
  3. inference 误拦截识别（0.6 仅因 heavy 被拦、0.7 可过）；
  4. 对比结论（维持 0.7 / 回退 0.6）+ 全流程 schema + JSON/MD 落盘 + CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AB_PATH = REPO / "tools" / "kb_corpus" / "guard_threshold_ab.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("guard_threshold_ab_under_test", AB_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ab = _load()


def row(rid, answerable=True, total=2, invalid=0, distinct=2, infer=0.0,
        resolvable=1.0, support=1.0, halluc=0.0):
    return {"id": rid, "question": f"问题 {rid}", "category": "是什么", "theme": "AI算力/半导体",
            "answerable": answerable, "citation_total": total,
            "citation_invalid_count": invalid, "distinct_cited_units": distinct,
            "inference_ratio": infer, "citation_resolvable_rate": resolvable,
            "support_rate": support, "hallucination_rate": halluc, "source": "test"}


# ---------------------------------------------------------------------------
# 1. 阈值判定
# ---------------------------------------------------------------------------
def test_issues_for_thresholds():
    r = row("a", infer=0.65, distinct=2, total=2)
    assert "inference_heavy" in ab.issues_for(r, 0.6)
    assert "inference_heavy" not in ab.issues_for(r, 0.7)
    assert ab.issues_for(r, 0.7) == []
    # 其他门不受阈值影响
    assert "insufficient_cited_units" in ab.issues_for(row("b", distinct=1), 0.7)
    assert "insufficient_cited_units" in ab.issues_for(row("b", distinct=1), 0.6)
    assert "citation_invalid" in ab.issues_for(row("c", invalid=1), 0.7)
    assert "citation_missing" in ab.issues_for(row("d", total=0, distinct=0), 0.7)


# ---------------------------------------------------------------------------
# 2. 离线模拟
# ---------------------------------------------------------------------------
def test_simulate_counts_and_proxy_quality():
    rows = [
        row("r1", total=3, distinct=3, infer=0.0),
        row("r2", total=2, distinct=2, infer=0.65),   # 0.6 block(heavy), 0.7 pass
        row("r3", total=1, distinct=1, infer=0.0),    # both block (units)
    ]
    s06 = ab.simulate(rows, 0.6)
    s07 = ab.simulate(rows, 0.7)
    assert s06["passed"] == 1 and s06["blocked"] == 2
    assert s06["block_reasons"]["inference_heavy"] == 1
    assert s07["passed"] == 2 and s07["blocked"] == 1
    assert s07["block_reasons"] == {"insufficient_cited_units": 1}
    # 代理质量：block 行按 fallback（可解析 1.0）→ 整体 1.0
    assert s06["citation_resolvable_rate_post"] == 1.0
    assert s06["support_rate_post"] == 1.0
    assert s06["hallucination_rate_post"] == 0.0
    # 可答率保留：0.6 仅 r1；0.7 保留 r1+r2
    assert s06["answerable_rate_post"] == round(1 / 3, 4)
    assert s07["answerable_rate_post"] == round(2 / 3, 4)


def test_inference_false_blocks_detection():
    rows = [
        row("f1", distinct=2, infer=0.65),   # 0.6 误拦截，0.7 可过
        row("f2", distinct=1, infer=0.65),   # 0.7 仍因 units 被拦 → 不算误拦截
        row("f3", distinct=2, infer=0.8),    # 两阈值都 heavy
        row("f4", distinct=2, infer=0.0),    # 都不拦
    ]
    false_blocks = ab.inference_false_blocks(rows)
    assert [x["id"] for x in false_blocks] == ["f1"]
    cmp = ab.compare_thresholds(rows)
    assert cmp["inference_false_block_count"] == 1
    assert cmp["threshold_0_7"]["pass_rate"] > cmp["threshold_0_6"]["pass_rate"]
    assert cmp["recommended_inference_max"] == 0.7
    assert "0.7" in cmp["verdict"]
    assert cmp["reasons"] and cmp["risks"]


# ---------------------------------------------------------------------------
# 3. 全流程（mock）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_ab_schema_and_outputs(tmp_path):
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么",
                  "theme": "AI算力/半导体"} for i in range(6)]
    v3 = tmp_path / "v3.json"
    v3.write_text(json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8")
    reused_rows = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么", "theme": "AI算力/半导体",
                    "before": {"answerable": True, "citation_total": 2, "citation_invalid_count": 0,
                               "distinct_cited_units": 2, "inference_ratio": 0.65,
                               "citation_resolvable_rate": 1.0, "support_rate": 1.0,
                               "hallucination_rate": 0.0, "guard_issues": []}}
                   for i in range(3)]
    guard_json = tmp_path / "guard.json"
    guard_json.write_text(json.dumps({"comparison": reused_rows}, ensure_ascii=False), encoding="utf-8")
    calls = []

    def mock_ask(question, **kw):
        calls.append(kw.get("mode"))
        assert kw.get("guard") is True
        return {"answer": "答案", "citations": [{"unit_id": "u1"}],
                "answerable": True, "citation_total": 2, "citation_invalid_count": 0,
                "distinct_cited_units": 2, "inference_ratio": 0.0,
                "citation_resolvable_rate": 1.0, "support_rate": 1.0,
                "hallucination_rate": 0.0, "guard_passed": True, "guard_action": "pass",
                "fallback": False, "refusal": False, "mode": "multi"}

    payload = ab.run_ab(n=6, ask_fn=mock_ask, v3_bank=v3, guard_json=guard_json, clock=lambda: 0.0)
    assert payload["kind"] == "guard_threshold_ab"
    assert payload["config"]["n_actual"] == 6
    assert payload["config"]["reused_rows"] == 3 and payload["config"]["live_calls"] == 3
    assert calls and set(calls) == {"multi"}
    for key in ("threshold_0_6", "threshold_0_7", "delta", "inference_false_block_count",
                "recommended_inference_max", "verdict", "reasons", "risks"):
        assert key in payload["comparison"], key
    assert len(payload["rows"]) == 6
    jp, mp = ab.write_outputs(payload, tmp_path / "ab.json", tmp_path / "ab.md")
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "0.6 vs 0.7 对比", "逐条指标", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["n_actual"] == 6
    proc = subprocess.run([sys.executable, "-X", "utf8", str(AB_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
