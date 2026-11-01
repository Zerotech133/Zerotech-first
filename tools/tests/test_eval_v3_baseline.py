# -*- coding: utf-8 -*-
"""tools/tests/test_eval_v3_baseline.py — 评测基线 v3 全量回归（wave21 支线 CF）。

覆盖（任务验收 ≥3）：
  1. 资产加载：v3 资产 version=3/n=102、report_header 与资产 set_id/content_hash 一致、
     hash 校验通过、全部查询有 grade-2；
  2. 纯函数评测：evaluate_queries 返回逐查询 v1/v2 指标、89/13 子集拆分、配对显著性字段；
  3. 报告 schema/MD：report_header + eval_set + summary + queries + conclusion 齐全、
     JSON 可序列化、MD 含评测集/hash/显著性/结论；
  4. 参考一致性：v3 中 v2 89 子集的 ΔP@5/ΔNDCG 与 wave12 参考值一致（实现无漂移）、
     v2 vs v1 在 v3 上仍统计显著。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_v3_baseline.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402
import eval_v3_baseline as v3b  # noqa: E402


@pytest.fixture(scope="module")
def rep() -> dict:
    return v3b.run(write=False)


# ------------------------------------------------------------------ 1. 资产加载/报告头
def test_v3_asset_and_header():
    asset = v3b.load_v3()
    assert asset["set_id"] == "eval_set_20261101_v3"
    assert asset["version"] == 3 and asset["n_queries"] == 102 == len(asset["queries"])
    assert len(asset["content_hash"]) == 64
    assert er.verify(v3b.V3_PATH)["ok"] is True
    header = v3b.rh.make_header(3)
    assert header["set_id"] == asset["set_id"]
    assert header["content_hash"] == asset["content_hash"]
    assert header["set_version"] == 3 and header["n_queries"] == 102
    assert header["hash_verified"] is True
    assert header["annotation_policy"]
    # 查询结构：graded 2 必有，id 唯一
    ids = [q["id"] for q in asset["queries"]]
    assert len(ids) == len(set(ids))
    assert all(q.get("graded", {}).get("2") for q in asset["queries"])
    new = [q for q in asset["queries"] if str(q["id"]).startswith(v3b.NEW_PREFIX)]
    assert len(new) == 13


# ------------------------------------------------------------------ 2. 纯函数评测
def test_evaluate_queries_pure():
    units = [
        {"book": "研报A", "chapter": "c1", "p1": 1, "p2": 5,
         "concept": "跨境电商增长", "claim": "跨境电商 行业 增长 态势 良好",
         "evidence": "2025 年 增长 20%"},
        {"book": "研报B", "chapter": "c2", "p1": 3, "p2": 9,
         "concept": "东盟中小微企业", "claim": "东盟 中小微 就业 占比 高",
         "evidence": "MSME 贡献 GDP"},
    ]
    queries = [
        {"id": "Q1", "q": "跨境电商 增长", "type": "主题",
         "graded": {"2": ["跨境电商增长"], "1": ["增长"]}},
        {"id": "V3-01", "q": "东盟 中小微", "type": "短语",
         "graded": {"2": ["东盟中小微企业"], "1": ["中小微"]}},
    ]
    res = v3b.evaluate_queries(units, queries, synonyms=None)
    assert len(res["rows"]) == 2
    for r in res["rows"]:
        assert {"precision_at_k", "coverage", "hit", "ndcg_at_k"} <= set(r["v1"])
        assert {"precision_at_k", "coverage", "hit", "ndcg_at_k"} <= set(r["v2"])
        assert "delta" in r
        assert r["id"] in ("Q1", "V3-01")
    assert res["subset_89"]["n"] == 1 and res["subset_13"]["n"] == 1
    assert res["overall"]["n"] == 2
    sig = res["significance"]
    assert "precision_at_k" in sig and "ndcg_at_k" in sig and "hit_rate_mcnemar" in sig
    assert isinstance(sig["significant"], bool)


# ------------------------------------------------------------------ 3. 报告 schema/MD
def test_report_schema_and_md(rep):
    for key in ("schema_version", "report_header", "eval_set", "sources",
                "engine", "summary", "queries", "conclusion"):
        assert key in rep, f"报告缺字段 {key}"
    h = rep["report_header"]
    assert h["set_id"] == "eval_set_20261101_v3"
    assert h["content_hash"] == rep["eval_set"]["content_hash"]
    assert h["hash_verified"] is True
    assert len(rep["queries"]) == 102
    s = rep["summary"]
    assert s["overall"]["n"] == 102
    assert s["subset_89"]["n"] == 89 and s["subset_13"]["n"] == 13
    assert 0.0 <= s["overall"]["v2"]["precision_at_k"] <= 1.0
    assert s["significance"]["significant"] is bool(s["significance"]["significant"])
    json.dumps(rep, ensure_ascii=False)          # 可序列化
    md = v3b.write_md(rep)
    for needle in ("评测集", rep["eval_set"]["content_hash"], "显著性", "分语法类型", "结论"):
        assert needle in md, f"MD 缺内容：{needle}"
    assert "v2_vs_v1_significant" not in md      # 给人类看的字段不应是原始 key


# ------------------------------------------------------------------ 4. 参考一致性/结论
def test_reference_consistency_and_significance(rep):
    ref = rep["reference_v2_89"]
    assert ref.get("v2_soft", {}).get("precision_at_k") == pytest.approx(0.782)
    assert ref.get("v2_soft", {}).get("ndcg_at_k") == pytest.approx(0.8479)
    s89 = rep["summary"]["subset_89"]["delta"]
    # v3 中 89 子集与 wave12 参考同查询同引擎 → 差值一致（实现无漂移）
    assert s89["precision_at_k"] == pytest.approx(0.0989, abs=1e-4)
    assert s89["ndcg_at_k"] == pytest.approx(0.0927, abs=1e-4)
    # v3 结论：仍显著
    assert rep["summary"]["significance"]["significant"] is True
    assert rep["conclusion"]["v2_vs_v1_significant"] is True
    assert "仍成立" in rep["conclusion"]["conclusion"]
    # 新增 13 条方向一致（v2 优于 v1，且 P@5 提升为正）
    s13 = rep["summary"]["subset_13"]
    assert s13["delta"]["precision_at_k"] > 0 and s13["delta"]["ndcg_at_k"] > 0
