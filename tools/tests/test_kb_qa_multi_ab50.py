# -*- coding: utf-8 -*-
"""kb_qa_multi_ab50（50 条 A/B 复跑）契约测试 —— mock 不调真 LLM。

覆盖：
  1. 基线加载：v3 问题库 50 条 + AW n=30 结果（30 行）；缺文件/坏文件容错；
  2. 组合配对行：前 30 条复用 AW（零新增调用）、后 20 条现场 single+multi；meta 计数准确；
  3. n=30 对比与复现判定：Δ 差异 ≤15pp + McNemar 显著 + 回退可控 → reproduced；
  4. run_ab50 全链路 schema + JSON/MD 落盘 + CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AB50_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi_ab50.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
AW_JSON = REPO / "research" / "reports" / "kb_qa_multi_ab_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("kb_qa_multi_ab50_under_test", AB50_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ab50 = _load()


def make_v3(path: Path, n: int = 35) -> Path:
    questions = [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么" if i % 2 else "关键数据",
                  "theme": "AI算力/半导体" if i % 2 else "新能源/电池"} for i in range(n)]
    path.write_text(json.dumps({"questions": questions, "answer_validation": {"samples": []}},
                               ensure_ascii=False), encoding="utf-8")
    return path


def make_aw(path: Path, n: int = 30) -> Path:
    rows = []
    for i in range(n):
        rows.append({"id": f"q{i}", "question": f"问题{i}", "category": "是什么" if i % 2 else "关键数据",
                     "theme": "AI算力/半导体" if i % 2 else "新能源/电池",
                     "single": {"status": "no_evidence" if i % 3 else "success",
                                "success": bool(i % 3 == 0), "refusal": bool(i % 3), "fallback": False,
                                "citations": 2 if i % 3 == 0 else 0, "elapsed_seconds": 8.0},
                     "multi": {"status": "success", "success": True, "refusal": False, "fallback": False,
                               "citations": 2, "cluster_count": 3, "elapsed_seconds": 8.5,
                               "answer_type": "direct_fact", "direct_facts": 2, "inferences": 1}})
    summary = {"total": n, "single": {"success": 10, "rate": 0.3333},
               "multi": {"success": 24, "rate": 0.8},
               "delta": {"answerable_rate": 0.4667},
               "paired": {"b_single_fail_multi_success": 14, "c_single_success_multi_fail": 0,
                          "mcnemar_p": 0.000122},
               "improved_ids": [f"q{i}" for i in range(n) if i % 3],
               "regressed_ids": []}
    path.write_text(json.dumps({"comparison": rows, "summary": summary,
                                "decision": {"default_multi": True, "verdict": "建议将 multi 设为问答默认模式"}},
                               ensure_ascii=False), encoding="utf-8")
    return path


def mock_single(question, **kw):
    mock_single.calls.append(kw)
    return {"answer": "答案", "citations": [{"unit_id": "u1"}],
            "confidence": {"level": "high"}, "fallback": False, "elapsed_seconds": 1.0}


def mock_multi(question, **kw):
    mock_multi.calls.append(kw)
    return {"answer": "【直接事实】x [u1] p.1", "citations": [{"unit_id": "u1"}],
            "confidence": {"level": "high"}, "fallback": False, "refusal": False,
            "cluster_count": 3, "direct_fact_markers": 1, "inference_markers": 1,
            "citation_check": {"hallucinated_ids": [], "citation_missing": False},
            "elapsed_seconds": 1.0}


mock_single.calls = []
mock_multi.calls = []


# ---------------------------------------------------------------------------
# 1. 基线加载
# ---------------------------------------------------------------------------
def test_load_sources_real_and_errors(tmp_path):
    questions = ab50.load_v3_questions(V3_BANK)
    assert len(questions) == 50
    aw_rows, aw_summary = ab50.load_aw_rows(AW_JSON)
    assert len(aw_rows) == 30 and aw_summary
    with _raises(ab50.Ab50Error):
        ab50.load_aw_rows(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with _raises(ab50.Ab50Error):
        ab50.load_aw_rows(bad)
    with _raises(ab50.Ab50Error):
        ab50.load_v3_questions(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# 2. 组合配对行（复用 + 现场）
# ---------------------------------------------------------------------------
def test_compose_rows_reuse_and_live(tmp_path):
    v3 = make_v3(tmp_path / "v3.json", 35)
    aw = make_aw(tmp_path / "aw.json", 30)
    questions = ab50.load_v3_questions(v3)
    aw_rows, _ = ab50.load_aw_rows(aw)
    mock_single.calls.clear()
    mock_multi.calls.clear()
    rows, meta = ab50.compose_rows(questions, aw_rows, single_fn=mock_single,
                                   multi_fn=mock_multi, clock=lambda: 0.0)
    assert len(rows) == 35
    assert meta["reused_rows"] == 30
    assert meta["new_single_calls"] == 5 and meta["new_multi_calls"] == 5
    assert len(mock_single.calls) == 5 and len(mock_multi.calls) == 5
    # 前 30 行来自 AW：single/multi arms 齐全且标记复用
    for row in rows[:30]:
        assert row["reused_from_aw"] is True
        assert row["single"]["reused"] is True
        assert row["multi"]["reused"] is True
        assert "success" in row["single"] and "success" in row["multi"]
    # 后 5 行现场跑：mode 参数正确
    for row in rows[30:]:
        assert row["reused_from_aw"] is False
        assert row["single"]["reused"] is False
    assert {kw.get("mode") for kw in mock_single.calls} == {"single"}
    assert {kw.get("mode") for kw in mock_multi.calls} == {"multi"}
    for row in rows:
        assert "improved" in row and "regressed" in row


# ---------------------------------------------------------------------------
# 3. n=30 对比 / 复现判定
# ---------------------------------------------------------------------------
def test_compare_n30_reproduced():
    rows = []
    for i in range(50):
        single_ok = i < 15
        multi_ok = i < 40
        rows.append({"id": f"q{i}", "question": f"问题{i}", "category": "是什么",
                     "single": {"status": "success" if single_ok else "no_evidence",
                                "success": single_ok, "citations": 1 if single_ok else 0,
                                "elapsed_seconds": 8.0, "reused": False},
                     "multi": {"status": "success" if multi_ok else "no_evidence",
                               "success": multi_ok, "citations": 2 if multi_ok else 0,
                               "elapsed_seconds": 8.5},
                     "improved": (not single_ok) and multi_ok,
                     "regressed": single_ok and not multi_ok})
    summary = ab50.ab().summarize_ab(rows)
    decision = ab50.ab().decide(summary, 50)
    aw_summary = {"total": 30, "single": {"rate": 0.3333}, "multi": {"rate": 0.8},
                  "delta": {"answerable_rate": 0.4667},
                  "paired": {"mcnemar_p": 0.000122}, "regressed_ids": []}
    aw_decision = {"default_multi": True, "verdict": "建议 multi 默认"}
    cmp = ab50.compare_n30(summary, decision, aw_summary, aw_decision)
    assert cmp["new_n50"]["n"] == 50
    assert cmp["reproduced"] is True
    assert cmp["delta_diff"] == round(cmp["new_n50"]["delta"] - 0.4667, 4)
    # 不相似的情形 → 未复现
    aw_decision2 = {"default_multi": False, "verdict": "不设默认"}
    cmp2 = ab50.compare_n30(summary, decision, aw_summary, aw_decision2)
    assert cmp2["reproduced"] is False


# ---------------------------------------------------------------------------
# 4. run_ab50 schema + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_ab50_schema_and_outputs(tmp_path):
    v3 = make_v3(tmp_path / "v3.json", 35)
    aw = make_aw(tmp_path / "aw.json", 30)
    mock_single.calls.clear()
    mock_multi.calls.clear()
    payload = ab50.run_ab50(n=35, single_fn=mock_single, multi_fn=mock_multi,
                            v3_path=v3, aw_path=aw, clock=lambda: 0.0)
    assert payload["kind"] == "kb_qa_multi_ab50"
    assert payload["config"]["n_actual"] == 35
    assert payload["run_meta"]["llm_calls_total"] == 10
    for key in ("summary", "decision", "comparison_n30", "comparison", "run_meta"):
        assert key in payload, key
    assert len(payload["comparison"]) == 35
    jp, mp = ab50.write_outputs(payload, tmp_path / "ab50.json", tmp_path / "ab50.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "与 AW n=30 对比", "判定阈值", "按模板类别", "逐条对比"):
        assert anchor in md, anchor
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["config"]["n_actual"] == 35
    proc = subprocess.run([sys.executable, "-X", "utf8", str(AB50_PATH), "--help"],
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
