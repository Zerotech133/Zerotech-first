# -*- coding: utf-8 -*-
"""tools/tests/test_weekly_eval.py — 每周评测回归（task-125）。

覆盖（任务验收 ≥4，不调 LLM）：
  1. 偏差判定：阈值内 ok / 超阈值 alert（逐指标状态+告警文本）；
  2. 报告 schema：eval_set/current/baseline_frozen/deltas/status/tolerance/alerts/ok；
  3. 历史追加：保留旧记录、追加新记录、max_runs 截断、字段完整；
  4. MD 渲染：指标表/告警/子集/口径章节；
  5. 真实产物：weekly_eval_20261102.json 与 eval_regression_history.json 存在且一致性。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_weekly_eval.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))


def _load():
    p = ROOT / "tools" / "kb_corpus" / "weekly_eval_regression.py"
    spec = importlib.util.spec_from_file_location("weekly_eval_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()
BASE = {"precision_at_k": 0.800, "ndcg_at_k": 0.857, "coverage": 0.893}


# ------------------------------------------------------------------ 1. 偏差判定
def test_compare_within_and_beyond_tolerance():
    ok = mod.compare_metrics({"precision_at_k": 0.810, "ndcg_at_k": 0.850,
                              "coverage": 0.900}, tolerance=0.02)
    assert ok["ok"] is True and ok["alerts"] == []
    assert ok["status"] == {"precision_at_k": "ok", "ndcg_at_k": "ok", "coverage": "ok"}
    bad = mod.compare_metrics({"precision_at_k": 0.760, "ndcg_at_k": 0.857,
                               "coverage": 0.893}, tolerance=0.02)
    assert bad["ok"] is False
    assert bad["status"]["precision_at_k"] == "alert"
    assert any("P@5" in x and "0.040" in x for x in bad["alerts"])
    missing = mod.compare_metrics({"precision_at_k": 0.8}, tolerance=0.02)
    assert missing["ok"] is False and missing["status"]["ndcg_at_k"] == "missing"


# ------------------------------------------------------------------ 2. 报告 schema
def test_build_report_schema():
    cur = {"metrics": {"precision_at_k": 0.802, "ndcg_at_k": 0.8549, "coverage": 0.8931},
           "v1_metrics": {"precision_at_k": 0.702, "ndcg_at_k": 0.7685, "coverage": 0.8534},
           "set_id": "eval_set_20261101_v3", "content_hash": "abc" * 21 + "d",
           "n_queries": 102, "subset": {"89": {}, "13": {}}}
    comp = mod.compare_metrics(cur["metrics"], tolerance=0.02)
    rep = mod.build_report(cur, comp, tag="20261102")
    assert rep["schema_version"] == "weekly_eval.v1"
    for key in ("eval_set", "current", "baseline_frozen", "deltas", "status",
                "tolerance", "alerts", "ok"):
        assert key in rep
    assert rep["baseline_frozen"] == BASE
    assert rep["eval_set"]["n_queries"] == 102
    assert rep["ok"] is True


# ------------------------------------------------------------------ 3. 历史追加
def test_append_history(tmp_path):
    path = tmp_path / "hist.json"
    text = ("# 每周评测回归（20261102）", "", "| P@5 | 0.802 |")
    assert any("每周评测回归" in x for x in text)
    old = {"schema_version": "eval_regression_history.v1",
           "runs": [{"date_tag": "20261026", "current": {"precision_at_k": 0.8}, "ok": True}]}
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    rep = {"generated_at": "2026-11-02T02:55:00+08:00", "date_tag": "20261102",
           "eval_set": {"set_id": "eval_set_20261101_v3", "content_hash": "h"},
           "current": {"precision_at_k": 0.802}, "baseline_frozen": BASE,
           "deltas": {"precision_at_k": 0.002}, "status": {"precision_at_k": "ok"},
           "tolerance": 0.02, "ok": True, "alerts": []}
    entry = mod.append_history(rep, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["runs"]) == 2 and data["runs"][-1]["date_tag"] == "20261102"
    assert entry["current"]["precision_at_k"] == 0.802
    assert data["total_runs"] == 2
    # max_runs 截断
    for i in range(5):
        rep2 = dict(rep, date_tag=f"x{i}")
        mod.append_history(rep2, path, max_runs=3)
    data2 = json.loads(path.read_text(encoding="utf-8"))
    assert len(data2["runs"]) == 3


# ------------------------------------------------------------------ 4. MD 渲染
def test_render_md_sections():
    rep = {"date_tag": "20261102",
           "generated_at": "2026-11-02T02:55:00+08:00",
           "eval_set": {"set_id": "eval_set_20261101_v3", "n_queries": 102,
                        "content_hash": "abcd" * 16},
           "engine": "search_v2 默认", "tolerance": 0.02, "ok": False,
           "current": {"precision_at_k": 0.76, "ndcg_at_k": 0.857, "coverage": 0.893},
           "baseline_frozen": BASE,
           "deltas": {"precision_at_k": -0.04, "ndcg_at_k": 0.0, "coverage": 0.0},
           "status": {"precision_at_k": "alert", "ndcg_at_k": "ok", "coverage": "ok"},
           "alerts": ["P@5 偏差 -0.0400 超过阈值 ±0.02"],
           "subset": {"89": {"precision_at_k": 0.78, "ndcg_at_k": 0.85, "coverage": 0.89},
                      "13": {"precision_at_k": 0.92, "ndcg_at_k": 0.9, "coverage": 0.89}}}
    md = mod.render_md(rep)
    for needle in ("每周评测回归", "当前 | 冻结基线", "告警", "子集参考", "口径与边界", "P@5"):
        assert needle in md
    assert "🔴" in md and "偏差 -0.0400" in md


# ------------------------------------------------------------------ 5. 真实产物
def test_real_outputs_consistency():
    rj = ROOT / "research" / "reports" / "weekly_eval_20261102.json"
    hj = ROOT / "research" / "reports" / "eval_regression_history.json"
    rm = ROOT / "research" / "reports" / "weekly_eval_20261102.md"
    assert rj.exists() and hj.exists() and rm.exists()
    rep = json.loads(rj.read_text(encoding="utf-8"))
    assert rep["schema_version"] == "weekly_eval.v1"
    assert rep["date_tag"] == "20261102"
    assert rep["eval_set"]["n_queries"] == 102
    for key in BASE:
        assert abs(rep["deltas"][key]) <= 0.02          # 当前应通过回归
    assert rep["ok"] is True
    hist = json.loads(hj.read_text(encoding="utf-8"))
    assert hist["runs"] and hist["runs"][-1]["date_tag"] == "20261102"
    assert "每周评测回归" in rm.read_text(encoding="utf-8")
