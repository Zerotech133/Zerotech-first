# -*- coding: utf-8 -*-
"""kb_qa_guard（multi 质量守卫）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 引用可解析硬门：unit 集合有效 / book alias 有效 / 未知无效；答案内引用去重解析；
  2. 推断比例阈值 + 被引单元集中度：inference_heavy / insufficient_cited_units / citation_invalid；
  3. on-guard-fail 三种策略：mark（仅标记）/ refuse（拒答）/ fallback（可核验摘录回退）+ 非法参数；
  4. 评估（run_eval mock）：guard 前后指标对比、输出 schema、JSON/MD 落盘、CLI --help；
  5. 样本加载容错。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_guard.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"

UNIT_IDS = {"aaaa1111bbbb", "cccc2222dddd", "eeee3333ffff"}
BOOK_IDS = {"rp_2026_book"}


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_guard_under_test", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gd = _load()


def raw_result(answer: str, citations: list[str], retrieved: list[str] | None = None,
               direct: int = 1, infer: int = 1, fallback: bool = False) -> dict:
    return {"question": "测试问题", "answer": answer,
            "citations": [{"unit_id": c, "book": "rp_2026_book", "chapter": "章", "page": 2,
                           "text": f"{c} 原文"} for c in citations],
            "retrieved": [{"unit_id": u, "kind": "units", "book": "rp_2026_book"}
                          for u in (retrieved if retrieved is not None else citations)],
            "direct_fact_markers": direct, "inference_markers": infer,
            "fallback": fallback, "confidence": {"level": "high"}}


# ---------------------------------------------------------------------------
# 1. 引用解析
# ---------------------------------------------------------------------------
def test_parse_and_resolve_citations():
    assert gd.parse_answer_citations("A [aaaa1111bbbb] B 【cccc2222dddd】 C [aaaa1111bbbb]") == \
        ["aaaa1111bbbb", "cccc2222dddd"]
    res = gd.resolve_citations(["aaaa1111bbbb", "rp_2026_book", "deadbeefcafe"],
                               UNIT_IDS, BOOK_IDS)
    assert res["valid"] == ["aaaa1111bbbb", "rp_2026_book"]
    assert res["invalid"] == ["deadbeefcafe"]
    assert res["aliases"] == ["rp_2026_book"]
    assert res["valid_count"] == 2 and res["total"] == 3
    # 大写/带前缀归一化
    assert gd.resolve_citations(["AAAA1111BBBB"], UNIT_IDS, BOOK_IDS)["valid"] == ["AAAA1111BBBB"]


# ---------------------------------------------------------------------------
# 2. 推断比例 + 集中度
# ---------------------------------------------------------------------------
def test_guard_metrics_thresholds():
    # 干净结果：2 个有效单元、推断比例 0.5、全部可解析 → passed
    clean = raw_result("【直接事实】A [aaaa1111bbbb]。【推断】B [cccc2222dddd]。",
                       ["aaaa1111bbbb", "cccc2222dddd"], direct=1, infer=1)
    m = gd.compute_guard_metrics(clean, UNIT_IDS, BOOK_IDS)
    assert m["citation_valid_count"] == 2 and m["citation_invalid"] == []
    assert m["citation_resolvable_rate"] == 1.0
    assert m["inference_ratio"] == 0.5 and m["inference_heavy"] is False
    assert m["distinct_cited_units"] == 2 and m["guard_passed"] is True and m["answerable"] is True

    # 幻觉引用 → citation_invalid
    bad = raw_result("A [deadbeefcafe] [aaaa1111bbbb]", ["deadbeefcafe", "aaaa1111bbbb"])
    mb = gd.compute_guard_metrics(bad, UNIT_IDS, BOOK_IDS)
    assert "citation_invalid" in mb["guard_issues"] and mb["guard_passed"] is False
    assert mb["citation_invalid"] == ["deadbeefcafe"]
    assert mb["hallucination_rate"] == 0.5

    # 推断过多 → inference_heavy
    heavy = raw_result("【推断】x [aaaa1111bbbb] [cccc2222dddd]",
                       ["aaaa1111bbbb", "cccc2222dddd"], direct=1, infer=3)
    mh = gd.compute_guard_metrics(heavy, UNIT_IDS, BOOK_IDS)
    assert mh["inference_ratio"] == 0.75 and mh["inference_heavy"] is True
    assert "inference_heavy" in mh["guard_issues"]

    # 集中度不足（只引 1 个单元）→ insufficient_cited_units
    single = raw_result("A [aaaa1111bbbb]", ["aaaa1111bbbb"])
    ms = gd.compute_guard_metrics(single, UNIT_IDS, BOOK_IDS)
    assert ms["distinct_cited_units"] == 1 and "insufficient_cited_units" in ms["guard_issues"]
    # 拒答答案 → answerable False
    refused = raw_result("知识库中没有足够依据回答该问题。", [])
    assert gd.compute_guard_metrics(refused, UNIT_IDS, BOOK_IDS)["answerable"] is False


# ---------------------------------------------------------------------------
# 3. on-guard-fail 策略
# ---------------------------------------------------------------------------
def test_guard_actions_mark_refuse_fallback():
    raw = raw_result("【直接事实】A [aaaa1111bbbb] [deadbeefcafe]",
                     ["aaaa1111bbbb", "deadbeefcafe"], direct=1, infer=0)
    marked = gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="mark")
    assert marked["guard_passed"] is False and marked["guard_action"] == "mark"
    assert marked["answer"] == raw["answer"]  # mark 不改答案
    assert marked["citation_invalid"] == ["deadbeefcafe"]

    refused = gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="refuse")
    assert refused["guard_action"] == "refuse" and refused["refusal"] is True
    assert refused["answer"] == gd.REFUSAL_ANSWER and refused["citations"] == []

    fallback = gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="fallback")
    assert fallback["guard_action"] == "fallback" and fallback["guarded"] is True
    assert fallback["answer"].startswith(gd.GUARD_FALLBACK_PREFIX)
    assert [c["unit_id"] for c in fallback["citations"]] == ["aaaa1111bbbb"]
    assert fallback["fallback"] is True

    # 通过的结果不触发动作
    clean = raw_result("A [aaaa1111bbbb] [cccc2222dddd]", ["aaaa1111bbbb", "cccc2222dddd"])
    assert gd.apply_guard(clean, UNIT_IDS, BOOK_IDS, on_fail="refuse")["guard_action"] == "pass"
    with _raises(gd.GuardError):
        gd.apply_guard(raw, UNIT_IDS, BOOK_IDS, on_fail="bogus")


# ---------------------------------------------------------------------------
# 4. 评估（mock）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_eval_with_mock_multi(tmp_path):
    calls = []

    def mock_multi(question, **kw):
        calls.append(kw.get("mode"))
        # 每条 multi 结果：2 有效引用 + 1 幻觉引用（guard 应拦截并回退）
        return raw_result("【直接事实】A [aaaa1111bbbb]。【推断】B [cccc2222dddd] [deadbeefcafe]。",
                          ["aaaa1111bbbb", "cccc2222dddd", "deadbeefcafe"],
                          retrieved=["aaaa1111bbbb", "cccc2222dddd"], direct=1, infer=1)

    payload = gd.run_eval(n=4, multi_fn=mock_multi, unit_ids=set(UNIT_IDS),
                          book_ids=set(BOOK_IDS), bank_path=V3_BANK, clock=lambda: 0.0)
    assert payload["kind"] == "kb_qa_guard_eval"
    for key in ("guard_rules", "before", "after", "delta", "issues", "comparison", "conclusion"):
        assert key in payload, key
    assert payload["config"]["n_actual"] == 4
    assert calls and set(calls) == {"multi"}
    b, a = payload["before"], payload["after"]
    assert b["citation_resolvable_rate"] < 1.0  # 原始有幻觉引用
    assert a["citation_resolvable_rate"] == 1.0  # guard 回退后可解析
    assert a["hallucination_rate"] == 0.0
    assert payload["guarded_count"] >= 1
    assert "citation_invalid" in payload["issues"]
    assert payload["conclusion"]["restored_quality"] is True
    jp, mp = gd.write_outputs(payload, tmp_path / "g.json", tmp_path / "g.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("guard 结论", "guard 前后质量对比", "逐条对比", "下一步"):
        assert anchor in md, anchor
    proc = subprocess.run([sys.executable, "-X", "utf8", str(GUARD_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout


def test_load_v3_questions_and_errors(tmp_path):
    questions = gd.load_v3_questions(V3_BANK, limit=12)
    assert len(questions) == 12 and questions[0]["question"]
    with _raises(gd.GuardError):
        gd.load_v3_questions(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with _raises(gd.GuardError):
        gd.load_v3_questions(bad)


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
