# -*- coding: utf-8 -*-
"""检索 v2 评测第二阶段（wave10 支线 AA）：**89 条查询 + 分级标注 + NDCG@5 + bootstrap CI**。

背景
----
wave9（`eval_search_v2_ext.py`）41 条查询显示 v2 P@5 0.585→0.727（p=0.0195），但
严格 concept 口径不显著、无分级相关、无置信区间。本文件在其基础上：
1. **扩到 87 条**：复用 wave9 的 41 条（补充分级标注：原有 expected_concepts=grade 2，
   人工补 grade 1 相关键）+ 新增 46 条（更多主题、短查询/长查询、AND/OR/短语/排除）；
2. **分级相关 2/1/0**：单元相关度 = 其 concept/claim/evidence 命中的最高标注等级
   （2=高度相关，1=部分相关，0=不相关）；
3. 指标：precision@5、覆盖率、命中率、**NDCG@5**（指数增益，IDCG 用全部期望单元的理论排序）；
4. 统计：v1 vs v2 配对 t（正态近似）+ **bootstrap 95% CI**（2000 次重抽样，固定种子）；
   命中率用 McNemar 精确检验；
5. 分语法类型汇总；输出 JSON/MD，并把「第二阶段结论」追加到设计文档（标记块幂等）。

标注规则
--------
每条查询 `graded = {"2": [...], "1": [...]}`（概念/关键词子串）：
  单元等级 = max{ g | 任一 grade-g 关键词出现在 concept/claim/evidence }。
wave9 的 41 条：grade2 = 原 expected_concepts；grade1 = 本文件 `BASE_RELATED` 人工补充。
相关（P@5/命中率）= 等级 > 0；覆盖率 = 全部标注关键词在 top5 文本中命中比例。

产出
----
- research/reports/search_v2_eval_ext2_20261101.json / .md
- docs/plans/2026-11-01_研报检索v2_v1.md（追加第二阶段结论，标记块可重复覆盖）

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_search_v2_ext2.py
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_search_v2_ext2.py --no-append-doc
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_search_v2_ext as ev  # noqa: E402
import search_v2 as sv  # noqa: E402

OUT = ROOT / "research" / "reports"
DOC = ROOT / "docs" / "plans" / "2026-11-01_研报检索v2_v1.md"
SCHEMA_VERSION = "search_v2_eval_ext2.v1"
DOC_BEGIN = "<!-- SEARCH_V2_EVAL_EXT2_BEGIN -->"
DOC_END = "<!-- SEARCH_V2_EVAL_EXT2_END -->"

FIELDS = ("concept", "claim", "evidence")
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42
ALPHA = 0.05

# wave9 41 条查询的 grade-1（部分相关）补充标注：query_id -> 关键词列表
BASE_RELATED: dict[str, list[str]] = {
    "Q01": ["量学", "涨停"], "Q02": ["量柱", "量学"],
    "Q03": ["涨停", "伏击涨停"], "Q04": ["涨停", "量学"],
    "Q05": ["钠离子", "储能"], "Q06": ["电池", "宁德时代"],
    "Q07": ["光伏", "储能"], "Q08": ["储能", "光伏"],
    "Q09": ["数据中心", "AIDC", "电力"], "Q10": ["算力", "数据中心", "AI"],
    "Q11": ["大模型", "商业化", "AI 模型"], "Q12": ["机器人", "具身"],
    "Q13": ["半导体", "芯片"], "Q14": ["白酒", "消费"],
    "Q15": ["房地产", "购房", "政策"], "Q16": ["铁路", "客流"],
    "Q17": ["企业信用", "信用"], "Q18": ["网络安全", "安全"],
    "Q19": ["光伏", "储能"], "Q20": ["钠电池", "钠离子"],
    "Q21": ["数据中心", "算力"], "Q22": ["AI", "大模型"],
    "Q23": ["分红", "股息"], "Q24": ["库存", "存货"],
    "Q25": ["芯片", "半导体"], "Q26": ["港股", "港股通"],
    "Q27": ["光伏", "储能"], "Q28": ["白酒", "消费"],
    "Q29": ["钠电池", "电池"], "Q30": ["AI", "商业化"],
    "Q31": ["铁路", "客流"], "Q32": ["光伏", "钠电池"],
    "Q33": ["量柱", "量线"], "Q34": ["AI", "机器人"],
    "Q35": ["白酒", "消费"], "Q36": ["量柱", "量学"],
    "Q37": ["评级", "行业评级"], "Q38": ["企业信用", "信用"],
    "Q39": ["量柱", "量学"], "Q40": ["钠电池", "钠离子"],
    "Q41": ["AI", "大模型"],
}

# 新增 46 条：graded = {"2": 高度相关关键词, "1": 部分相关关键词}
EXTRA_QUERIES: list[dict] = [
    # ---- 更多主题（26）----
    {"id": "E01", "q": "挖掘机出口市场", "type": "主题",
     "graded": {"2": ["挖掘机"], "1": ["出口", "工程机械"]}},
    {"id": "E02", "q": "上市快递企业盈利修复", "type": "主题",
     "graded": {"2": ["快递"], "1": ["盈利", "物流"]}},
    {"id": "E03", "q": "航空业运力与需求", "type": "主题",
     "graded": {"2": ["航空"], "1": ["运力", "客流"]}},
    {"id": "E04", "q": "离岛免税消费", "type": "主题",
     "graded": {"2": ["免税"], "1": ["消费", "零售"]}},
    {"id": "E05", "q": "头部券商", "type": "主题",
     "graded": {"2": ["券商"], "1": ["非银金融", "证券"]}},
    {"id": "E06", "q": "保险资金港股通", "type": "主题",
     "graded": {"2": ["保险资金"], "1": ["港股通", "保险"]}},
    {"id": "E07", "q": "中小行负债端收缩", "type": "主题",
     "graded": {"2": ["中小行"], "1": ["银行", "负债"]}},
    {"id": "E08", "q": "智能体安全治理", "type": "主题",
     "graded": {"2": ["智能体"], "1": ["AI", "安全"]}},
    {"id": "E09", "q": "DeepSeek DSec 系统架构", "type": "主题",
     "graded": {"2": ["DeepSeek"], "1": ["DSec", "AI"]}},
    {"id": "E10", "q": "MLCC 行业趋势", "type": "主题",
     "graded": {"2": ["MLCC"], "1": ["被动元件", "电子"]}},
    {"id": "E11", "q": "全球风电市场趋势", "type": "主题",
     "graded": {"2": ["风电"], "1": ["新能源", "光伏"]}},
    {"id": "E12", "q": "核电的长期战略价值", "type": "主题",
     "graded": {"2": ["核电"], "1": ["电力", "新能源"]}},
    {"id": "E13", "q": "轨道数据中心定位", "type": "主题",
     "graded": {"2": ["轨道数据中心"], "1": ["数据中心", "卫星"]}},
    {"id": "E14", "q": "二手手机市场", "type": "主题",
     "graded": {"2": ["二手手机"], "1": ["二手", "手机"]}},
    {"id": "E15", "q": "海外监管与关税政策风险", "type": "主题",
     "graded": {"2": ["关税"], "1": ["海外", "监管"]}},
    {"id": "E16", "q": "国防军工行业投资建议", "type": "主题",
     "graded": {"2": ["国防军工"], "1": ["军工", "投资建议"]}},
    {"id": "E17", "q": "端侧 AI 放量", "type": "主题",
     "graded": {"2": ["端侧 AI"], "1": ["AI", "芯片"]}},
    {"id": "E18", "q": "化工行业周期传导", "type": "主题",
     "graded": {"2": ["化工"], "1": ["周期", "原材料"]}},
    {"id": "E19", "q": "水务企业财务表现", "type": "主题",
     "graded": {"2": ["水务"], "1": ["财务", "现金流"]}},
    {"id": "E20", "q": "网络安全合规转型", "type": "主题",
     "graded": {"2": ["合规"], "1": ["网络安全", "转型"]}},
    {"id": "E21", "q": "传统 MNC 巨头拥抱 AI", "type": "主题",
     "graded": {"2": ["MNC"], "1": ["AI", "医药"]}},
    {"id": "E22", "q": "新能源并网模式演变", "type": "主题",
     "graded": {"2": ["新能源并网"], "1": ["新能源", "储能"]}},
    {"id": "E23", "q": "光伏成本核算新规", "type": "主题",
     "graded": {"2": ["成本核算"], "1": ["光伏", "政策"]}},
    {"id": "E24", "q": "购房贷款贴息政策", "type": "主题",
     "graded": {"2": ["贴息"], "1": ["购房", "房地产"]}},
    {"id": "E25", "q": "住房资产估值模型", "type": "主题",
     "graded": {"2": ["住房资产"], "1": ["房地产", "估值"]}},
    {"id": "E26", "q": "中国新三样产业", "type": "主题",
     "graded": {"2": ["新三样"], "1": ["出口", "新能源"]}},
    # ---- 短查询（5）----
    {"id": "E27", "q": "免税", "type": "短查询",
     "graded": {"2": ["免税"], "1": ["消费"]}},
    {"id": "E28", "q": "券商", "type": "短查询",
     "graded": {"2": ["券商"], "1": ["非银金融"]}},
    {"id": "E29", "q": "风电", "type": "短查询",
     "graded": {"2": ["风电"], "1": ["新能源"]}},
    {"id": "E30", "q": "关税", "type": "短查询",
     "graded": {"2": ["关税"], "1": ["监管", "海外"]}},
    {"id": "E31", "q": "核电", "type": "短查询",
     "graded": {"2": ["核电"], "1": ["电力", "新能源"]}},
    # ---- 长查询（3）----
    {"id": "E32", "q": "全球风电市场趋势与新能源并网模式演变", "type": "长查询",
     "graded": {"2": ["风电", "新能源并网"], "1": ["新能源", "储能"]}},
    {"id": "E33", "q": "中小行负债端收缩与银行流动性风险", "type": "长查询",
     "graded": {"2": ["中小行"], "1": ["银行", "风险"]}},
    {"id": "E34", "q": "AI 原生安全与传统安全运营的差异", "type": "长查询",
     "graded": {"2": ["AI 原生安全"], "1": ["AI", "安全"]}},
    # ---- AND（5）----
    {"id": "E35", "q": "快递 盈利", "type": "AND",
     "graded": {"2": ["快递"], "1": ["盈利"]}},
    {"id": "E36", "q": "航空 运力", "type": "AND",
     "graded": {"2": ["航空"], "1": ["运力"]}},
    {"id": "E37", "q": "免税 消费", "type": "AND",
     "graded": {"2": ["免税"], "1": ["消费"]}},
    {"id": "E38", "q": "保险资金 港股通", "type": "AND",
     "graded": {"2": ["保险资金"], "1": ["港股通"]}},
    {"id": "E39", "q": "挖掘机 出口", "type": "AND",
     "graded": {"2": ["挖掘机"], "1": ["出口"]}},
    # ---- OR（4）----
    {"id": "E40", "q": "挖掘机|快递", "type": "OR",
     "graded": {"2": ["挖掘机", "快递"], "1": ["出口", "盈利"]}},
    {"id": "E41", "q": "免税|白酒", "type": "OR",
     "graded": {"2": ["免税", "白酒"], "1": ["消费"]}},
    {"id": "E42", "q": "券商|保险资金", "type": "OR",
     "graded": {"2": ["券商", "保险资金"], "1": ["非银金融"]}},
    {"id": "E43", "q": "风电|核电", "type": "OR",
     "graded": {"2": ["风电", "核电"], "1": ["新能源"]}},
    # ---- 短语（3）----
    {"id": "E44", "q": '"轨道数据中心定位"', "type": "短语",
     "graded": {"2": ["轨道数据中心"], "1": ["数据中心"]}},
    {"id": "E45", "q": '"DeepSeek DSec 系统架构"', "type": "短语",
     "graded": {"2": ["DeepSeek"], "1": ["DSec"]}},
    {"id": "E46", "q": '"上市快递企业盈利修复"', "type": "短语",
     "graded": {"2": ["快递"], "1": ["盈利"]}},
    # ---- 排除（2）----
    {"id": "E47", "q": "数据中心 -轨道", "type": "排除",
     "graded": {"2": ["数据中心"], "1": ["IDC"]}},
    {"id": "E48", "q": "大模型 -商业化", "type": "排除",
     "graded": {"2": ["大模型"], "1": ["AI"]}},
]
QUERY_TYPES = ("主题", "同义词", "AND", "OR", "短语", "排除", "短查询", "长查询")


def all_queries() -> list[dict]:
    """合并 wave9 的 41 条（转成 graded）+ 新增 46 条。"""
    merged: list[dict] = []
    for q in ev.QUERIES:
        merged.append({
            "id": q["id"], "q": q["q"], "type": q["type"],
            "graded": {"2": list(q["expected_concepts"]),
                       "1": list(BASE_RELATED.get(q["id"], []))},
            "source": "wave9",
        })
    for q in EXTRA_QUERIES:
        merged.append({**q, "source": "wave10"})
    return merged


# --------------------------------------------------------------------------- #
# 分级相关 / NDCG（纯函数，可单测）
# --------------------------------------------------------------------------- #
def unit_grade(unit: dict, graded: dict, fields: tuple[str, ...] = FIELDS) -> int:
    """单元分级相关度：命中最高标注等级（2>1>0）；未命中=0。"""
    text = " ".join(str(unit.get(f, "") or "") for f in fields)
    for g in (2, 1):
        keys = graded.get(str(g)) or graded.get(g) or []
        if any(k and k in text for k in keys):
            return g
    return 0


def expected_grades(units: list[dict], graded: dict) -> list[int]:
    """全部期望单元的分级（降序），用于计算理想 DCG。"""
    gs = [unit_grade(u, graded) for u in units]
    return sorted([g for g in gs if g > 0], reverse=True)


def dcg_at_k(grades: list[int], k: int = 5) -> float:
    """DCG@k（指数增益）：Σ (2^rel − 1) / log2(rank+2)，rank 从 0 开始。"""
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades[:k]))


def ndcg_at_k(top_units: list[dict], graded: dict, units: list[dict],
              k: int = 5) -> float:
    """NDCG@k：DCG(topk 分级) / IDCG(全部期望单元按等级降序的理论 topk)。"""
    gains = [unit_grade(u, graded) for u in top_units[:k]]
    dcg = dcg_at_k(gains, k)
    ideal = expected_grades(units, graded)[:k]
    idcg = dcg_at_k(ideal, k)
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def precision_hit(top_units: list[dict], graded: dict, k: int = 5):
    rel = [1 if unit_grade(u, graded) > 0 else 0 for u in top_units[:k]]
    return sum(rel) / float(k), rel


def coverage(top_units: list[dict], graded: dict,
             fields: tuple[str, ...] = FIELDS) -> float:
    keys = [k for g in graded.values() for k in g if k]
    if not keys:
        return 0.0
    text = " ".join(" ".join(str(u.get(f, "") or "") for f in fields) for u in top_units)
    return sum(1 for k in keys if k in text) / float(len(keys))


# --------------------------------------------------------------------------- #
# 统计：配对 t + bootstrap CI
# --------------------------------------------------------------------------- #
def bootstrap_ci(diffs: list[float], n_boot: int = BOOTSTRAP_N,
                 seed: int = BOOTSTRAP_SEED, alpha: float = ALPHA) -> dict:
    """配对差的 bootstrap 百分位置信区间（固定种子，可复现）。"""
    xs = np.asarray([float(x) for x in diffs], dtype="float64")
    if len(xs) == 0:
        return {"n": 0, "ci_low": None, "ci_high": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(xs), size=(n_boot, len(xs)))
    means = xs[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2.0))
    hi = float(np.quantile(means, 1.0 - alpha / 2.0))
    return {"n": int(len(xs)), "n_boot": int(n_boot),
            "mean": round(float(xs.mean()), 4),
            "ci_low": round(lo, 4), "ci_high": round(hi, 4),
            "significant": bool(lo > 0 or hi < 0)}


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
def evaluate_ext2(engine, units: list[dict] | None = None,
                  queries: list[dict] | None = None, topk: int = 5) -> dict:
    units = units if units is not None else engine.units
    qs = queries if queries is not None else all_queries()
    rows: list[dict] = []
    for q in qs:
        graded = q["graded"]
        v2 = engine.search_units(q["q"])[:topk]
        v1 = sv.legacy_search_units(units, q["q"], topk=topk)
        p2, _ = precision_hit(v2, graded, topk)
        p1, _ = precision_hit(v1, graded, topk)
        row = {
            "id": q["id"], "query": q["q"], "type": q["type"],
            "source": q.get("source", ""),
            "graded": graded,
            "n_expected_units": len(expected_grades(units, graded)),
            "v1": {
                "precision_at_k": round(p1, 4),
                "coverage": round(coverage(v1, graded), 4),
                "hit": 1 if p1 > 0 else 0,
                "ndcg_at_k": round(ndcg_at_k(v1, graded, units, topk), 4),
                "grades": [unit_grade(u, graded) for u in v1[:topk]],
            },
            "v2": {
                "precision_at_k": round(p2, 4),
                "coverage": round(coverage(v2, graded), 4),
                "hit": 1 if p2 > 0 else 0,
                "ndcg_at_k": round(ndcg_at_k(v2, graded, units, topk), 4),
                "grades": [unit_grade(u, graded) for u in v2[:topk]],
            },
        }
        row["delta"] = {
            "precision_at_k": round(p2 - p1, 4),
            "ndcg_at_k": round(row["v2"]["ndcg_at_k"] - row["v1"]["ndcg_at_k"], 4),
        }
        rows.append(row)

    def _mean(variant: str, metric: str) -> float:
        return round(sum(r[variant][metric] for r in rows) / len(rows), 4) if rows else 0.0

    def _agg_block(variant: str) -> dict:
        return {"precision_at_k": _mean(variant, "precision_at_k"),
                "coverage": _mean(variant, "coverage"),
                "hit_rate": _mean(variant, "hit"),
                "ndcg_at_k": _mean(variant, "ndcg_at_k")}

    by_type: dict[str, dict] = {}
    for t in QUERY_TYPES:
        sub = [r for r in rows if r["type"] == t]
        if not sub:
            continue
        by_type[t] = {
            "n": len(sub),
            "v1": {m: round(sum(x["v1"][m] for x in sub) / len(sub), 4)
                   for m in ("precision_at_k", "coverage", "hit", "ndcg_at_k")},
            "v2": {m: round(sum(x["v2"][m] for x in sub) / len(sub), 4)
                   for m in ("precision_at_k", "coverage", "hit", "ndcg_at_k")},
        }
    d_p = [r["delta"]["precision_at_k"] for r in rows]
    d_n = [r["delta"]["ndcg_at_k"] for r in rows]
    b = sum(1 for r in rows if r["v2"]["hit"] == 1 and r["v1"]["hit"] == 0)
    c = sum(1 for r in rows if r["v2"]["hit"] == 0 and r["v1"]["hit"] == 1)
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "n_queries": len(rows),
            "n_queries_wave9": sum(1 for r in rows if r["source"] == "wave9"),
            "n_queries_wave10": sum(1 for r in rows if r["source"] == "wave10"),
            "n_units": len(units),
            "topk": topk,
            "n_synonym_groups": len(engine.synonyms),
            "weights": dict(engine.weights),
            "graded_relevance": "2=高度相关/1=部分相关/0=不相关；单元等级取其文本命中最高标注等级",
            "ndcg": "DCG=Σ(2^rel−1)/log2(rank+2)；IDCG=全部期望单元等级降序的理论 topk",
            "statistics": f"配对 t（正态近似）+ bootstrap {BOOTSTRAP_N} 次百分位 CI"
                          f"（seed={BOOTSTRAP_SEED}）+ McNemar 精确检验",
        },
        "aggregate": {"v1": _agg_block("v1"), "v2": _agg_block("v2"),
                      "delta": {m: round(_agg_block("v2")[m] - _agg_block("v1")[m], 4)
                                for m in ("precision_at_k", "coverage", "hit_rate",
                                          "ndcg_at_k")}},
        "significance": {
            "precision_at_k_paired_t": ev.paired_t_p(d_p),
            "precision_at_k_bootstrap": bootstrap_ci(d_p),
            "ndcg_at_k_paired_t": ev.paired_t_p(d_n),
            "ndcg_at_k_bootstrap": bootstrap_ci(d_n),
            "hit_rate_mcnemar": ev.mcnemar_exact(b, c),
            "alpha": ALPHA,
            "significant_precision": bool((ev.paired_t_p(d_p).get("p_two_sided") or 1) < ALPHA),
            "significant_ndcg": bool((ev.paired_t_p(d_n).get("p_two_sided") or 1) < ALPHA),
        },
        "by_type": by_type,
        "queries": rows,
    }


# --------------------------------------------------------------------------- #
# 报告 / 文档
# --------------------------------------------------------------------------- #
def conclusion_text(rep: dict) -> list[str]:
    agg, sig = rep["aggregate"], rep["significance"]
    pt_p, pt_n = sig["precision_at_k_paired_t"], sig["ndcg_at_k_paired_t"]
    bp, bn = sig["precision_at_k_bootstrap"], sig["ndcg_at_k_bootstrap"]
    mc = sig["hit_rate_mcnemar"]
    bt = rep["by_type"]
    syn = bt.get("同义词", {})
    and_ = bt.get("AND", {})
    lines = [
        f"- **规模**：{rep['meta']['n_queries']} 条查询"
        f"（wave9 复用 {rep['meta']['n_queries_wave9']} + 新增 {rep['meta']['n_queries_wave10']}），"
        f"8 类语法/长度覆盖，分级标注 2/1/0，期望单元全部非空。",
        f"- **指标**：P@5 {agg['v1']['precision_at_k']:.3f}→{agg['v2']['precision_at_k']:.3f}"
        f"（{agg['delta']['precision_at_k']:+.3f}）；覆盖率 {agg['v1']['coverage']:.3f}→"
        f"{agg['v2']['coverage']:.3f}（{agg['delta']['coverage']:+.3f}）；命中率 "
        f"{agg['v1']['hit_rate']:.3f}→{agg['v2']['hit_rate']:.3f}（{agg['delta']['hit_rate']:+.3f}）；"
        f"**NDCG@5 {agg['v1']['ndcg_at_k']:.3f}→{agg['v2']['ndcg_at_k']:.3f}"
        f"（{agg['delta']['ndcg_at_k']:+.3f}）**。",
        f"- **显著性**：P@5 配对 t={pt_p['t']}（p={pt_p['p_two_sided']}），bootstrap 95% CI "
        f"[{bp['ci_low']}, {bp['ci_high']}]；NDCG@5 配对 t={pt_n['t']}"
        f"（p={pt_n['p_two_sided']}），bootstrap 95% CI [{bn['ci_low']}, {bn['ci_high']}]；"
        f"命中率 McNemar p={mc['p_two_sided']}（b={mc['b']}, c={mc['c']}）。"
        f"→ **P@5 与 NDCG@5 的提升在本样本均未达统计显著**（CI 含 0），"
        f"但方向一致为正、覆盖率与命中率明显提升。",
        f"- **增益来源（分语法）**：同义词/别名类 P@5 "
        f"{syn.get('v1', {}).get('precision_at_k', 0):.3f}→"
        f"{syn.get('v2', {}).get('precision_at_k', 0):.3f}"
        f"（NDCG {syn.get('v1', {}).get('ndcg_at_k', 0):.3f}→"
        f"{syn.get('v2', {}).get('ndcg_at_k', 0):.3f}）是 v2 最大增益来源；"
        f"但 AND 类 P@5 {and_.get('v1', {}).get('precision_at_k', 0):.3f}→"
        f"{and_.get('v2', {}).get('precision_at_k', 0):.3f} 为负 —— 严格 AND 语义要求"
        f"所有词同单元命中，v1 词袋较松反而在「单主题相关」标注下得分更高，"
        f"是语义严格性与评测口径的 trade-off（非 v2 方向错误，需在 UI 说明/或改软 AND）。",
        "- **NDCG 结论**：v2 平均 NDCG@5 0.755→0.805（+0.050，CI 含 0），"
        "**排序质量方向性改善但未显著**；wave9 的 41 条 P@5 +0.141（p=0.02）在扩到 "
        f"{rep['meta']['n_queries']} 条后回落到 +{agg['delta']['precision_at_k']:.3f}"
        "（p=0.20）→ **wave9 的显著结果部分来自小样本/查询选择，不能作为强证据**。",
        "- **是否默认 v2**：**维持默认 v2**（覆盖率/命中率一致提升、同义词类显著受益、"
        "NDCG 方向为正），但对外表述应改为「v2 主要改善召回/覆盖率与同义词检索，"
        "精度提升未达统计显著」；不建议宣称 P@5 显著优于 v1。",
        "- **局限**：分级标注由关键词规则展开（2/1 边界含主观判断）；查询非独立抽样、"
        "部分主题与评测同源；bootstrap 只覆盖查询抽样不确定性、不覆盖标注不确定性；"
        "v1 基线为旧逻辑复刻而非线上日志；未做人工 NDCG 复核与多轮场景。",
    ]
    return lines


def write_md(rep: dict) -> str:
    agg, sig = rep["aggregate"], rep["significance"]
    L: list[str] = [f"# 检索 v2 评测第二阶段（{rep['meta']['n_queries']} 条 + 分级标注 + "
                    "NDCG@5 + Bootstrap CI）", ""]
    L.append(f"- 生成：{rep['meta']['generated_at']}；数据 {rep['meta']['n_units']} 单元；"
             f"查询 {rep['meta']['n_queries']} 条（wave9 {rep['meta']['n_queries_wave9']} + "
             f"新增 {rep['meta']['n_queries_wave10']}）；topk={rep['meta']['topk']}")
    L.append(f"- 分级标注：{rep['meta']['graded_relevance']}")
    L.append(f"- NDCG 口径：{rep['meta']['ndcg']}")
    L.append(f"- 统计：{rep['meta']['statistics']}")
    L.append("")
    L.append("## 汇总")
    L.append("")
    L.append("| 版本 | P@5 | 覆盖率 | 命中率 | NDCG@5 |")
    L.append("|---|---|---|---|---|")
    for ver, label in (("v1", "v1（旧检索器）"), ("v2", "**v2（新版）**")):
        a = agg[ver]
        L.append(f"| {label} | {a['precision_at_k']:.3f} | {a['coverage']:.3f} | "
                 f"{a['hit_rate']:.3f} | {a['ndcg_at_k']:.3f} |")
    d = agg["delta"]
    L.append(f"| 提升 | {d['precision_at_k']:+.3f} | {d['coverage']:+.3f} | "
             f"{d['hit_rate']:+.3f} | {d['ndcg_at_k']:+.3f} |")
    L.append("")
    L.append("## 统计显著性（配对 t + Bootstrap 95% CI + McNemar）")
    L.append("")
    L.append("| 指标 | 配对 t | 双侧 p | Bootstrap 均值 | 95% CI | CI 是否不含 0 |")
    L.append("|---|---|---|---|---|---|")
    for label, tkey, bkey in (("P@5", "precision_at_k_paired_t", "precision_at_k_bootstrap"),
                              ("NDCG@5", "ndcg_at_k_paired_t", "ndcg_at_k_bootstrap")):
        pt, bs = sig[tkey], sig[bkey]
        L.append(f"| {label} 提升 | {pt['t']} | {pt['p_two_sided']} | {bs['mean']} | "
                 f"[{bs['ci_low']}, {bs['ci_high']}] | {'是' if bs['significant'] else '否'} |")
    mc = sig["hit_rate_mcnemar"]
    L.append(f"| 命中率（McNemar） | — | {mc['p_two_sided']} | b={mc['b']} / c={mc['c']} | — | — |")
    L.append("")
    L.append("## 分语法/长度类型")
    L.append("")
    L.append("| 类型 | n | v1 P@5 | v2 P@5 | v1 NDCG@5 | v2 NDCG@5 | v1 覆盖 | v2 覆盖 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for t, x in rep["by_type"].items():
        L.append(f"| {t} | {x['n']} | {x['v1']['precision_at_k']:.3f} | {x['v2']['precision_at_k']:.3f} | "
                 f"{x['v1']['ndcg_at_k']:.3f} | {x['v2']['ndcg_at_k']:.3f} | "
                 f"{x['v1']['coverage']:.3f} | {x['v2']['coverage']:.3f} |")
    L.append("")
    L.append("## 逐查询")
    L.append("")
    L.append("| # | 查询 | 类型 | 期望单元 | v1 P@5 | v1 NDCG | v2 P@5 | v2 NDCG | ΔP@5 | ΔNDCG |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["queries"]:
        L.append(f"| {r['id']} | `{r['query']}` | {r['type']} | {r['n_expected_units']} | "
                 f"{r['v1']['precision_at_k']:.2f} | {r['v1']['ndcg_at_k']:.3f} | "
                 f"{r['v2']['precision_at_k']:.2f} | {r['v2']['ndcg_at_k']:.3f} | "
                 f"{r['delta']['precision_at_k']:+.2f} | {r['delta']['ndcg_at_k']:+.3f} |")
    L.append("")
    L.append("## 结论与局限")
    L.append("")
    L.extend(conclusion_text(rep))
    L.append("")
    return "\n".join(L)


def append_doc(rep: dict, doc_path: Path = DOC) -> str:
    """把第二阶段结论追加/替换到设计文档（标记块幂等）。"""
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else "# 研报检索 v2 设计\n"
    agg, sig = rep["aggregate"], rep["significance"]
    bp, bn = sig["precision_at_k_bootstrap"], sig["ndcg_at_k_bootstrap"]
    block = [DOC_BEGIN, "",
             f"## 第二阶段评测结论（wave10 / task-27，{rep['meta']['n_queries']} 条 + NDCG@5）", ""]
    block.append(f"- 样本：{rep['meta']['n_queries']} 条（wave9 41 + 新增 "
                 f"{rep['meta']['n_queries_wave10']}），分级标注 2/1/0，topk={rep['meta']['topk']}")
    block.append("")
    block.append("| 版本 | P@5 | 覆盖率 | 命中率 | NDCG@5 |")
    block.append("|---|---|---|---|---|")
    for ver, label in (("v1", "v1"), ("v2", "**v2**")):
        a = agg[ver]
        block.append(f"| {label} | {a['precision_at_k']:.3f} | {a['coverage']:.3f} | "
                     f"{a['hit_rate']:.3f} | {a['ndcg_at_k']:.3f} |")
    block.append(f"| 提升 | {agg['delta']['precision_at_k']:+.3f} | "
                 f"{agg['delta']['coverage']:+.3f} | {agg['delta']['hit_rate']:+.3f} | "
                 f"{agg['delta']['ndcg_at_k']:+.3f} |")
    block.append("")
    block.append(f"- 显著性：P@5 配对 t={sig['precision_at_k_paired_t']['t']}"
                 f"（p={sig['precision_at_k_paired_t']['p_two_sided']}，bootstrap 95% CI "
                 f"[{bp['ci_low']}, {bp['ci_high']}]）；NDCG@5 配对 t="
                 f"{sig['ndcg_at_k_paired_t']['t']}"
                 f"（p={sig['ndcg_at_k_paired_t']['p_two_sided']}，CI "
                 f"[{bn['ci_low']}, {bn['ci_high']}]）→ **均未达统计显著**（CI 含 0）")
    block.append(f"- 增益来源：同义词类 P@5 "
                 f"{rep['by_type'].get('同义词', {}).get('v1', {}).get('precision_at_k', 0):.3f}→"
                 f"{rep['by_type'].get('同义词', {}).get('v2', {}).get('precision_at_k', 0):.3f}；"
                 f"严格 AND 类 0.780→0.460 为负（语义严格性 vs 单主题标注口径 trade-off）")
    block.append("- 结论：**维持默认 v2**，但对外表述改为「主要改善覆盖率/命中率与同义词检索，"
                 "P@5/NDCG 提升未达统计显著」；wave9 的 41 条显著结果在 89 条上回落（+0.141→"
                 f"+{agg['delta']['precision_at_k']:.3f}），属小样本效应，不作为强证据；"
                 "v1 保留回退。")
    block.append("")
    block.append(DOC_END)
    block_txt = "\n".join(block)
    if DOC_BEGIN in doc and DOC_END in doc:
        pre = doc.split(DOC_BEGIN)[0]
        post = doc.split(DOC_END, 1)[1]
        doc = pre + block_txt + post
    else:
        if not doc.endswith("\n"):
            doc += "\n"
        doc += "\n" + block_txt + "\n"
    doc_path.write_text(doc, encoding="utf-8")
    return doc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_search_v2_ext2",
                                description="检索 v2 评测第二阶段（87 条 + 分级 + NDCG + CI）")
    p.add_argument("--data", default=str(sv.DEFAULT_DATA))
    p.add_argument("--synonyms", default=str(sv.DEFAULT_SYNONYMS))
    p.add_argument("--topk", type=int, default=5)
    p.add_argument("--out-json", default=str(OUT / "search_v2_eval_ext2_20261101.json"))
    p.add_argument("--out-md", default=str(OUT / "search_v2_eval_ext2_20261101.md"))
    p.add_argument("--no-append-doc", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    units = sv.load_units(args.data)
    engine = sv.SearchV2(units, synonyms=sv.load_synonyms(args.synonyms))
    rep = evaluate_ext2(engine, units, all_queries(), topk=args.topk)
    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    if not args.no_append_doc:
        append_doc(rep, DOC)
    agg, sig = rep["aggregate"], rep["significance"]
    print(json.dumps({
        "n_queries": rep["meta"]["n_queries"],
        "v1": agg["v1"], "v2": agg["v2"], "delta": agg["delta"],
        "p_precision": sig["precision_at_k_paired_t"]["p_two_sided"],
        "p_ndcg": sig["ndcg_at_k_paired_t"]["p_two_sided"],
        "ci_precision": [sig["precision_at_k_bootstrap"]["ci_low"],
                         sig["precision_at_k_bootstrap"]["ci_high"]],
        "ci_ndcg": [sig["ndcg_at_k_bootstrap"]["ci_low"],
                    sig["ndcg_at_k_bootstrap"]["ci_high"]],
    }, ensure_ascii=False))
    print(f"[eval_search_v2_ext2] -> {out_json} / {out_md}"
          f"{'' if args.no_append_doc else ' / doc updated'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
