# -*- coding: utf-8 -*-
"""kb_qa_multi_ab（多单元聚合 A/B）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 样本加载：v3 问题库 50 条 + 前 12 条 single 基线；缺文件/坏文件容错；
  2. 配对统计：McNemar 精确 p、2x2 配对计数、可答率/引用/耗时比、按类别、改善/回退清单；
  3. 判定阈值：Δ≥15pp、回退不明显增多、耗时比、McNemar 显著性、样本量；
  4. run_ab（mock）：复用 v3 基线不重复调 single、逐条 two-arm 记录、schema；
  5. 分类：single/multi 状态与答案类型；JSON/MD 落盘；CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AB_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi_ab.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load_ab():
    spec = importlib.util.spec_from_file_location("kb_qa_multi_ab_under_test", AB_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ab = _load_ab()


def row(rid, single_status, multi_status, single_cit=1, multi_cit=2, single_elapsed=8.0,
        multi_elapsed=6.0, category="是什么", reused=False):
    def arm(status, cit, elapsed, re=False):
        return {"status": status, "success": status == "success",
                "refusal": status == "no_evidence", "fallback": status == "fallback",
                "citations": cit, "elapsed_seconds": elapsed, "reused": re}
    return {"id": rid, "question": f"问题 {rid}", "category": category, "theme": "AI算力/半导体",
            "single": arm(single_status, single_cit, single_elapsed, reused),
            "multi": {**arm(multi_status, multi_cit, multi_elapsed),
                      "cluster_count": 3, "answer_type": "direct_fact",
                      "direct_facts": 2, "inferences": 1},
            "improved": single_status != "success" and multi_status == "success",
            "regressed": single_status == "success" and multi_status != "success"}


# ---------------------------------------------------------------------------
# 1. 样本加载
# ---------------------------------------------------------------------------
def test_load_v3_bank_real_and_errors(tmp_path):
    bank = ab.load_v3_bank(V3_BANK)
    assert len(bank["questions"]) == 50
    assert len(bank["baseline"]) == 12
    assert bank["questions"][0]["question"]
    with_pytest = _raises(ab.AbError)
    with with_pytest:
        ab.load_v3_bank(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with _raises(ab.AbError):
        ab.load_v3_bank(bad)
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"questions": []}), encoding="utf-8")
    with _raises(ab.AbError):
        ab.load_v3_bank(empty)


# ---------------------------------------------------------------------------
# 2. McNemar + 配对统计
# ---------------------------------------------------------------------------
def test_mcnemar_exact():
    assert ab.mcnemar_exact(0, 0) == 1.0
    assert ab.mcnemar_exact(5, 5) == 1.0
    assert ab.mcnemar_exact(0, 6) == round(2 / 64, 6)  # 0.03125 显著
    assert ab.mcnemar_exact(0, 12) < 0.01
    # 不对称：改善远多于回退 → 小 p
    assert ab.mcnemar_exact(1, 12) < 0.01


def test_summarize_ab_paired_and_categories():
    rows = [
        row("r1", "no_evidence", "success", single_cit=0, multi_cit=2, category="关键数据"),
        row("r2", "no_evidence", "success", single_cit=0, multi_cit=1, category="关键数据"),
        row("r3", "success", "success", single_cit=3, multi_cit=2, category="是什么"),
        row("r4", "success", "no_evidence", single_cit=1, multi_cit=0, category="是什么"),
        row("r5", "no_evidence", "no_evidence", single_cit=0, multi_cit=0, category="风险限制"),
        row("r6", "no_evidence", "success", single_cit=0, multi_cit=2, reused=True, category="竞争格局"),
    ]
    s = ab.summarize_ab(rows)
    assert s["total"] == 6
    assert s["paired"] == {"a_both_success": 1, "b_single_fail_multi_success": 3,
                           "c_single_success_multi_fail": 1, "d_both_fail": 1,
                           "mcnemar_p": ab.mcnemar_exact(3, 1)}
    assert s["single"]["success"] == 2 and s["multi"]["success"] == 4
    assert s["single"]["rate"] == round(2 / 6, 4) and s["multi"]["rate"] == round(4 / 6, 4)
    assert s["delta"]["answerable_rate"] == round(s["multi"]["rate"] - s["single"]["rate"], 4)
    assert s["single"]["reused_count"] == 1
    assert s["improved_ids"] == ["r1", "r2", "r6"]
    assert s["regressed_ids"] == ["r4"]
    cats = {c["category"]: c for c in s["by_category"]}
    assert cats["关键数据"]["delta"] == 1.0
    assert cats["是什么"]["delta"] == -0.5


# ---------------------------------------------------------------------------
# 3. 判定阈值
# ---------------------------------------------------------------------------
def test_decide_thresholds():
    rows = [row(f"x{i}", "no_evidence", "success") for i in range(20)]
    rows += [row(f"y{i}", "success", "success") for i in range(10)]
    summary = ab.summarize_ab(rows)
    dec = ab.decide(summary, n_actual=30)
    assert dec["criteria"][0]["pass"] is True  # Δ≥15pp
    assert dec["criteria"][1]["pass"] is True  # 无回退
    assert dec["default_multi"] is True and "默认" in dec["verdict"]
    # 回退严重 → 不设默认
    bad_rows = [row(f"z{i}", "success", "no_evidence") for i in range(8)]
    bad_rows += [row(f"w{i}", "no_evidence", "success") for i in range(2)]
    bad_summary = ab.summarize_ab(bad_rows)
    bad_dec = ab.decide(bad_summary, n_actual=10)
    assert bad_dec["default_multi"] is False
    assert bad_dec["criteria"][1]["pass"] is False
    # Δ 不足 → 不切换
    flat_rows = [row(f"f{i}", "success", "success") for i in range(10)]
    flat_summary = ab.summarize_ab(flat_rows)
    flat_dec = ab.decide(flat_summary, n_actual=10)
    assert flat_dec["default_multi"] is False
    assert "不建议" in flat_dec["verdict"] or "可选" in flat_dec["verdict"]


# ---------------------------------------------------------------------------
# 4. run_ab（mock，复用基线）
# ---------------------------------------------------------------------------
def test_run_ab_reuse_and_schema(tmp_path):
    bank = {"questions": [
        {"id": f"q{i}", "question": f"问题{i}", "category": "是什么", "theme": "AI算力/半导体"}
        for i in range(4)],
        "answer_validation": {"samples": [
            {"id": "q0", "question": "问题0", "status": "no_evidence", "citations": []}]}}
    bank_path = tmp_path / "bank.json"
    bank_path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    calls = {"single": [], "multi": []}

    def single_fn(question, **kw):
        calls["single"].append(question)
        return {"answer": "答案", "citations": [{"unit_id": "u1"}],
                "confidence": {"level": "high"}, "fallback": False, "elapsed_seconds": 5.0}

    def multi_fn(question, **kw):
        calls["multi"].append(question)
        return {"answer": "【直接事实】x [u1] p.1", "citations": [{"unit_id": "u1"}],
                "confidence": {"level": "high"}, "fallback": False, "refusal": False,
                "cluster_count": 3, "direct_fact_markers": 1, "inference_markers": 1,
                "elapsed_seconds": 4.0}

    payload = ab.run_ab(n=4, k=8, reuse_v3_single=True, single_fn=single_fn,
                        multi_fn=multi_fn, bank_path=bank_path, clock=lambda: 0.0)
    assert payload["kind"] == "kb_qa_multi_ab"
    assert payload["config"]["n_actual"] == 4
    assert len(payload["comparison"]) == 4
    assert payload["comparison"][0]["single"]["reused"] is True
    assert len(calls["single"]) == 3, "复用基线的问题不应重复调用 single"
    assert len(calls["multi"]) == 4
    for key in ("summary", "decision", "comparison", "config", "disclaimer"):
        assert key in payload, key
    assert payload["decision"]["criteria"]
    # 落盘
    jp, mp = ab.write_outputs(payload, tmp_path / "ab.json", tmp_path / "ab.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论与建议", "A/B 汇总", "配对 McNemar", "判定阈值", "逐条对比"):
        assert anchor in md, anchor
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["config"]["n_actual"] == 4


# ---------------------------------------------------------------------------
# 5. 分类 + CLI
# ---------------------------------------------------------------------------
def test_classify_arms():
    s_ok = ab.classify_single({"answer": "答案", "citations": [{"unit_id": "u"}],
                               "confidence": {"level": "high"}, "fallback": False})
    assert s_ok["status"] == "success" and s_ok["answer_type"] == "direct_answer"
    s_ref = ab.classify_single({"answer": "知识库中没有足够依据回答该问题。",
                                "citations": [], "confidence": {"level": "none"}, "fallback": False})
    assert s_ref["status"] == "no_evidence" and s_ref["answer_type"] == "refusal"
    m_mix = ab.classify_multi({"answer": "x", "citations": [{"unit_id": "u"}],
                               "confidence": {"level": "high"}, "fallback": False,
                               "refusal": False, "cluster_count": 3,
                               "direct_fact_markers": 2, "inference_markers": 1})
    assert m_mix["status"] == "success" and m_mix["answer_type"] == "direct_fact+inference"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(AB_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout


class _raises:
    """轻量 pytest.raises 替代（避免额外导入）。"""

    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
