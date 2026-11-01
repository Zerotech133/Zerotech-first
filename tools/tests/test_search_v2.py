# -*- coding: utf-8 -*-
"""tools/tests/test_search_v2.py — 研报检索 v2（tools/kb_corpus/search_v2.py）单元测试。

覆盖（任务验收 ≥4 项，全部合成数据、不依赖 portal/static 大文件）：
  1. 多字段加权：concept/claim/evidence/chapter 权重可配，命中字段权重高者排序靠前；
  2. 同义词/别名扩展：查询别名可命中原标题词（组内整组参与匹配），parse 结果含 variants；
  3. 短语 / 多词 AND / OR / 排除词：查询语法解析与检索语义正确；
  4. 分组输出：按 (book, chapter) 聚合、组相关度排序、top_units 结构；
  5. 小评测 schema：precision@5/覆盖率计算、v1 vs v2 对比字段、md 渲染；
  6. 数据加载与 CLI：JSON / jsonl 目录加载、synonyms JSON 加载、`search --explain` 输出结构；
  7. 软 AND 合入（wave12 AH）：默认 and_mode=soft/soft_cov=0.5/synonym_weight=0.6，
     2 词 AND 默认放宽；显式 strict+synonym_weight=1.0 保持旧 v2 逐查询行为（向后兼容）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_search_v2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import search_v2 as sv  # noqa: E402


# ------------------------------------------------------------------ 合成数据
def _units(**overrides):
    base = [
        {"book": "书A", "chapter": "章节1", "concept": "光伏行业供给出清",
         "claim": "光伏行业供给出清加速，组件价格触底", "evidence": "硅料价格下跌"},
        {"book": "书A", "chapter": "章节2", "concept": "钠电池应用场景",
         "claim": "钠电池在储能领域应用拓展", "evidence": "钠离子电池成本优势"},
        {"book": "书B", "chapter": "章节X", "concept": "大模型应用落地",
         "claim": "大模型应用商业化加速", "evidence": "AI 应用百花齐放"},
        {"book": "书B", "chapter": "章节Y", "concept": "数据中心用电",
         "claim": "数据中心用电量快速上升", "evidence": "算力需求带动电力设备"},
    ]
    for k, v in overrides.items():
        idx = int(k[1:])
        base[idx].update(v)
    return base


def _engine(units=None, groups=None, weights=None, **kwargs):
    """测试用引擎：默认 **显式 strict + synonym_weight=1.0**（合入前 v2 行为），
    以便历史用例断言旧语义；新默认（soft + 0.6）由 test_default_soft_and_synonym 覆盖。"""
    syn = sv.SynonymTable(groups if groups is not None else
                          [["光伏", "太阳能"], ["钠电池", "钠离子电池"],
                           ["大模型", "AI"], ["数据中心", "IDC", "算力"]])
    params = {"and_mode": "strict", "synonym_weight": 1.0}
    params.update(kwargs)
    return sv.SearchV2(units if units is not None else _units(), synonyms=syn,
                       weights=weights, **params)


# ------------------------------------------------------------------ 1. 多字段加权
def test_weighted_fields():
    units = [
        {"book": "b", "chapter": "c", "concept": "光伏概念命中", "claim": "无关", "evidence": "无关"},
        {"book": "b", "chapter": "c", "concept": "无关", "claim": "无关",
         "evidence": "光伏 evidence 命中"},
    ]
    concept_heavy = _engine(units, groups=[], weights={
        "concept": 1.0, "claim": 0.0, "evidence": 0.1, "chapter": 0.0, "book": 0.0})
    res = concept_heavy.search_units("光伏")
    assert len(res) == 2 and res[0]["concept"] == "光伏概念命中"
    ev_heavy = _engine(units, groups=[], weights={
        "concept": 0.1, "claim": 0.0, "evidence": 1.0, "chapter": 0.0, "book": 0.0})
    res2 = ev_heavy.search_units("光伏")
    assert res2[0]["evidence"].startswith("光伏 evidence")
    assert res2[0]["score"] > res2[1]["score"]
    # 权重设 0 的字段不参与：evidence 权重 0 时该单元不命中
    no_ev = _engine(units, groups=[], weights={
        "concept": 1.0, "claim": 1.0, "evidence": 0.0, "chapter": 0.0, "book": 0.0})
    assert all(r["concept"] == "光伏概念命中" for r in no_ev.search_units("光伏"))


# ------------------------------------------------------------------ 2. 同义词扩展
def test_synonym_expansion():
    eng = _engine()
    # 查询「太阳能」→ 应命中正文只含「光伏」的单元
    res = eng.search_units("太阳能")
    assert any(r["concept"] == "光伏行业供给出清" for r in res)
    # 查询「IDC」→ 命中「数据中心/算力」单元
    res2 = eng.search_units("IDC")
    assert any(r["concept"] == "数据中心用电" for r in res2)
    # parse 展开同义词 variants
    parsed = sv.parse_query("太阳能", eng.synonyms)
    variants = parsed["or_branches"][0][0]["variants"]
    assert "光伏" in variants and "太阳能" in variants
    # 未登录词 → 仅自身
    assert eng.synonyms.expand("未知词") == ["未知词"]
    # 自定义同义词组：大小写归一、重复项去重、单词组被忽略
    syn = sv.SynonymTable([["A", "B", "A"], ["单"]])
    assert len(syn) == 1
    assert syn.expand("a") == ["a", "b"]
    # JSON 里 term 统一小写后仍可扩展
    syn2 = sv.SynonymTable([["IDC", "数据中心"]])
    assert syn2.expand("idc") == ["idc", "数据中心"]


# ------------------------------------------------------------------ 3. AND / OR / 短语 / 排除
def test_query_syntax_and_semantics():
    parsed = sv.parse_query('光伏 组件|"存款 贷款" -商誉')
    assert len(parsed["or_branches"]) == 2
    assert [t["term"] for t in parsed["or_branches"][0]] == ["光伏", "组件"]
    assert parsed["or_branches"][1][0]["term"] == "存款 贷款"
    assert parsed["or_branches"][1][0]["phrase"] is True
    assert parsed["or_branches"][1][1]["exclude"] is True
    eng = _engine()
    # AND：两个词都要命中
    assert [r["concept"] for r in eng.search_units("大模型 应用")] == ["大模型应用落地"]
    assert eng.search_units("大模型 光伏") == []
    # OR：任一分支命中
    r = eng.search_units("钠电池|光伏")
    concepts = {x["concept"] for x in r}
    assert "钠电池应用场景" in concepts and "光伏行业供给出清" in concepts
    # 排除词：-储能 过滤掉含「储能」的钠电池单元
    assert eng.search_units("钠电池 -储能") == []
    assert eng.search_units("钠电池 -大模型") != []


# ------------------------------------------------------------------ 4. 分组输出
def test_grouping():
    units = [
        {"book": "书A", "chapter": "c1", "concept": "量柱分类甲", "claim": "x", "evidence": "y"},
        {"book": "书A", "chapter": "c1", "concept": "量柱分类乙", "claim": "x", "evidence": "y"},
        {"book": "书A", "chapter": "c2", "concept": "量柱分类丙", "claim": "x", "evidence": "y"},
        {"book": "书B", "chapter": "c3", "concept": "量线定义", "claim": "x", "evidence": "y"},
    ]
    eng = _engine(units, groups=[])
    payload = eng.search("量柱", topk=3, group=True, explain=True)
    groups = payload["groups"]
    assert len(groups) >= 2
    assert groups[0]["book"] == "书A" and groups[0]["chapter"] == "c1"
    assert groups[0]["n_units"] == 2
    assert len(groups[0]["top_units"]) <= 3
    assert "concept" in groups[0]["top_units"][0]
    assert payload["matches"][0]["score"] >= payload["matches"][-1]["score"]
    assert "explain" in payload["matches"][0]
    # 不分组时无 groups 字段
    assert "groups" not in eng.search("量柱", topk=2, group=False)


# ------------------------------------------------------------------ 5. 评测 schema
def test_eval_schema(tmp_path):
    eng = _engine()
    queries = [
        {"q": "太阳能", "keywords": ["光伏"], "note": "同义词"},
        {"q": "大模型 应用", "keywords": ["大模型"], "note": "AND"},
        {"q": "IDC", "keywords": ["数据中心"], "note": "同义词"},
    ]
    rep = sv.evaluate(eng, queries, topk=2)
    assert rep["meta"]["schema_version"] == "search_v2_eval.v1"
    assert rep["meta"]["n_queries"] == 3 and rep["meta"]["topk"] == 2
    assert set(rep["aggregate"]) >= {"v1", "v2", "delta_precision", "delta_coverage"}
    for r in rep["queries"]:
        assert set(r["v1"]) >= {"precision_at_k", "coverage", "top_concepts"}
        assert 0.0 <= r["v2"]["precision_at_k"] <= 1.0
        assert 0.0 <= r["v2"]["coverage"] <= 1.0
    # v2 至少不该比 v1 差（同义词/加权在当前合成数据上应有提升或持平）
    assert rep["aggregate"]["v2"]["precision_at_k"] >= rep["aggregate"]["v1"]["precision_at_k"] - 1e-9
    md = sv.write_eval_md(rep, tmp_path / "eval.md")
    assert "precision@5" in md and "v2" in md


# ------------------------------------------------------------------ 7. 软 AND 合入默认
def test_soft_and_is_default_and_backward_compatible():
    # 默认常量
    assert sv.DEFAULT_AND_MODE == "soft"
    assert sv.DEFAULT_SOFT_COV == 0.5
    assert sv.DEFAULT_SYNONYM_WEIGHT == 0.6
    assert set(sv.AND_MODES) == {"strict", "soft", "any"}
    units = [
        {"book": "b", "chapter": "c", "concept": "快递行业", "claim": "快递单量", "evidence": "x"},
        {"book": "b", "chapter": "c", "concept": "企业盈利", "claim": "盈利提升", "evidence": "x"},
        {"book": "b", "chapter": "c", "concept": "快递盈利双主题", "claim": "快递盈利改善",
         "evidence": "x"},
    ]
    default_eng = sv.SearchV2(units, synonyms=sv.SynonymTable([]))
    assert default_eng.and_mode == "soft" and default_eng.soft_cov == 0.5
    # 默认 soft：2 词查询允许 1 词命中，且全词命中排最前
    res = default_eng.search_units("快递 盈利")
    concepts = [r["concept"] for r in res]
    assert concepts[0] == "快递盈利双主题"
    assert "快递行业" in concepts and "企业盈利" in concepts
    # 显式 strict：只返回全词命中（旧语义）
    strict_eng = sv.SearchV2(units, synonyms=sv.SynonymTable([]),
                             and_mode="strict", synonym_weight=1.0)
    assert [r["concept"] for r in strict_eng.search_units("快递 盈利")] == ["快递盈利双主题"]
    # any：最宽
    any_eng = sv.SearchV2(units, synonyms=sv.SynonymTable([]), and_mode="any")
    assert len(any_eng.search_units("快递 盈利")) >= len(res)
    # 非法参数报错
    with pytest.raises(ValueError):
        sv.SearchV2(units, and_mode="bogus")
    with pytest.raises(ValueError):
        sv.SearchV2(units, soft_cov=1.5)


def test_default_synonym_weight():
    units = [
        {"book": "b", "chapter": "c", "concept": "光伏行业", "claim": "光伏装机",
         "evidence": "x"},
        {"book": "b", "chapter": "c", "concept": "太阳能装机", "claim": "太阳能利用",
         "evidence": "x"},
    ]
    syn = sv.SynonymTable([["光伏", "太阳能"]])
    default_eng = sv.SearchV2(units, synonyms=syn)   # 默认 synonym_weight=0.6
    scores = {r["concept"]: r["score"] for r in default_eng.search_units("光伏")}
    assert scores["光伏行业"] > scores["太阳能装机"]   # 原词命中 > 同义词命中
    # 兼容模式（synonym_weight=1.0）两者同分
    compat = sv.SearchV2(units, synonyms=syn, and_mode="strict", synonym_weight=1.0)
    cs = {r["concept"]: r["score"] for r in compat.search_units("光伏")}
    assert cs["太阳能装机"] == pytest.approx(cs["光伏行业"], abs=1e-9)
    # payload 暴露默认参数，便于前端/调用方感知
    payload = default_eng.search("光伏", topk=2, group=False)
    assert payload["and_mode"] == "soft" and payload["soft_cov"] == 0.5
    assert payload["synonym_weight"] == 0.6


# ------------------------------------------------------------------ 6. 数据加载与 CLI
def test_load_units_and_cli(tmp_path, capsys):
    # JSON dict 格式
    p_json = tmp_path / "units.json"
    p_json.write_text(json.dumps({"total": 2, "units": _units()[:2]}, ensure_ascii=False),
                      encoding="utf-8")
    assert len(sv.load_units(p_json)) == 2
    # jsonl 目录格式
    d = tmp_path / "ingest" / "book1"
    d.mkdir(parents=True)
    (d / "knowledge_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in _units()[:2]),
        encoding="utf-8")
    assert len(sv.load_units(tmp_path / "ingest")) == 2
    # 同义词 JSON 文件
    p_syn = tmp_path / "syn.json"
    p_syn.write_text(json.dumps({"groups": [["光伏", "太阳能"]]}, ensure_ascii=False),
                     encoding="utf-8")
    syn = sv.load_synonyms(p_syn)
    assert len(syn) == 1 and "光伏" in syn.expand("太阳能")
    assert len(sv.load_synonyms(tmp_path / "missing.json")) == 0
    # CLI search --explain（合成数据）
    rc = sv.main(["--data", str(p_json), "--synonyms", str(p_syn),
                  "search", "--q", "太阳能", "--topk", "2", "--explain"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["query"] == "太阳能" and out["n"] >= 1
    assert "groups" in out and "matches" in out
    assert "explain" in out["matches"][0] and "matched" in out["matches"][0]["explain"]
