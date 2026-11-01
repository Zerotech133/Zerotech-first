# -*- coding: utf-8 -*-
"""guard_threshold_study（guard 阈值敏感性）契约测试 —— 纯离线、不调 LLM。

覆盖：
  1. 拦截原因重算（inference/min_units 阈值）；
  2. 单组合模拟：通过/拦截、原因分布、代理质量（fallback 行用 after 指标）；
  3. 阈值网格（5×2=10 组合）+ 推荐（同通过率时温和阈值 0.7/2、min_units=2）；
  4. 全流程 schema + JSON/MD 落盘 + 异常容错 + CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STUDY_PATH = REPO / "tools" / "kb_corpus" / "guard_threshold_study.py"
GUARD_JSON = REPO / "research" / "reports" / "kb_qa_guard_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("guard_threshold_study_under_test", STUDY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


st = _load()


def make_row(rid, answerable=True, total=2, invalid=0, distinct=2, infer=0.0):
    before = {"answerable": answerable, "citation_total": total,
              "citation_invalid_count": invalid, "citation_resolvable_rate": 1.0 if invalid == 0 else 0.5,
              "support_rate": 1.0, "hallucination_rate": 0.0 if invalid == 0 else 0.5,
              "inference_ratio": infer, "distinct_cited_units": distinct,
              "guard_passed": invalid == 0 and distinct >= 2 and infer <= 0.6,
              "guard_issues": []}
    after = {"citation_total": 2, "citation_resolvable_rate": 1.0, "support_rate": 1.0,
             "hallucination_rate": 0.0, "inference_ratio": 0.0, "distinct_cited_units": 2}
    return {"id": rid, "question": f"问题 {rid}", "category": "是什么", "theme": "AI算力/半导体",
            "before": before, "after": after,
            "answerable": answerable, "citation_total": total, "citation_invalid": invalid,
            "citation_resolvable_rate": before["citation_resolvable_rate"],
            "support_rate": 1.0, "hallucination_rate": before["hallucination_rate"],
            "inference_ratio": infer, "inference_heavy": infer > 0.6,
            "distinct_cited_units": distinct}


# ---------------------------------------------------------------------------
# 1. 拦截原因重算
# ---------------------------------------------------------------------------
def test_classify_issues_by_thresholds():
    row = make_row("a", total=2, distinct=1, infer=0.5)
    assert "insufficient_cited_units" in st.classify_issues(row, 0.7, 2)
    assert "inference_heavy" not in st.classify_issues(row, 0.7, 2)
    # 阈值放宽到 0.5 以下 → 不 heavy
    assert "inference_heavy" not in st.classify_issues(row, 0.6, 1)
    assert st.classify_issues(row, 0.5, 1) == []  # distinct>=1, infer<=0.5
    heavy = make_row("b", infer=0.8, distinct=3)
    assert "inference_heavy" in st.classify_issues(heavy, 0.7, 2)
    missing = make_row("c", total=0, distinct=0)
    assert "citation_missing" in st.classify_issues(missing, 0.7, 2)
    invalid = make_row("d", invalid=1)
    assert "citation_invalid" in st.classify_issues(invalid, 0.7, 2)


# ---------------------------------------------------------------------------
# 2. 单组合模拟
# ---------------------------------------------------------------------------
def test_simulate_combo_counts_and_proxy_quality():
    rows = [
        make_row("r1", total=3, distinct=3, infer=0.0),        # pass
        make_row("r2", total=2, distinct=2, infer=0.5),        # pass (0.5<=0.6)
        make_row("r3", total=1, distinct=1, infer=0.5),        # block: units
        make_row("r4", total=2, distinct=2, infer=0.9),        # block: inference
    ]
    res = st.simulate_combo(rows, 0.6, 2)
    assert res["total"] == 4 and res["passed"] == 2 and res["blocked"] == 2
    assert res["pass_rate"] == 0.5
    assert res["block_reasons"] == {"insufficient_cited_units": 1, "inference_heavy": 1}
    assert res["answerable_rate_post"] == round(2 / 4, 4)  # 2 个 pass 且 answerable
    # 代理质量：pass 行可解析 1.0，block 行用 after（1.0）→ 加权仍 1.0
    assert res["citation_resolvable_rate_post"] == 1.0
    assert res["hallucination_rate_post"] == 0.0


# ---------------------------------------------------------------------------
# 3. 网格 + 推荐
# ---------------------------------------------------------------------------
def test_matrix_and_recommend():
    rows = [make_row(f"r{i}") for i in range(6)]
    matrix = st.build_matrix(rows)
    assert len(matrix["combos"]) == 5 * 2
    combo = matrix["combos"][0]
    for key in ("inference_max", "min_units", "pass_rate", "blocked", "block_reasons",
                "citation_resolvable_rate_post", "support_rate_post", "avg_inference_density_post"):
        assert key in combo, key
    rec = st.recommend(matrix, rows)
    assert rec["recommended"]["inference_max"] in st.INFERENCE_THRESHOLDS
    assert rec["recommended"]["min_units"] in st.MIN_UNITS_OPTIONS
    assert rec["risks"] and rec["conclusion"] and rec["alternatives"]
    # 本数据全 pass；同通过率时选温和阈值 0.7 / min_units 2
    assert rec["recommended"] == {"inference_max": 0.7, "min_units": 2}


# ---------------------------------------------------------------------------
# 4. 全流程 + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_study_schema_and_outputs(tmp_path):
    rows = [make_row("r1"), make_row("r2", distinct=1, infer=0.8)]
    guard = {"comparison": [{"id": r["id"], "question": r["question"], "category": r["category"],
                             "theme": r["theme"], "before": r["before"], "after": r["after"]}
                            for r in rows]}
    guard_path = tmp_path / "guard.json"
    guard_path.write_text(json.dumps(guard, ensure_ascii=False), encoding="utf-8")
    payload = st.run_study(guard_path, tmp_path / "review.json")
    assert payload["kind"] == "guard_threshold_study"
    for key in ("grid", "matrix", "recommendation", "source", "note", "disclaimer"):
        assert key in payload, key
    assert payload["source"]["rows"] == 2
    assert len(payload["matrix"]) == 10
    jp, mp = st.write_outputs(payload, tmp_path / "study.json", tmp_path / "study.md")
    md = mp.read_text(encoding="utf-8")
    for anchor in ("推荐默认", "阈值 × 通过率矩阵", "风险与后续", "结论"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["source"]["rows"] == 2
    with _raises(st.StudyError):
        st.run_study(tmp_path / "nope.json")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(STUDY_PATH), "--help"],
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
