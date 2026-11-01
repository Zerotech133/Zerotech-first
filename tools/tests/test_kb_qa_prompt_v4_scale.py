# -*- coding: utf-8 -*-
"""kb_qa_prompt_v4_scale（prompt v4 扩样 A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 选样：跳过 wave25/29 前 12 条、条数正确、越界/缺文件容错；
  2. 单臂采集：v1（mock ask_fn）/ v4（mock chat+search）→ 逐条字段与统计；
  3. 统计聚合：可答率/guard/fallback/拒答/可解析/支撑/推断/引用/耗时/错误；
  4. 结论判定：switch_default / hold / keep_v1 + wave29 一致性 + 回滚方案；
  5. 合并两臂中间结果 → 最终 schema/JSON/MD + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCALE_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_prompt_v4_scale.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_prompt_v4_scale_under_test", SCALE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load()


def row(rid, status="success", guard=True, resolvable=1.0, support=1.0, infer=0.2,
        cites=2, elapsed=10.0, error=None):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么", "theme": "AI算力/半导体",
            "status": status, "success": status == "success",
            "no_evidence": status == "no_evidence", "fallback": status == "fallback",
            "guard_passed": guard, "citation_resolvable_rate": resolvable,
            "support_rate": support, "inference_ratio": infer, "citations": cites,
            "elapsed_seconds": elapsed, "error": error}


def mock_search(question, topk=8):
    return [{"unit_id": "u1", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 2, "text": "M9 覆铜板支持 224Gbps。", "fused_score": 1.0},
            {"unit_id": "u2", "kind": "units", "book": "b", "chapter": "章",
             "page_start": 5, "text": "单颗 GPU 配套 PCB 价值量三倍。", "fused_score": 0.8}]


# ---------------------------------------------------------------------------
# 1. 选样
# ---------------------------------------------------------------------------
def test_select_questions_skips_wave25_29_ids():
    qs = sc.select_questions(30, offset=12, path=V3_BANK)
    assert len(qs) == 30
    wave_ids = {r["id"] for r in json.loads(
        (REPO / "research/reports/kb_qa_prompt_v4_20261101.json").read_text(encoding="utf-8")
    )["v4"]["rows"]}
    assert not (wave_ids & {q["id"] for q in qs}), "扩样不应与 wave29 前 12 条重复"
    assert all(q.get("question") for q in qs)
    with _raises(sc.ScaleError):
        sc.select_questions(30, offset=50, path=V3_BANK)  # 越界（无剩余）
    with _raises(sc.ScaleError):
        sc.select_questions(5, path=Path("nope.json"))


# ---------------------------------------------------------------------------
# 2. 单臂采集（mock）
# ---------------------------------------------------------------------------
def test_run_arm_v1_with_mock_ask():
    qs = [{"id": "q1", "question": "问题1", "category": "是什么"}]

    def mock_ask(question, **kw):
        assert kw.get("mode") == "multi" and kw.get("guard") is True
        return {"answer": "答案 [u1] p.1", "citations": [{"unit_id": "u1", "page": 1}],
                "retrieved": [{"unit_id": "u1"}], "confidence": {"level": "high"},
                "fallback": False, "refusal": False, "guard_passed": True, "guard_action": "pass"}

    arm = sc.run_arm("v1", questions=qs, ask_fn=mock_ask, clock=lambda: 0.0)
    assert arm["which"] == "v1" and arm["n_actual"] == 1
    row0 = arm["rows"][0]
    assert row0["id"] == "q1" and row0["status"] == "success"
    assert "citation_resolvable_rate" in row0 and "support_rate" in row0


def test_run_arm_v4_with_mock_chat():
    guard_mod = sc.mod("kb_qa_guard")
    unit_ids, _ = guard_mod.load_kb_ids()
    ids = sorted(unit_ids)[:2]
    qs = [{"id": "q1", "question": "问题1", "category": "是什么"}]
    answer = ("【事实摘录】\n"
              f"【事实】M9 覆铜板支持 224Gbps（[{ids[0]}] p.2）。\n"
              f"【事实】单颗 GPU 配套 PCB 价值量三倍（[{ids[1]}] p.5）。\n"
              "【推断】\n"
              f"【推断】综合看价值量提升（依据：[{ids[0]}] p.2、[{ids[1]}] p.5）。\n")
    hits = [{"unit_id": ids[0], "kind": "units", "book": "b", "chapter": "章",
             "page_start": 2, "text": "M9 覆铜板支持 224Gbps。", "fused_score": 1.0},
            {"unit_id": ids[1], "kind": "units", "book": "b", "chapter": "章",
             "page_start": 5, "text": "单颗 GPU 配套 PCB 价值量三倍。", "fused_score": 0.8}]

    def mock_chat(prompt, model=None, timeout=None):
        assert "【事实摘录】" in prompt
        return answer

    arm = sc.run_arm("v4", questions=qs, chat_fn=mock_chat,
                     search_fn=lambda question, topk=8: hits, clock=lambda: 0.0)
    assert arm["which"] == "v4" and arm["n_actual"] == 1
    row0 = arm["rows"][0]
    assert row0["status"] in ("success", "fallback", "no_evidence")
    assert row0["citations"] >= 1
    assert "inference_ratio" in row0


# ---------------------------------------------------------------------------
# 3. 统计聚合
# ---------------------------------------------------------------------------
def test_arm_stats_aggregation():
    rows = [row("1", "success", True, 1.0, 1.0, 0.2, 3, 10.0),
            row("2", "fallback", False, 0.0, 0.0, 0.8, 1, 12.0),
            row("3", "no_evidence", False, 0.0, 0.0, 0.0, 0, 8.0)]
    s = sc.arm_stats(rows)
    assert s["total"] == 3 and s["success"] == 1
    assert s["answerable_rate"] == round(1 / 3, 4)
    assert s["fallback"] == 1 and s["no_evidence"] == 1
    assert s["guard_pass_rate"] == round(1 / 3, 4)
    assert s["avg_citations"] == round((3 + 1 + 0) / 3, 4)
    assert s["avg_citation_resolvable_rate"] == round((1.0 + 0 + 0) / 3, 4)


# ---------------------------------------------------------------------------
# 4. 结论 + wave29 一致性 + 合并
# ---------------------------------------------------------------------------
def test_combine_decision_cases():
    wave29 = {"n": 12, "v1_answerable_rate": 0.5, "v4_answerable_rate": 0.75,
              "delta_answerable": 0.25, "v1_guard_pass_rate": 0.5, "v4_guard_pass_rate": 0.75}
    # v1：可答 0 但质量好（可解析/支撑 100%）——用于验证「质量不降」标准
    v1_good_low = {"rows": [row(str(i), "fallback", False, 1.0, 1.0, 0.8, 1, 12.0)
                            for i in range(10)]}
    v4_better = {"rows": [row(str(i), "success", True, 1.0, 1.0, 0.2, 2, 8.0) for i in range(10)]}
    res2 = sc.combine(v1_good_low, v4_better, wave29)
    assert res2["decision"] == "switch_default"
    assert res2["meets_switch_criteria"] is True
    assert res2["wave29_consistency"]["consistent_direction"] is True
    assert "回滚" in res2["rollback"]

    # 可答率达标但质量下降（可解析/支撑 50%）→ hold
    v4_quality_drop = {"rows": [row(str(i), "success", True, 0.5, 0.5, 0.2, 2, 8.0)
                                for i in range(10)]}
    res3 = sc.combine(v1_good_low, v4_quality_drop, wave29)
    assert res3["decision"] == "hold"

    # 可答率提升 <10pp → keep_v1
    v1_mid = {"rows": [row(str(i), "success" if i < 8 else "fallback", i < 8,
                           1.0, 1.0, 0.2, 2, 12.0) for i in range(20)]}
    v4_slight = {"rows": [row(str(i), "success" if i < 9 else "fallback", i < 9,
                              1.0, 1.0, 0.2, 2, 8.0) for i in range(20)]}
    res4 = sc.combine(v1_mid, v4_slight, wave29)
    assert res4["decision"] == "keep_v1"
    assert res4["delta"]["answerable_rate"] == 0.05


def test_wave29_reference_and_combine_files(tmp_path):
    ref = sc.wave29_reference()
    if ref:
        assert ref["n"] >= 1 and "delta_answerable" in ref
    assert sc.wave29_reference(tmp_path / "nope.json") == {}
    v1 = {"which": "v1", "n_actual": 2, "n_requested": 2,
          "rows": [row("a", "success", True, 1.0, 1.0, 0.2, 2, 10.0),
                   row("b", "fallback", False, 0.4, 0.4, 0.8, 1, 12.0)], "elapsed_seconds": 22.0}
    v4 = {"which": "v4", "n_actual": 2, "n_requested": 2, "k": 8, "model": "m",
          "rows": [row("a", "success", True, 1.0, 1.0, 0.1, 2, 8.0),
                   row("b", "success", True, 1.0, 1.0, 0.2, 2, 8.0)], "elapsed_seconds": 16.0}
    p1 = tmp_path / "v1.json"
    p4 = tmp_path / "v4.json"
    p1.write_text(json.dumps(v1), encoding="utf-8")
    p4.write_text(json.dumps(v4), encoding="utf-8")
    payload = sc.combine_files(p1, p4, tmp_path / "out.json", tmp_path / "out.md")
    assert payload["kind"] == "kb_qa_prompt_v4_scale"
    assert payload["comparison"]["decision"] in ("switch_default", "hold", "keep_v1")
    md = (tmp_path / "out.md").read_text(encoding="utf-8")
    for anchor in ("结论", "两臂对比", "切默认标准校验", "风险与说明"):
        assert anchor in md, anchor
    assert sc._read_arm_partial(p1)["which"] == "v1"


# 5. 串行 run + schema + 降级 + CLI
def test_run_scale_schema_and_cli(tmp_path):

    orig_run_arm = sc.run_arm

    def fake_run_arm(which, **kw):
        rows = [row("q0", "success", True, 1.0, 1.0, 0.1, 2, 8.0)] if which == "v4" else \
               [row("q0", "fallback", False, 0.5, 0.5, 0.7, 1, 12.0)]
        return {"which": which, "n_requested": 1, "n_actual": 1, "skipped": 0,
                "model": "m", "k": 8, "timeout_seconds": 300, "deadline_seconds": 300,
                "rows": rows, "elapsed_seconds": 10.0, "generated_at": "x"}

    sc.run_arm = fake_run_arm
    try:
        payload = sc.run_scale(n=2, offset=12, clock=lambda: 0.0)
    finally:
        sc.run_arm = orig_run_arm
    assert payload["kind"] == "kb_qa_prompt_v4_scale"
    assert "comparison" in payload and payload["config"]["n_requested"] == 2
    payload2 = sc.write_outputs(payload, tmp_path / "a.json", tmp_path / "a.md")
    assert payload2[0].exists() and payload2[1].exists()
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SCALE_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "combine" in proc.stdout


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
