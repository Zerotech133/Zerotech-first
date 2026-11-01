# -*- coding: utf-8 -*-
"""citation_retry 扩样 A/B 契约测试 —— mock 不调真 LLM。

覆盖：
  1. 选样：排除 task-102 已用 11 条、条数正确、缺库容错；
  2. 单臂采集（mock _run_one）：行字段与统计（guard/可答/fallback/引用/单元/质量/耗时/重试）；
  3. 判定：switch_default / keep_off（提升不足、绝对不达标）/ hold（错误）；
  4. 合并中间结果 → 最终 schema + JSON/MD 落盘 + CLI；
  5. 透传建议/回滚字段齐全。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCALE_PATH = REPO / "tools" / "kb_corpus" / "citation_retry_scale.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("citation_retry_scale_under_test", SCALE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load()


def row(rid, guard=True, status="success", resolvable=1.0, support=1.0, infer=0.2,
        cites=2, units=2, elapsed=10.0, retry_used=False, retry_improved=False, error=None):
    return {"id": rid, "question": f"问题{rid}", "category": "是什么",
            "status": status, "success": status == "success",
            "no_evidence": status == "no_evidence", "fallback": status == "fallback",
            "guard_passed": guard, "guard_issues": [],
            "citations": cites, "distinct_units": units,
            "citation_resolvable_rate": resolvable, "support_rate": support,
            "inference_ratio": infer, "retry_used": retry_used,
            "retry_improved": retry_improved, "elapsed_seconds": elapsed, "error": error}


# ---------------------------------------------------------------------------
# 1. 选样
# ---------------------------------------------------------------------------
def test_select_questions_excludes_task102():
    used = sc.task102_used_ids()
    qs = sc.select_questions(30)
    assert len(qs) == 30
    assert len(used) == 11
    assert not ({q["id"] for q in qs} & used), "扩样应排除 task-102 已用样本"
    bank_ids = {q["id"] for q in json.loads(Path(V3_BANK).read_text(encoding="utf-8"))["questions"]}
    assert {q["id"] for q in qs} <= bank_ids
    with _raises(sc.ScaleError):
        sc.select_questions(5, path=Path("nope.json"))


# ---------------------------------------------------------------------------
# 2. 单臂 + 统计
# ---------------------------------------------------------------------------
def test_run_arm_with_mock_and_stats():
    qs = [{"id": "q1", "question": "问题1", "category": "是什么"},
          {"id": "q2", "question": "问题2", "category": "是什么"}]
    orig = sc._run_one

    def fake(question, use_retry, k, model, timeout):
        if question["id"] == "q1":
            return row("q1", True, "success", retry_used=use_retry,
                       retry_improved=use_retry, elapsed=8.0 if use_retry else 6.0)
        return row("q2", False, "fallback", resolvable=0.0, support=0.0, cites=1, units=1,
                   elapsed=12.0)

    sc._run_one = fake
    try:
        off = sc.run_arm("off", questions=qs, clock=lambda: 0.0)
        on = sc.run_arm("on", questions=qs, clock=lambda: 0.0)
    finally:
        sc._run_one = orig
    assert off["which"] == "off" and on["which"] == "on"
    s_off, s_on = sc.arm_stats(off["rows"]), sc.arm_stats(on["rows"])
    assert s_off["total"] == 2 and s_off["guard_pass_rate"] == 0.5
    assert s_off["answerable_rate"] == 0.5 and s_off["fallback_rate"] == 0.5
    assert s_on["retry_used"] == 1 and s_on["retry_improved"] == 1
    assert s_on["avg_elapsed_seconds"] > s_off["avg_elapsed_seconds"]


# ---------------------------------------------------------------------------
# 3. 判定
# ---------------------------------------------------------------------------
def test_combine_decisions():
    off_rows = [row(str(i), True) for i in range(9)] + [row("bad", False, "fallback")]
    on_rows = [row(str(i), True) for i in range(10)]
    res = sc.combine({"rows": off_rows}, {"rows": on_rows})
    assert res["delta"]["guard_pass_rate"] == 0.1
    assert res["checks"]["on_guard_ge_85"] is True
    assert res["checks"]["guard_gain_ge_8pp"] is True
    assert res["decision"] == "switch_default"
    assert res["rollout_suggestion"]["need_transparent_pass"] is True
    assert "KB_QA_CITATION_RETRY" in res["rollout_suggestion"]["env_switch"]
    assert "回滚" in res["rollout_suggestion"]["rollback"]

    # 提升 <8pp → keep_off
    off2 = [row(str(i), True) for i in range(10)] + [row("b2", False, "fallback")]
    on2 = [row(str(i), True) for i in range(11)]   # 10/11≈90.9 vs 10/11? 计算：off 10/11, on 11/11 → Δ9pp≥8
    res2 = sc.combine({"rows": off2}, {"rows": on2})
    assert res2["checks"]["guard_gain_ge_8pp"] is True
    # 绝对不达标 → keep_off
    off3 = [row(str(i), i < 4) for i in range(10)]
    on3 = [row(str(i), i < 5) for i in range(10)]
    res3 = sc.combine({"rows": off3}, {"rows": on3})
    assert res3["checks"]["on_guard_ge_85"] is False
    assert res3["decision"] == "keep_off"
    # 错误 → hold
    res4 = sc.combine({"rows": off_rows}, {"rows": on_rows, "errors": ["boom"]})
    assert res4["decision"] == "hold"


# ---------------------------------------------------------------------------
# 4. 合并 / 落盘 / schema / CLI
# ---------------------------------------------------------------------------
def test_combine_files_schema_and_outputs(tmp_path):
    off = {"which": "off", "n_requested": 3, "n_actual": 3, "skipped": 0, "k": 8,
           "model": "m", "timeout_seconds": 300, "deadline_seconds": 720,
           "rows": [row(str(i), True) for i in range(2)] + [row("x", False, "fallback")],
           "elapsed_seconds": 30.0, "generated_at": "x"}
    on = {"which": "on", "n_requested": 3, "n_actual": 3, "skipped": 0, "k": 8,
          "model": "m", "timeout_seconds": 300, "deadline_seconds": 720,
          "rows": [row(str(i), True, retry_used=i == 2, retry_improved=i == 2) for i in range(3)],
          "elapsed_seconds": 40.0, "generated_at": "x"}
    p_off = tmp_path / "off.json"
    p_on = tmp_path / "on.json"
    p_off.write_text(json.dumps(off), encoding="utf-8")
    p_on.write_text(json.dumps(on), encoding="utf-8")
    payload = sc.combine_files(p_off, p_on, tmp_path / "out.json", tmp_path / "out.md")
    assert payload["kind"] == "citation_retry_scale"
    for key in ("off_arm", "on_arm", "comparison", "config", "elapsed_seconds"):
        assert key in payload, key
    assert payload["config"]["guard_marker_mode"] == "segment"
    assert payload["comparison"]["decision"] in ("switch_default", "keep_off", "hold")
    md = (tmp_path / "out.md").read_text(encoding="utf-8")
    for anchor in ("结论", "切默认标准校验", "off vs on", "切默认透传改法建议", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["comparison"]["criteria"]
    with _raises(sc.ScaleError):
        sc.combine_files(tmp_path / "nope.json", p_on)
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
