# -*- coding: utf-8 -*-
"""检索 v2b：软 AND + 同义词低权重实验（修复 wave10 的 AND 类回退）。

背景
----
wave10 冻结评测（89 查询）：v2 严格 AND（要求所有查询词同单元命中）在 AND 类
P@5 0.780→0.460 回退——因为「快递 盈利」「航空 运力」等 2 词查询中，单元通常只覆盖
主主题词，严格 AND 把结果砍到 1-2 条，P@5 固定分母下大幅失分。
本文件在**不修改 search_v2.py** 的前提下实现 v2b：

1. **软 AND**（`--and-mode strict|soft|any`）：
   - `strict`：与 v2 完全一致（全部词必须命中，用于回归对照）；
   - `soft`（默认）：命中词占比 ≥ `soft_cov`（默认 0.5；即 2 词查询允许 1 词命中），
     未命中词按 0 分参与加权平均 + 覆盖率因子排序 → 全词命中的单元仍排最前，部分命中降权保留；
   - `any`：≥1 个词命中（接近 v1 词袋，但保留多字段加权/同义词/短语能力）。
   > 关于 ceil(0.7×tokens)：n≤3 时 ceil(0.7n)=n 与 strict 等价（2 词 AND 无法放松），
   > 因此软 AND 用「加权覆盖率阈值」实现；0.7 作为敏感性对照（`v2b_soft07`）一并评测。
2. **同义词扩展权重 ×0.6**：查询词通过同义词/别名（非原词）命中时，该词得分乘 0.6；
   原词命中保持 1.0 → 同义扩展只作召回补充，不压过原词直配。
3. 冻结评测：复用 `eval_search_v2_ext2.all_queries()` 的 89 条查询与分级标注
   （**不重新标注**，避免标注漂移），对比 v1 / v2 / v2b 的 P@5、覆盖率、命中率、NDCG@5
   + 配对 t + bootstrap CI（复用 wave9/10 的统计工具）。

产出：research/reports/search_v2b_eval_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/search_v2b.py eval
      C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/search_v2b.py search --q "快递 盈利" --and-mode soft
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_search_v2_ext as ev  # noqa: E402
import eval_search_v2_ext2 as e2  # noqa: E402
import search_v2 as sv  # noqa: E402

OUT = ROOT / "research" / "reports"
SCHEMA_VERSION = "search_v2b.v1"
AND_MODES = ("strict", "soft", "any")
DEFAULT_SOFT_COV = 0.5
SYNONYM_WEIGHT_DEFAULT = 0.6


def _required_ratio(mode: str, soft_cov: float) -> float:
    if mode == "strict":
        return 1.0
    if mode == "any":
        return 0.0          # ≥1 命中（matched >= max(1, …)）
    return float(soft_cov)


class SearchV2B(sv.SearchV2):
    """在 SearchV2 基础上增加「软 AND」与「同义词低权重」。"""

    def __init__(self, units: list[dict], synonyms=None, weights=None,
                 and_mode: str = "soft", soft_cov: float = DEFAULT_SOFT_COV,
                 synonym_weight: float = SYNONYM_WEIGHT_DEFAULT):
        super().__init__(units, synonyms=synonyms, weights=weights)
        if and_mode not in AND_MODES:
            raise ValueError(f"未知 and_mode: {and_mode!r}（可选 {AND_MODES}）")
        if not 0.0 <= soft_cov <= 1.0:
            raise ValueError(f"soft_cov 应在 [0,1]: {soft_cov!r}")
        self.and_mode = and_mode
        self.soft_cov = float(soft_cov)
        self.synonym_weight = float(synonym_weight)

    # -- 单词打分：同义词命中 × synonym_weight ------------------------------ #
    def _term_score(self, unit: dict, t: dict) -> tuple[float, str | None, str | None]:
        best, best_field, best_var = 0.0, None, None
        for f in sv.FIELDS:
            w = self.weights.get(f, 0.0)
            if w <= 0:
                continue
            s, var = self._term_field_score(t["term"], t["variants"], t["phrase"],
                                            unit["_text"][f], unit["_tokens"][f])
            if s > 0:
                # 同义词/别名命中（非原词）权重 ×synonym_weight
                eff = s if var is None else (
                    s if var == t["term"].strip().lower() else s * self.synonym_weight)
                if eff > best:
                    best, best_field, best_var = eff, f, var
        return best, best_field, best_var

    # -- 单元打分：软 AND / any 阈值 + 覆盖率加权 --------------------------- #
    def _unit_score(self, unit: dict, terms: list[dict]) -> dict | None:
        norm_terms = [t for t in terms if not t.get("exclude")]
        exclude_terms = [t for t in terms if t.get("exclude")]
        if not norm_terms:
            return None
        # 排除词：任一字段命中即过滤
        for t in exclude_terms:
            s, _, _ = self._term_score(unit, t)
            if s > 0:
                return None
        field_scores = {f: 0.0 for f in sv.FIELDS}
        matched: list[dict] = []
        phrase_hit = False
        for t in norm_terms:
            s, best_field, best_var = self._term_score(unit, t)
            if s <= 0:
                continue
            # 回填该词在所有字段的加权分（用于排序与 explain）
            for f in sv.FIELDS:
                w = self.weights.get(f, 0.0)
                if w <= 0:
                    continue
                raw, var = self._term_field_score(t["term"], t["variants"], t["phrase"],
                                                  unit["_text"][f], unit["_tokens"][f])
                if raw > 0:
                    eff = raw if var is None else (
                        raw if var == t["term"].strip().lower()
                        else raw * self.synonym_weight)
                    field_scores[f] += w * eff
                    if eff >= 1.0 and f in ("concept", "claim"):
                        phrase_hit = True
            matched.append({"term": t["term"], "field": best_field,
                            "score": round(s, 3), "matched_variant": best_var})
        n = len(norm_terms)
        coverage = len(matched) / float(n)
        ratio = 1.0 if self.and_mode == "strict" else self._required_ratio()
        if self.and_mode == "any":
            ok = len(matched) >= 1
        else:
            ok = coverage >= ratio - 1e-9
        if not ok:
            return None
        weight_sum = sum(self.weights.get(f, 0.0) for f in sv.FIELDS) or 1.0
        base = sum(field_scores.values()) / (weight_sum * n)   # 未命中词按 0 计
        base = min(base, 1.0)
        score = base * (0.8 + 0.2 * coverage)
        if phrase_hit:
            score = min(1.0, score + sv.PHRASE_BONUS * 0.5)
        return {"score": round(float(score), 4), "coverage": round(float(coverage), 3),
                "field_scores": {f: round(v, 4) for f, v in field_scores.items() if v > 0},
                "matched": matched, "n_terms": n}

    def _required_ratio(self) -> float:  # noqa: D401 - 供子类/测试覆写
        return _required_ratio(self.and_mode, self.soft_cov)


# --------------------------------------------------------------------------- #
# 冻结评测：v1 / v2 / v2b 对比（89 查询，分级标注不重标）
# --------------------------------------------------------------------------- #
def _engine_metrics(rep: dict) -> dict:
    """从 eval_search_v2_ext2 报告中提取引擎列（报告把实验引擎记作 v2 列）。"""
    return {q["id"]: {
        "id": q["id"], "query": q["query"], "type": q["type"],
        "precision_at_k": q["v2"]["precision_at_k"],
        "coverage": q["v2"]["coverage"],
        "hit": q["v2"]["hit"],
        "ndcg_at_k": q["v2"]["ndcg_at_k"],
        "grades": q["v2"]["grades"],
        "v1_precision_at_k": q["v1"]["precision_at_k"],
        "v1_coverage": q["v1"]["coverage"],
        "v1_hit": q["v1"]["hit"],
        "v1_ndcg_at_k": q["v1"]["ndcg_at_k"],
        "v1_grades": q["v1"]["grades"],
    } for q in rep["queries"]}


def _agg(rows: list[dict], engine: str = "v2") -> dict:
    def _m(key: str) -> float:
        if engine == "v1":
            key = "v1_" + key
        return sum(float(r.get(key, 0.0) or 0.0) for r in rows) / len(rows) if rows else 0.0
    return {"n": len(rows),
            "precision_at_k": round(_m("precision_at_k"), 4),
            "coverage": round(_m("coverage"), 4),
            "hit_rate": round(_m("hit"), 4),
            "ndcg_at_k": round(_m("ndcg_at_k"), 4)}


def compare_engines(units: list[dict], synonyms, queries: list[dict]) -> dict:
    """v1 / v2（strict）/ v2b（soft/soft0.7/any/strict）对比。"""
    syn_names = {
        "v1": "v1 旧检索器（bigram 词袋）",
        "v2": "v2 严格 AND（wave7 实现）",
        "v2b_soft": "v2b 软 AND（soft_cov=0.5，推荐）",
        "v2b_soft07": "v2b 软 AND（soft_cov=0.7，规格字面口径）",
        "v2b_any": "v2b any（≥1 词命中）",
        "v2b_strict": "v2b 严格 AND（应复现 v2）",
    }
    engines = {
        # v2 默认已在 wave12 AH 合入 soft+0.6；本对照表沿用「旧 v2 严格基线」
        # （strict + synonym_weight=1.0），保证与 wave12 历史结果可比。
        "v2": sv.SearchV2(units, synonyms=synonyms, and_mode="strict",
                          synonym_weight=1.0),
        "v2b_soft": SearchV2B(units, synonyms=synonyms, and_mode="soft",
                              soft_cov=DEFAULT_SOFT_COV),
        "v2b_soft07": SearchV2B(units, synonyms=synonyms, and_mode="soft", soft_cov=0.7),
        "v2b_any": SearchV2B(units, synonyms=synonyms, and_mode="any"),
        "v2b_strict": SearchV2B(units, synonyms=synonyms, and_mode="strict"),
        # 回归对照：strict + 同义词权重 1.0 应与 v2 完全一致（隔离同义词低权重的改动）
        "v2b_strict_parity": SearchV2B(units, synonyms=synonyms, and_mode="strict",
                                       synonym_weight=1.0),
    }
    reports = {name: e2.evaluate_ext2(eng, units, queries) for name, eng in engines.items()}
    columns = {name: _engine_metrics(rep) for name, rep in reports.items()}
    base = columns["v2"]
    ids = [q["id"] for q in queries]
    type_of = {q["id"]: q["type"] for q in queries}

    def _subset_rows(subset_ids: list[str]) -> dict:
        out = {}
        for name, col in columns.items():
            rows = [col[i] for i in subset_ids if i in col]
            out[name] = _agg(rows)
        out["v1"] = _agg([base[i] for i in subset_ids if i in base], "v1")
        return out

    and_ids = [i for i in ids if type_of.get(i) == "AND"]
    tables = {
        "all": {name: _agg(list(col.values())) for name, col in columns.items()},
        "AND": _subset_rows(and_ids),
    }
    tables["all"]["v1"] = _agg(list(base.values()), "v1")
    # 配对差（v2b各变体 − v2）与显著性
    def _paired(metric: str, variant: str, subset_ids: list[str]) -> list[float]:
        vals = []
        for i in subset_ids:
            a = columns[variant].get(i)
            b = base.get(i)
            if not a or not b:
                continue
            if metric == "precision_at_k":
                vals.append(float(a["precision_at_k"]) - float(b["precision_at_k"]))
            elif metric == "ndcg_at_k":
                vals.append(float(a["ndcg_at_k"]) - float(b["ndcg_at_k"]))
        return vals

    variants = ["v2b_soft", "v2b_soft07", "v2b_any"]
    significance = {}
    for v in variants:
        significance[v] = {}
        for metric in ("precision_at_k", "ndcg_at_k"):
            d_all = _paired(metric, v, ids)
            d_and = _paired(metric, v, and_ids)
            significance[v][metric] = {
                "all_89": {"paired_t": ev.paired_t_p(d_all), "bootstrap": e2.bootstrap_ci(d_all)},
                "AND_10": {"paired_t": ev.paired_t_p(d_and), "bootstrap": e2.bootstrap_ci(d_and)},
            }
    # 与 v1 对比（各变体）
    vs_v1 = {v: {"precision_at_k_delta": round(
        _agg(list(columns[v].values()))["precision_at_k"]
        - _agg(list(columns[v].values()), "v1")["precision_at_k"], 4)}
        for v in ["v2"] + variants}
    # 分语法（推荐变体 vs v2）
    by_type = {}
    for t in sorted(set(type_of.values())):
        tids = [i for i in ids if type_of[i] == t]
        by_type[t] = {"n": len(tids),
                      "v2": _agg([columns["v2"][i] for i in tids]),
                      "v2b_soft": _agg([columns["v2b_soft"][i] for i in tids])}
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "n_queries": len(queries), "n_units": len(units), "topk": 5,
            "eval_set": "冻结的 wave10 89 查询（eval_search_v2_ext2.all_queries()，分级标注未改）",
            "engines": syn_names,
            "defaults": {"soft_cov": DEFAULT_SOFT_COV,
                         "synonym_weight": SYNONYM_WEIGHT_DEFAULT},
            "soft_and_note": "ceil(0.7×n)=n（n≤3 时），规格 0.7 字面阈值对 2 词 AND 等价 strict；"
                             "软 AND 用覆盖率阈值实现，默认 0.5 以修复 2 词 AND，0.7 作敏感性对照",
        },
        "tables": tables,
        "by_type": by_type,
        "vs_v1": vs_v1,
        "significance": significance,
        "parity_check": {
            "v2b_strict_parity_vs_v2_max_abs_diff": {
                "precision_at_k": round(max(abs(columns["v2b_strict_parity"][i]["precision_at_k"]
                                                  - base[i]["precision_at_k"]) for i in ids), 6),
                "ndcg_at_k": round(max(abs(columns["v2b_strict_parity"][i]["ndcg_at_k"]
                                             - base[i]["ndcg_at_k"]) for i in ids), 6),
            },
            "identical": all(abs(columns["v2b_strict_parity"][i]["precision_at_k"]
                                 - base[i]["precision_at_k"]) < 1e-9 for i in ids),
            "note": "strict + synonym_weight=1.0 与 v2 逐条一致 → 软 AND/同义词权重是仅有的改动；"
                    "默认 v2b_strict（synonym_weight=0.6）与 v2 的差异来自同义词低权重（预期行为）",
        },
        "per_query": {
            "AND": [{**{k: columns["v2"][i][k] for k in
                        ("id", "query", "precision_at_k", "ndcg_at_k")},
                     "v2b_soft_precision_at_k": columns["v2b_soft"][i]["precision_at_k"],
                     "v2b_soft_ndcg_at_k": columns["v2b_soft"][i]["ndcg_at_k"]}
                    for i in and_ids],
        },
        "conclusion": None,
    }


def build_conclusion(rep: dict) -> dict:
    t = rep["tables"]
    and_v2 = t["AND"]["v2"]
    and_b = t["AND"]["v2b_soft"]
    all_v2 = t["all"]["v2"]
    all_b = t["all"]["v2b_soft"]
    sig = rep["significance"]["v2b_soft"]
    d_and_p = round(and_b["precision_at_k"] - and_v2["precision_at_k"], 4)
    d_and_n = round(and_b["ndcg_at_k"] - and_v2["ndcg_at_k"], 4)
    d_all_p = round(all_b["precision_at_k"] - all_v2["precision_at_k"], 4)
    d_all_n = round(all_b["ndcg_at_k"] - all_v2["ndcg_at_k"], 4)
    ci_and = sig["precision_at_k"]["AND_10"]["bootstrap"]
    ci_all = sig["precision_at_k"]["all_89"]["bootstrap"]
    merge = bool(d_and_p > 0.1 and d_all_p >= 0.0 and d_all_n >= -0.005)
    syn = rep["by_type"].get("同义词", {})
    syn_d = (round(syn.get("v2b_soft", {}).get("precision_at_k", 0)
                   - syn.get("v2", {}).get("precision_at_k", 0), 4)
             if syn else 0.0)
    return {
        "and_p5_improvement": d_and_p,
        "and_ndcg_improvement": d_and_n,
        "overall_p5_improvement": d_all_p,
        "overall_ndcg_improvement": d_all_n,
        "and_ci": ci_and,
        "overall_ci": ci_all,
        "synonym_class_p5_change": syn_d,
        "merge_recommended": merge,
        "recommendation": (
            f"AND 类 P@5 {and_v2['precision_at_k']:.3f}→{and_b['precision_at_k']:.3f}"
            f"（{d_and_p:+.3f}）、NDCG {and_v2['ndcg_at_k']:.3f}→{and_b['ndcg_at_k']:.3f}"
            f"（{d_and_n:+.3f}）；整体 P@5 {all_v2['precision_at_k']:.3f}→"
            f"{all_b['precision_at_k']:.3f}（{d_all_p:+.3f}）、NDCG "
            f"{all_v2['ndcg_at_k']:.3f}→{all_b['ndcg_at_k']:.3f}（{d_all_n:+.3f}）；"
            f"同义词类 P@5 变化 {syn_d:+.3f}（synonym_weight=0.6 的代价，见分语法表）。"
            + (" → **建议合入 v2**：软 AND 作为 v2 的 `and_mode` 默认 soft（soft_cov=0.5）+ "
               "同义词权重 0.6，AND 类回退修复且整体不劣化；v2 文件保持不变，v2b 独立可灰度。"
               if merge else
               " → **暂不合入**：AND 改善或整体指标未达预期，保留 v2b 为实验分支。")),
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def write_md(rep: dict) -> str:
    m, t = rep["meta"], rep["tables"]
    c = rep["conclusion"]
    L = ["# 检索 v2b：软 AND + 同义词低权重实验（修复 AND 类回退）", ""]
    L.append(f"- 生成：{m['generated_at']}；评测集：{m['eval_set']}；topk={m['topk']}，"
             f"单元 {m['n_units']}")
    L.append(f"- 引擎：v1 旧检索器 / v2 严格 AND / v2b soft（默认 soft_cov="
             f"{m['defaults']['soft_cov']}、同义词权重 {m['defaults']['synonym_weight']}）/ "
             f"v2b soft0.7（规格字面）/ v2b any / v2b strict（对照）")
    L.append(f"- 说明：{m['soft_and_note']}")
    L.append("")
    L.append("## 整体（89 查询）")
    L.append("")
    L.append("| 引擎 | P@5 | 覆盖率 | 命中率 | NDCG@5 |")
    L.append("|---|---|---|---|---|")
    for key, name in (("v1", "v1"), ("v2", "v2 严格 AND"),
                      ("v2b_soft", "**v2b soft**"), ("v2b_soft07", "v2b soft0.7"),
                      ("v2b_any", "v2b any"), ("v2b_strict", "v2b strict")):
        row = t["all"][key]
        L.append(f"| {name} | {row['precision_at_k']:.3f} | {row['coverage']:.3f} | "
                 f"{row['hit_rate']:.3f} | {row['ndcg_at_k']:.3f} |")
    L.append("")
    L.append("## AND 类（n=10，回退修复重点）")
    L.append("")
    L.append("| 引擎 | P@5 | 覆盖率 | 命中率 | NDCG@5 |")
    L.append("|---|---|---|---|---|")
    for key in ("v1", "v2", "v2b_soft", "v2b_soft07", "v2b_any", "v2b_strict"):
        row = t["AND"][key]
        if "v1" not in row:
            continue
        L.append(f"| {key} | {row['precision_at_k']:.3f} | {row['coverage']:.3f} | "
                 f"{row['hit_rate']:.3f} | {row['ndcg_at_k']:.3f} |")
    L.append("")
    L.append("逐 AND 查询：")
    L.append("")
    L.append("| id | 查询 | v2 P@5 | v2b P@5 | v2 NDCG | v2b NDCG |")
    L.append("|---|---|---|---|---|---|")
    for r in rep["per_query"]["AND"]:
        L.append(f"| {r['id']} | `{r['query']}` | {r['precision_at_k']:.2f} | "
                 f"{r['v2b_soft_precision_at_k']:.2f} | {r['ndcg_at_k']:.3f} | "
                 f"{r['v2b_soft_ndcg_at_k']:.3f} |")
    L.append("")
    L.append("## 显著性（v2b soft − v2，配对 t + bootstrap CI）")
    L.append("")
    L.append("| 指标 | 子集 | 配对 t | p | bootstrap 95% CI | CI 不含 0 |")
    L.append("|---|---|---|---|---|---|")
    for metric in ("precision_at_k", "ndcg_at_k"):
        for subset in ("all_89", "AND_10"):
            blk = rep["significance"]["v2b_soft"][metric][subset]
            pt, bs = blk["paired_t"], blk["bootstrap"]
            L.append(f"| {metric} | {subset} | {pt['t']} | {pt['p_two_sided']} | "
                     f"[{bs['ci_low']}, {bs['ci_high']}] | "
                     f"{'是' if bs['significant'] else '否'} |")
    L.append("")
    L.append("## 分语法类型（v2 vs v2b soft）")
    L.append("")
    L.append("| 类型 | n | v2 P@5 | v2b P@5 | v2 NDCG | v2b NDCG |")
    L.append("|---|---|---|---|---|---|")
    for typ, x in rep["by_type"].items():
        L.append(f"| {typ} | {x['n']} | {x['v2']['precision_at_k']:.3f} | "
                 f"{x['v2b_soft']['precision_at_k']:.3f} | {x['v2']['ndcg_at_k']:.3f} | "
                 f"{x['v2b_soft']['ndcg_at_k']:.3f} |")
    L.append("")
    L.append("## v2b strict 与 v2 一致性（回归对照）")
    L.append("")
    pc = rep["parity_check"]
    L.append(f"- v2b strict + synonym_weight=1.0 vs v2 逐查询最大绝对差：P@5 "
             f"{pc['v2b_strict_parity_vs_v2_max_abs_diff']['precision_at_k']}、NDCG "
             f"{pc['v2b_strict_parity_vs_v2_max_abs_diff']['ndcg_at_k']} → "
             f"{'完全一致 ✅' if pc['identical'] else '存在差异 ⚠️'}（隔离验证软 AND/同义词权重是实现中仅有的改动）")
    L.append("")
    L.append("## 结论与合入建议")
    L.append("")
    L.append(f"- AND 类 P@5 提升 **{c['and_p5_improvement']:+.3f}**、NDCG "
             f"{c['and_ndcg_improvement']:+.3f}；整体 P@5 {c['overall_p5_improvement']:+.3f}、"
             f"NDCG {c['overall_ndcg_improvement']:+.3f}")
    L.append(f"- 合入建议：**{'建议合入 v2（默认 soft）' if c['merge_recommended'] else '暂不合入'}**")
    L.append(f"- {c['recommendation']}")
    L.append("")
    L.append("## 局限")
    L.append("")
    for x in ("评测集为冻结的 89 条（同一批查询/标注），结论不覆盖新查询分布；",
              "AND 类仅 10 条，AND 子集提升的置信区间较宽，p 值仅作量级参考；",
              "软 AND 的 soft_cov/同义词权重为超参，本报告只做了 0.7（规格）与 0.5 两个阈值；",
              "未做线上 A/B；v2b 为独立实验文件，未合入 v2 主实现。"):
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_search(args) -> int:
    units = sv.load_units(args.data)
    synonyms = sv.load_synonyms(args.synonyms)
    eng = SearchV2B(units, synonyms=synonyms, and_mode=args.and_mode,
                    soft_cov=args.soft_cov, synonym_weight=args.synonym_weight)
    payload = eng.search(args.q, topk=args.topk, explain=args.explain)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


def cmd_eval(args) -> int:
    units = sv.load_units(args.data)
    synonyms = sv.load_synonyms(args.synonyms)
    queries = e2.all_queries()
    rep = compare_engines(units, synonyms, queries)
    rep["conclusion"] = build_conclusion(rep)
    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    print(json.dumps({
        "n_queries": rep["meta"]["n_queries"],
        "overall": {k: rep["tables"]["all"][k] for k in
                    ("v1", "v2", "v2b_soft", "v2b_soft07", "v2b_any")},
        "AND": {k: rep["tables"]["AND"][k] for k in
                ("v1", "v2", "v2b_soft", "v2b_soft07", "v2b_any")},
        "merge_recommended": rep["conclusion"]["merge_recommended"],
    }, ensure_ascii=False))
    print(f"[search_v2b] eval -> {out_json} / {out_md}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="search_v2b",
                                description="检索 v2b：软 AND + 同义词低权重实验")
    p.add_argument("--data", default=str(sv.DEFAULT_DATA))
    p.add_argument("--synonyms", default=str(sv.DEFAULT_SYNONYMS))
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("search")
    ps.add_argument("--q", required=True)
    ps.add_argument("--topk", type=int, default=10)
    ps.add_argument("--and-mode", choices=AND_MODES, default="soft")
    ps.add_argument("--soft-cov", type=float, default=DEFAULT_SOFT_COV)
    ps.add_argument("--synonym-weight", type=float, default=SYNONYM_WEIGHT_DEFAULT)
    ps.add_argument("--explain", action="store_true")
    ps.add_argument("--no-group", action="store_true")
    ps.set_defaults(func=cmd_search)
    pe = sub.add_parser("eval")
    pe.add_argument("--out-json", default=str(OUT / "search_v2b_eval_20261101.json"))
    pe.add_argument("--out-md", default=str(OUT / "search_v2b_eval_20261101.md"))
    pe.set_defaults(func=cmd_eval)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
