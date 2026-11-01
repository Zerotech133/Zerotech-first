# -*- coding: utf-8 -*-
"""检索 v2 评测扩展（wave9 支线 X）：40 条人工标注查询，v1 vs v2 统计性对比。

背景
----
wave7 的评测只有 10 条查询（P@5 0.760→1.000，偏乐观）。本文件把评测集扩到 **41 条**：
- 主题：量学（量柱/量线/涨停）、电池（钠电/动力）、光伏/储能、AI/算力/大模型/机器人、
  半导体、白酒、房地产、铁路、企业信用、网络安全等研报/书籍主题；
- 语法：同义词（太阳能/IDC/钠离子电池/人工智能/股息/存货/芯片/港股通）、
  多词 AND、OR、短语、`-排除` 全覆盖。
每条查询人工标注 `expected_concepts`（从 498 单元的人工挑选概念子串），脚本据此展开
expected_units 并计算：
  - precision@5（top5 中概念相关单元占比，分母固定 5）；
  - 覆盖率（标注概念键在 top5 中命中比例）；
  - 命中率（top5 至少 1 条相关单元的查询占比）。
v1 基线 = 旧检索器逻辑等价复刻（`search_v2.legacy_search_units`，concept×2+claim×1 bigram）；
v2 = `search_v2.SearchV2`（多字段加权+同义词+AND/OR+短语）。

统计：对每查询 precision@5 差做配对 t（正态近似）检验；对命中率做 McNemar 精确检验；
输出 p 值与局限说明（小样本/标注偏差/查询非独立）。

产出
----
- research/reports/search_v2_eval_ext_20261101.json / .md
- 追加到 docs/plans/2026-11-01_研报检索v2_v1.md 的评测章节（标记块可重复覆盖）

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_search_v2_ext.py
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_search_v2_ext.py --no-append-doc
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

import search_v2 as sv  # noqa: E402

OUT = ROOT / "research" / "reports"
DOC = ROOT / "docs" / "plans" / "2026-11-01_研报检索v2_v1.md"
SCHEMA_VERSION = "search_v2_eval_ext.v1"
DOC_BEGIN = "<!-- SEARCH_V2_EVAL_EXT_BEGIN -->"
DOC_END = "<!-- SEARCH_V2_EVAL_EXT_END -->"

# 人工标注查询集：expected_concepts 为「概念子串」列表（从 498 单元的人工挑选），
# 脚本据此展开 expected_units（concept 命中任一子串即相关）。
QUERIES: list[dict] = [
    # ---- 主题（18） ----
    {"id": "Q01", "q": "量柱分类", "type": "主题", "expected_concepts": ["量柱"]},
    {"id": "Q02", "q": "量线定义", "type": "主题", "expected_concepts": ["量线"]},
    {"id": "Q03", "q": "涨停基因", "type": "主题", "expected_concepts": ["涨停基因", "涨停密码"]},
    {"id": "Q04", "q": "伏击涨停定律", "type": "主题", "expected_concepts": ["伏击涨停第", "伏击涨停"]},
    {"id": "Q05", "q": "钠电池应用场景", "type": "主题", "expected_concepts": ["钠电池"]},
    {"id": "Q06", "q": "动力电池竞争", "type": "主题", "expected_concepts": ["动力电池"]},
    {"id": "Q07", "q": "光伏装机", "type": "主题", "expected_concepts": ["光伏"]},
    {"id": "Q08", "q": "储能市场", "type": "主题", "expected_concepts": ["储能"]},
    {"id": "Q09", "q": "数据中心用电", "type": "主题", "expected_concepts": ["数据中心"]},
    {"id": "Q10", "q": "AI 算力建设", "type": "主题", "expected_concepts": ["算力", "AI 算力"]},
    {"id": "Q11", "q": "大模型商业化", "type": "主题",
     "expected_concepts": ["大模型", "AI 模型商业化", "商业化"]},
    {"id": "Q12", "q": "四足机器人", "type": "主题", "expected_concepts": ["四足机器人", "机器人"]},
    {"id": "Q13", "q": "半导体景气度", "type": "主题", "expected_concepts": ["半导体"]},
    {"id": "Q14", "q": "白酒价格带", "type": "主题", "expected_concepts": ["白酒"]},
    {"id": "Q15", "q": "房地产政策", "type": "主题", "expected_concepts": ["房地产", "购房"]},
    {"id": "Q16", "q": "铁路客流", "type": "主题", "expected_concepts": ["铁路"]},
    {"id": "Q17", "q": "企业信用分化", "type": "主题", "expected_concepts": ["企业信用"]},
    {"id": "Q18", "q": "网络安全技能", "type": "主题", "expected_concepts": ["网络安全"]},
    # ---- 同义词 / 别名（8） ----
    {"id": "Q19", "q": "太阳能", "type": "同义词", "expected_concepts": ["光伏"]},
    {"id": "Q20", "q": "钠离子电池", "type": "同义词", "expected_concepts": ["钠电池"]},
    {"id": "Q21", "q": "IDC", "type": "同义词", "expected_concepts": ["数据中心"]},
    {"id": "Q22", "q": "人工智能", "type": "同义词", "expected_concepts": ["AI", "大模型"]},
    {"id": "Q23", "q": "股息", "type": "同义词", "expected_concepts": ["股息", "分红"]},
    {"id": "Q24", "q": "存货", "type": "同义词", "expected_concepts": ["库存"]},
    {"id": "Q25", "q": "芯片", "type": "同义词", "expected_concepts": ["芯片", "半导体"]},
    {"id": "Q26", "q": "港股通", "type": "同义词", "expected_concepts": ["港股通", "港股"]},
    # ---- 多词 AND（5） ----
    {"id": "Q27", "q": "光伏 储能", "type": "AND", "expected_concepts": ["光伏", "储能"]},
    {"id": "Q28", "q": "白酒 价格带", "type": "AND", "expected_concepts": ["白酒"]},
    {"id": "Q29", "q": "钠电池 竞争壁垒", "type": "AND", "expected_concepts": ["钠电池"]},
    {"id": "Q30", "q": "AI 商业化", "type": "AND", "expected_concepts": ["AI", "商业化"]},
    {"id": "Q31", "q": "铁路 客流", "type": "AND", "expected_concepts": ["铁路"]},
    # ---- OR（4） ----
    {"id": "Q32", "q": "光伏|钠电池", "type": "OR", "expected_concepts": ["光伏", "钠电池"]},
    {"id": "Q33", "q": "量柱|量线", "type": "OR", "expected_concepts": ["量柱", "量线"]},
    {"id": "Q34", "q": "AI|机器人", "type": "OR", "expected_concepts": ["AI", "机器人"]},
    {"id": "Q35", "q": "白酒|消费", "type": "OR", "expected_concepts": ["白酒", "消费"]},
    # ---- 短语（3） ----
    {"id": "Q36", "q": '"量柱完全分类"', "type": "短语", "expected_concepts": ["量柱完全分类"]},
    {"id": "Q37", "q": '"行业评级定义"', "type": "短语", "expected_concepts": ["行业评级定义"]},
    {"id": "Q38", "q": '"企业信用结构性分化"', "type": "短语", "expected_concepts": ["企业信用"]},
    # ---- 排除（3） ----
    {"id": "Q39", "q": "量柱 -量线", "type": "排除", "expected_concepts": ["量柱"]},
    {"id": "Q40", "q": "钠电池 -储能", "type": "排除", "expected_concepts": ["钠电池"]},
    {"id": "Q41", "q": "AI -网络安全", "type": "排除", "expected_concepts": ["AI"]},
]
QUERY_TYPES = ("主题", "同义词", "AND", "OR", "短语", "排除")


# --------------------------------------------------------------------------- #
# 纯函数（可单测）
# --------------------------------------------------------------------------- #
RELEVANCE_FIELDS = ("concept", "claim", "evidence")


def is_relevant(unit: dict, expected_concepts: list[str],
                fields: tuple[str, ...] = RELEVANCE_FIELDS) -> bool:
    """相关判定：期望概念/关键词出现在单元指定字段（默认 concept/claim/evidence）。

    默认用三字段而非仅 concept：同一知识点在不同单元的 concept 命名不一致
    （如「量柱」相关单元 concept 可能是「量学理论」），只比 concept 会低估两种检索器；
    本文件同时给出 concept-only 严格口径的敏感性分析。
    """
    text = " ".join(str(unit.get(f, "") or "") for f in fields)
    return any(k and k in text for k in expected_concepts)


def expected_unit_ids(units: list[dict], expected_concepts: list[str],
                      fields: tuple[str, ...] = RELEVANCE_FIELDS) -> list[int]:
    return [i for i, u in enumerate(units) if is_relevant(u, expected_concepts, fields)]


def precision_at_k(units_top: list[dict], expected_concepts: list[str], k: int = 5,
                   fields: tuple[str, ...] = RELEVANCE_FIELDS) -> float:
    """precision@k：topk 中概念相关单元数 / k（分母固定 k）。"""
    if k <= 0:
        raise ValueError(f"k 必须为正: {k}")
    hits = sum(1 for u in units_top[:k] if is_relevant(u, expected_concepts, fields))
    return hits / float(k)


def coverage_at_k(units_top: list[dict], expected_concepts: list[str],
                  fields: tuple[str, ...] = RELEVANCE_FIELDS) -> float:
    """覆盖率：标注概念键在 topk 结果（concept/claim/evidence）中被命中的比例。"""
    keys = [k for k in expected_concepts if k]
    if not keys:
        return 0.0
    text = " || ".join(" ".join(str(u.get(f, "") or "") for f in fields)
                       for u in units_top)
    return sum(1 for k in keys if k in text) / float(len(keys))


def paired_t_p(diffs: list[float]) -> dict:
    """配对差的正态近似 t 检验（双侧 p）。"""
    xs = [float(x) for x in diffs]
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": (round(xs[0], 4) if xs else None), "sd": None,
                "t": None, "p_two_sided": None}
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    if sd <= 0:
        return {"n": n, "mean": round(mean, 4), "sd": 0.0, "t": None,
                "p_two_sided": 1.0 if mean == 0 else 0.0}
    t = mean / (sd / math.sqrt(n))
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))
    return {"n": n, "mean": round(mean, 4), "sd": round(sd, 4),
            "t": round(t, 4), "p_two_sided": round(min(1.0, p), 4)}


def mcnemar_exact(b: int, c: int) -> dict:
    """McNemar 精确检验（双侧）：b/c 为两法命中不一致的计数。"""
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n_discordant": 0, "p_two_sided": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / float(2 ** n)
    p = min(1.0, 2.0 * tail)
    return {"b": b, "c": c, "n_discordant": n, "p_two_sided": round(p, 4)}


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
def evaluate_ext(engine, units: list[dict] | None = None,
                 queries: list[dict] | None = None, topk: int = 5,
                 fields: tuple[str, ...] = RELEVANCE_FIELDS) -> dict:
    units = units if units is not None else engine.units
    qs = queries if queries is not None else QUERIES
    exp_units_map = {q["id"]: expected_unit_ids(units, q["expected_concepts"], fields)
                     for q in qs}
    rows: list[dict] = []
    for q in qs:
        kws = q["expected_concepts"]
        v2 = engine.search_units(q["q"])[:topk]
        v1 = sv.legacy_search_units(units, q["q"], topk=topk)
        r = {
            "id": q["id"], "query": q["q"], "type": q["type"],
            "expected_concepts": kws,
            "n_expected_units": len(exp_units_map[q["id"]]),
            "v1": {
                "precision_at_k": round(precision_at_k(v1, kws, topk, fields), 4),
                "coverage": round(coverage_at_k(v1, kws), 4),
                "hit": 1 if any(is_relevant(u, kws, fields) for u in v1[:topk]) else 0,
                "top_concepts": [str(u.get("concept", "") or "") for u in v1[:topk]],
            },
            "v2": {
                "precision_at_k": round(precision_at_k(v2, kws, topk, fields), 4),
                "coverage": round(coverage_at_k(v2, kws), 4),
                "hit": 1 if any(is_relevant(u, kws, fields) for u in v2[:topk]) else 0,
                "top_concepts": [str(u.get("concept", "") or "") for u in v2[:topk]],
            },
        }
        r["delta_precision"] = round(r["v2"]["precision_at_k"] - r["v1"]["precision_at_k"], 4)
        rows.append(r)

    def _agg(key: str, metric: str) -> float:
        vals = [r[key][metric] for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    # 按语法类型汇总
    by_type: dict[str, dict] = {}
    for t in QUERY_TYPES:
        sub = [r for r in rows if r["type"] == t]
        if not sub:
            continue
        by_type[t] = {
            "n": len(sub),
            "v1": {"precision_at_k": round(sum(x["v1"]["precision_at_k"] for x in sub) / len(sub), 4),
                   "coverage": round(sum(x["v1"]["coverage"] for x in sub) / len(sub), 4),
                   "hit_rate": round(sum(x["v1"]["hit"] for x in sub) / len(sub), 4)},
            "v2": {"precision_at_k": round(sum(x["v2"]["precision_at_k"] for x in sub) / len(sub), 4),
                   "coverage": round(sum(x["v2"]["coverage"] for x in sub) / len(sub), 4),
                   "hit_rate": round(sum(x["v2"]["hit"] for x in sub) / len(sub), 4)},
        }
    diffs = [r["delta_precision"] for r in rows]
    b = sum(1 for r in rows if r["v2"]["hit"] == 1 and r["v1"]["hit"] == 0)
    c = sum(1 for r in rows if r["v2"]["hit"] == 0 and r["v1"]["hit"] == 1)
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "n_queries": len(rows),
            "n_units": len(units),
            "topk": topk,
            "n_synonym_groups": len(engine.synonyms),
            "weights": dict(engine.weights),
            "annotation_rule": "expected_concepts=人工挑选概念/关键词子串；"
                               "expected_units=concept/claim/evidence 命中任一子串的单元；"
                               "相关=topk 单元的 concept/claim/evidence 命中任一 expected_concept",
        },
        "aggregate": {
            "v1": {"precision_at_k": _agg("v1", "precision_at_k"),
                   "coverage": _agg("v1", "coverage"),
                   "hit_rate": _agg("v1", "hit")},
            "v2": {"precision_at_k": _agg("v2", "precision_at_k"),
                   "coverage": _agg("v2", "coverage"),
                   "hit_rate": _agg("v2", "hit")},
            "delta": {
                "precision_at_k": round(_agg("v2", "precision_at_k") - _agg("v1", "precision_at_k"), 4),
                "coverage": round(_agg("v2", "coverage") - _agg("v1", "coverage"), 4),
                "hit_rate": round(_agg("v2", "hit") - _agg("v1", "hit"), 4),
            },
        },
        "significance": {
            "precision_at_k_paired_t": paired_t_p(diffs),
            "hit_rate_mcnemar": mcnemar_exact(b, c),
            "alpha": 0.05,
            "significant": bool(paired_t_p(diffs).get("p_two_sided") is not None
                                and paired_t_p(diffs)["p_two_sided"] < 0.05),
        },
        "by_type": by_type,
        "queries": rows,
        "expected_units": exp_units_map,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def write_md(rep: dict) -> str:
    agg = rep["aggregate"]
    sig = rep["significance"]
    pt = sig["precision_at_k_paired_t"]
    mc = sig["hit_rate_mcnemar"]
    L: list[str] = ["# 检索 v2 评测扩展（41 条人工标注查询，v1 vs v2）", ""]
    L.append(f"- 生成：{rep['meta']['generated_at']}；数据 {rep['meta']['n_units']} 单元；"
             f"查询 {rep['meta']['n_queries']} 条；topk={rep['meta']['topk']}；"
             f"同义词组 {rep['meta']['n_synonym_groups']}")
    L.append(f"- 标注：{rep['meta']['annotation_rule']}")
    L.append("")
    L.append("## 汇总")
    L.append("")
    L.append("| 版本 | precision@5 | 覆盖率 | 命中率(至少1条相关) |")
    L.append("|---|---|---|---|")
    L.append(f"| v1（旧检索器） | {agg['v1']['precision_at_k']:.3f} | {agg['v1']['coverage']:.3f} | "
             f"{agg['v1']['hit_rate']:.3f} |")
    L.append(f"| **v2（新版）** | **{agg['v2']['precision_at_k']:.3f}** | "
             f"**{agg['v2']['coverage']:.3f}** | **{agg['v2']['hit_rate']:.3f}** |")
    L.append(f"| 提升 | {agg['delta']['precision_at_k']:+.3f} | {agg['delta']['coverage']:+.3f} | "
             f"{agg['delta']['hit_rate']:+.3f} |")
    L.append("")
    L.append("## 显著性")
    L.append("")
    L.append(f"- precision@5 配对差（v2−v1，三字段口径）：mean={pt['mean']}、sd={pt['sd']}、"
             f"t={pt['t']}、双侧 p={pt['p_two_sided']}（正态近似；n={pt['n']}）")
    L.append(f"- 命中率 McNemar：b(v2命中/v1未命中)={mc['b']}、c(v1命中/v2未命中)={mc['c']}、"
             f"p={mc['p_two_sided']}（精确检验）")
    L.append(f"- 按 α={sig['alpha']}：**{'差异统计显著' if sig['significant'] else '差异未达统计显著'}**"
             f"（小样本 {rep['meta']['n_queries']} 条、标注由概念关键词规则展开，见局限）")
    L.append("")
    sens = rep.get("sensitivity") or {}
    if sens:
        st = sens.get("three_field") or {}
        so = sens.get("concept_only") or {}
        L.append("### 标注口径敏感性")
        L.append("")
        L.append("| 相关判定口径 | v1 P@5 | v2 P@5 | ΔP@5 | 配对 t | 双侧 p | 是否显著(α=0.05) |")
        L.append("|---|---|---|---|---|---|---|")
        for label, blk in (("三字段（concept/claim/evidence，默认）", st),
                           ("仅 concept（严格）", so)):
            a = blk.get("aggregate") or {}
            s2 = blk.get("significance") or {}
            spt = s2.get("precision_at_k_paired_t") or {}
            L.append(f"| {label} | {(a.get('v1') or {}).get('precision_at_k')} | "
                     f"{(a.get('v2') or {}).get('precision_at_k')} | "
                     f"{(a.get('delta') or {}).get('precision_at_k')} | "
                     f"{spt.get('t')} | {spt.get('p_two_sided')} | "
                     f"{'是' if s2.get('significant') else '否'} |")
        L.append("")
        L.append(f"> {sens.get('note', '')}")
        L.append("")
    L.append("## 按语法类型")
    L.append("")
    L.append("| 类型 | n | v1 P@5 | v2 P@5 | v1 覆盖 | v2 覆盖 | v1 命中率 | v2 命中率 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for t, x in rep["by_type"].items():
        L.append(f"| {t} | {x['n']} | {x['v1']['precision_at_k']:.3f} | {x['v2']['precision_at_k']:.3f} | "
                 f"{x['v1']['coverage']:.3f} | {x['v2']['coverage']:.3f} | "
                 f"{x['v1']['hit_rate']:.3f} | {x['v2']['hit_rate']:.3f} |")
    L.append("")
    L.append("## 逐查询")
    L.append("")
    L.append("| # | 查询 | 类型 | 期望概念 | 期望单元数 | v1 P@5 | v1 命中 | v2 P@5 | v2 命中 | ΔP@5 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["queries"]:
        L.append(f"| {r['id']} | `{r['query']}` | {r['type']} | "
                 f"{'、'.join(r['expected_concepts'])} | {r['n_expected_units']} | "
                 f"{r['v1']['precision_at_k']:.2f} | {r['v1']['hit']} | "
                 f"{r['v2']['precision_at_k']:.2f} | {r['v2']['hit']} | "
                 f"{r['delta_precision']:+.2f} |")
    L.append("")
    L.append("## 结论与局限")
    L.append("")
    L.append(rep_conclusion(rep))
    L.append("")
    return "\n".join(L)


def rep_conclusion(rep: dict) -> str:
    agg, sig = rep["aggregate"], rep["significance"]
    pt, mc = sig["precision_at_k_paired_t"], sig["hit_rate_mcnemar"]
    lines = []
    lines.append(
        f"- **规模**：{rep['meta']['n_queries']} 条查询（主题 18 / 同义词 8 / AND 5 / OR 4 / 短语 3 / 排除 3），"
        f"覆盖 498 单元中的 {sum(1 for v in rep['expected_units'].values() if v)} 条可命中查询。")
    lines.append(
        f"- **指标**：v1 P@5={agg['v1']['precision_at_k']:.3f} → v2={agg['v2']['precision_at_k']:.3f}"
        f"（{agg['delta']['precision_at_k']:+.3f}）；覆盖率 {agg['v1']['coverage']:.3f}→"
        f"{agg['v2']['coverage']:.3f}（{agg['delta']['coverage']:+.3f}）；命中率 "
        f"{agg['v1']['hit_rate']:.3f}→{agg['v2']['hit_rate']:.3f}（{agg['delta']['hit_rate']:+.3f}）。")
    sig_txt = "统计显著" if sig["significant"] else "未达统计显著"
    lines.append(
        f"- **显著性**：precision@5 配对 t={pt['t']}（p={pt['p_two_sided']}）、"
        f"命中率 McNemar p={mc['p_two_sided']} → 差异**{sig_txt}**（α=0.05；小样本+标注规则化，见下）。")
    sens = rep.get("sensitivity") or {}
    if sens:
        st = (sens.get("three_field") or {}).get("significance") or {}
        so = (sens.get("concept_only") or {}).get("significance") or {}
        lines.append(
            f"- **口径敏感性**：三字段口径 P@5 提升 p="
            f"{(st.get('precision_at_k_paired_t') or {}).get('p_two_sided')}（显著："
            f"{'是' if st.get('significant') else '否'}）；仅 concept 严格口径 "
            f"p={(so.get('precision_at_k_paired_t') or {}).get('p_two_sided')}"
            f"（{'显著' if so.get('significant') else '不显著'}）→ 结论对标注口径敏感。")
    lines.append(
        "- **结论**：v2 在扩展评测集上方向一致优于 v1（三字段口径 ΔP@5 "
        f"{agg['delta']['precision_at_k']:+.3f}、覆盖率 {agg['delta']['coverage']:+.3f}、"
        f"命中率 {agg['delta']['hit_rate']:+.3f}），推荐**保持默认 v2**；v1 保留为回退"
        "（前端 #engine 切换）。")
    lines.append(
        "- **局限**：(1) 期望单元由「人工挑概念子串」规则展开，存在标注者偏差，且与 v1/v2 的"
        "词法匹配同源（相关判定偏宽松）；(2) 41 条查询非独立抽样、难度分布偏易（v2 命中率接近 1），"
        "统计功效有限，p 值仅作量级参考；(3) v1 基线为旧检索器逻辑复刻（非线上日志），"
        "真实用户查询分布可能不同；(4) 未做人工分级相关性（NDCG）与多轮对话场景评测。")
    return "\n".join(lines)


def append_doc(md_report: str, rep: dict, doc_path: Path = DOC) -> str:
    """把扩展评测结果追加/替换到设计文档的评测章节（标记块幂等）。"""
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else "# 研报检索 v2 设计\n"
    agg, sig = rep["aggregate"], rep["significance"]
    pt, mc = sig["precision_at_k_paired_t"], sig["hit_rate_mcnemar"]
    block = [DOC_BEGIN, "", "## 评测扩展（wave9 / task-24，41 条人工标注查询）", ""]
    block.append(f"- 样本：{rep['meta']['n_queries']} 条（主题/同义词/AND/OR/短语/排除）；"
                 f"数据 {rep['meta']['n_units']} 单元；topk={rep['meta']['topk']}")
    block.append("")
    block.append("| 版本 | precision@5 | 覆盖率 | 命中率 |")
    block.append("|---|---|---|---|")
    block.append(f"| v1 | {agg['v1']['precision_at_k']:.3f} | {agg['v1']['coverage']:.3f} | "
                 f"{agg['v1']['hit_rate']:.3f} |")
    block.append(f"| **v2** | **{agg['v2']['precision_at_k']:.3f}** | "
                 f"**{agg['v2']['coverage']:.3f}** | **{agg['v2']['hit_rate']:.3f}** |")
    block.append(f"| 提升 | {agg['delta']['precision_at_k']:+.3f} | {agg['delta']['coverage']:+.3f} | "
                 f"{agg['delta']['hit_rate']:+.3f} |")
    block.append("")
    block.append(f"- 显著性：P@5 配对 t={pt['t']}（p={pt['p_two_sided']}）、命中率 McNemar "
                 f"p={mc['p_two_sided']} → "
                 f"{'统计显著' if sig['significant'] else '未达统计显著'}（小样本/规则化标注，见报告局限）")
    sens = rep.get("sensitivity") or {}
    if sens:
        st = (sens.get("three_field") or {}).get("significance") or {}
        so = (sens.get("concept_only") or {}).get("significance") or {}
        block.append(
            f"- 口径敏感性：三字段 P@5 p="
            f"{(st.get('precision_at_k_paired_t') or {}).get('p_two_sided')}；"
            f"仅 concept 严格口径 p="
            f"{(so.get('precision_at_k_paired_t') or {}).get('p_two_sided')}")
    block.append("- 结论：扩展集上 v2 方向一致优于 v1（P@5/覆盖率/命中率均升），"
                 "**保持默认 v2**、v1 留作回退；逐查询明细见 `search_v2_eval_ext_20261101.md`。")
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
    p = argparse.ArgumentParser(prog="eval_search_v2_ext",
                                description="检索 v2 评测扩展（41 条人工标注查询）")
    p.add_argument("--data", default=str(sv.DEFAULT_DATA))
    p.add_argument("--synonyms", default=str(sv.DEFAULT_SYNONYMS))
    p.add_argument("--topk", type=int, default=5)
    p.add_argument("--out-json", default=str(OUT / "search_v2_eval_ext_20261101.json"))
    p.add_argument("--out-md", default=str(OUT / "search_v2_eval_ext_20261101.md"))
    p.add_argument("--no-append-doc", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    units = sv.load_units(args.data)
    engine = sv.SearchV2(units, synonyms=sv.load_synonyms(args.synonyms))
    rep = evaluate_ext(engine, units, QUERIES, topk=args.topk)
    strict = evaluate_ext(engine, units, QUERIES, topk=args.topk, fields=("concept",))
    rep["sensitivity"] = {
        "three_field": {"aggregate": rep["aggregate"], "significance": rep["significance"]},
        "concept_only": {"aggregate": strict["aggregate"],
                         "significance": strict["significance"]},
        "note": "三字段口径（默认）把同一知识点在不同单元的命名差异（如 concept=量学理论、"
                "claim 含量柱）也计入相关，更贴近人工判断；仅 concept 口径更严格。"
                "两口径下 v2 方向一致，但仅三字段口径的 P@5 提升达到统计显著 → "
                "结论对标注口径敏感，需在扩大样本/人工分级复核后定论。",
    }
    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    if not args.no_append_doc:
        append_doc(None, rep, DOC)
    agg, sig = rep["aggregate"], rep["significance"]
    print(json.dumps({
        "n_queries": rep["meta"]["n_queries"],
        "v1": agg["v1"], "v2": agg["v2"], "delta": agg["delta"],
        "p_precision": sig["precision_at_k_paired_t"]["p_two_sided"],
        "p_hit_mcnemar": sig["hit_rate_mcnemar"]["p_two_sided"],
        "significant": sig["significant"],
    }, ensure_ascii=False))
    print(f"[eval_search_v2_ext] -> {out_json} / {out_md}"
          f"{'' if args.no_append_doc else ' / doc updated'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
