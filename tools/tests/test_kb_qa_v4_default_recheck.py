# -*- coding: utf-8 -*-
"""kb_qa_v4_default_recheck（v4 默认灰度复评）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 选样：8 条新题（第 43-50 条）+ 12 条复测（第 13-42 条），标签正确、缺库容错；
  2. 单题复评：mock ask_fn 记录状态/guard/prompt_version/marker_mode/耗时；错误降级；
  3. 统计聚合：可答率/guard/fallback/拒答/可解析/支撑/推断/引用/耗时/灰度字段；
  4. 结论判定：stable / stable_improved / watch / rollback_risk / hold + 回滚方案；
  5. 全流程 schema + JSON/MD 落盘 + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
M_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_v4_default_recheck.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_v4_default_recheck_under_test", M_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rc = _load()


def row(rid, status="success", guard=True, resolvable=1.0, support=1.0, infer=0.2,
        cites=2, elapsed=10.0, error=None, tag="recheck"):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么", "sample_tag": tag,
            "status": status, "answerable": status == "success", "guard_passed": guard,
            "guard_action": "pass" if guard else "fallback", "guard_issues": [],
            "citations": cites, "citation_resolvable_rate": resolvable,
            "support_rate": support, "inference_ratio": infer,
            "prompt_version": "v4", "guard_marker_mode": "segment",
            "elapsed_seconds": elapsed, "error": error}


# ---------------------------------------------------------------------------
# 1. 选样
# ---------------------------------------------------------------------------
def test_select_questions_mix():
    qs = rc.select_questions(20)
    assert len(qs) == 20
    unseen = [q for q in qs if q["sample_tag"] == "unseen"]
    recheck = [q for q in qs if q["sample_tag"] == "recheck"]
    assert len(unseen) == 8 and len(recheck) == 12
    bank = json.loads(Path(V3_BANK).read_text(encoding="utf-8"))["questions"]
    unseen_ids = {q["id"] for q in unseen}
    # 新题必须来自第 43-50 条（未被前 42 条覆盖）
    assert unseen_ids == {q["id"] for q in bank[42:]}
    assert all(q.get("question") for q in qs)
    with _raises(rc.RecheckError):
        rc.select_questions(5, path=Path("nope.json"))


# ---------------------------------------------------------------------------
# 2. 单题复评 + 降级
# ---------------------------------------------------------------------------
def test_recheck_question_with_mock_and_error():
    q = {"id": "q1", "question": "问题1", "category": "是什么", "sample_tag": "unseen"}

    def mock_ask(question, **kw):
        assert kw.get("mode") == "multi" and kw.get("guard") is True
        return {"answer": "答案 [u1] p.1", "citations": [{"unit_id": "u1"}],
                "answerable": True, "fallback": False, "refusal": False,
                "guard_passed": True, "guard_action": "pass", "guard_issues": [],
                "citation_resolvable_rate": 1.0, "support_rate": 1.0,
                "inference_ratio": 0.2, "prompt_version": "v4",
                "guard_marker_mode": "segment"}

    out = rc.recheck_question(q, ask_fn=mock_ask)
    assert out["status"] == "success" and out["guard_passed"] is True
    assert out["prompt_version"] == "v4" and out["guard_marker_mode"] == "segment"
    assert out["citations"] == 1

    def bad_ask(question, **kw):
        raise TimeoutError("LLM 超时（测试）")

    out2 = rc.recheck_question(q, ask_fn=bad_ask)
    assert out2["status"] in ("fallback", "no_evidence") and out2["error"]
    assert out2["prompt_version"] is None


# ---------------------------------------------------------------------------
# 3. 统计聚合
# ---------------------------------------------------------------------------
def test_arm_stats():
    rows = [row("1", "success", True, 1.0, 1.0, 0.2, 3, 10.0),
            row("2", "fallback", False, 0.0, 0.0, 0.8, 1, 12.0),
            row("3", "no_evidence", False, 0.0, 0.0, 0.0, 0, 8.0)]
    s = rc.arm_stats(rows)
    assert s["total"] == 3 and s["success"] == 1
    assert s["answerable_rate"] == round(1 / 3, 4)
    assert s["guard_pass_rate"] == round(1 / 3, 4)
    assert s["fallback"] == 1 and s["no_evidence"] == 1
    assert s["prompt_versions"] == ["v4"]
    assert s["guard_marker_modes"] == ["segment"]
    assert s["errors"] == []


# ---------------------------------------------------------------------------
# 4. 结论判定
# ---------------------------------------------------------------------------
def test_evaluate_decision_cases():
    base = {"answerable_rate": 0.8333, "guard_pass_rate": 0.8667}
    stable = {"answerable_rate": 0.85, "guard_pass_rate": 0.88, "errors": [],
              "prompt_versions": ["v4"], "guard_marker_modes": ["segment"]}
    res = rc.evaluate(stable, base)
    assert res["decision"] in ("stable", "stable_improved")
    assert res["risk_flagged"] is False

    low = {"answerable_rate": 0.70, "guard_pass_rate": 0.75, "errors": [],
           "prompt_versions": ["v4"], "guard_marker_modes": ["segment"]}
    res2 = rc.evaluate(low, base)
    assert res2["decision"] == "rollback_risk" and res2["risk_flagged"] is True
    assert "回滚" in res2["rollback"]

    improved = {"answerable_rate": 0.95, "guard_pass_rate": 0.95, "errors": [],
                "prompt_versions": ["v4"], "guard_marker_modes": ["segment"]}
    assert rc.evaluate(improved, base)["decision"] == "stable_improved"

    watch = {"answerable_rate": 0.82, "guard_pass_rate": 0.87, "errors": [],
             "prompt_versions": ["v4"], "guard_marker_modes": ["segment"]}
    assert rc.evaluate(watch, {**base, "answerable_rate": 0.95,
                               "guard_pass_rate": 0.95})["decision"] == "watch"

    hold = {**stable, "errors": ["boom"]}
    assert rc.evaluate(hold, base)["decision"] == "hold"

    # 灰度未生效 → 风险项
    no_gray = {"answerable_rate": 0.85, "guard_pass_rate": 0.88, "errors": [],
               "prompt_versions": ["v1"], "guard_marker_modes": ["legacy"]}
    res3 = rc.evaluate(no_gray, base)
    assert any("生效校验" in r for r in res3["risks"])


# ---------------------------------------------------------------------------
# 5. 全流程 schema + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_recheck_all_schema_and_outputs(tmp_path):
    orig = rc.run_recheck
    rc.run_recheck = lambda **kw: {
        "n_requested": 2, "n_actual": 2, "skipped": 0, "model": "m", "k": 8,
        "timeout_seconds": 300, "deadline_seconds": 600,
        "rows": [row("q1", tag="unseen"), row("q2", "fallback", False, 0.5, 0.5, 0.8, 1, 12.0)],
        "elapsed_seconds": 22.0, "generated_at": "x"}
    try:
        payload = rc.run_recheck_all(n=2, k=8)
    finally:
        rc.run_recheck = orig
    assert payload["kind"] == "kb_qa_v4_default_recheck"
    assert payload["stats"]["total"] == 2
    assert "evaluation" in payload and "baseline_scale30" in payload
    jp, mp = rc.write_outputs(payload, tmp_path / "r.json", tmp_path / "r.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "复评 vs 扩样基线", "灰度生效校验", "逐条结果", "风险与回退"):
        assert anchor in md, anchor
    proc = subprocess.run([sys.executable, "-X", "utf8", str(M_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
