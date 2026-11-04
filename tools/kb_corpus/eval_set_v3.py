# -*- coding: utf-8 -*-
"""评测集 v3（wave19 支线 BG）：补齐 merged-concept 查询 + 合并复评。

背景（wave15 AQ 的盲区）
------------------------
`merge_sim_89` 复评 89 查询时「合并前后 0 变化」，但原因是评测集**没有查询覆盖被合并的 2 组概念**：
- CG-003「东盟中小微企业（MSME）经济地位」（重复单元主/被合并）；
- CG-004「跨境电子商务面临的四大挑战」。
0 变化是评测盲区，不是收益/风险证据。本工具：

1. **v3 评测集**：v2（89 条）全部保留 + 新增 13 条覆盖上述概念及跨境电商/东盟/中小微/贸易相关主题的
   查询（人工 graded 标注 2/1，从知识单元挑期望）；按 `eval_registry` 流程 freeze 为
   **eval_set_20261101_v3**（version=3，hash/规范校验/更新 manifest）；
2. **合并复评**：复用 `merge_sim_89` 的内存合并视图逻辑（不改真实数据），用 v3 重跑
   「合并前 vs 合并后」的 P@5/覆盖率/命中率/NDCG@5，重点看：
   - v2 盲区 vs v3 覆盖（被合并概念现在被哪些查询提及）；
   - v3 全部 101 条的合并前后对比；
   - 新增 13 条 + 受影响查询的逐条变化；
3. 结论：合并对「相关查询」是否有收益、是否值得执行（不改数据，供审批参考）。

输出：
- research/reports/eval_sets/eval_set_20261101_v3.json（+ manifest.json 更新）
- research/reports/eval_set_v3_20261101.md（资产说明）
- research/reports/merge_sim_v3_20261101.{json,md}（合并复评）
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_set_v3.py freeze
      C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_set_v3.py merge-eval
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402

OUT = ROOT / "research" / "reports"
EVAL_DIR = OUT / "eval_sets"
V2_PATH = EVAL_DIR / "eval_set_20261101_v2.json"
V3_PATH = EVAL_DIR / "eval_set_20261101_v3.json"
V3_SET_ID = "eval_set_20261101_v3"
V3_VERSION = 3
UNITS_PATH = ROOT / "portal" / "static" / "kb_units_all.json"
MERGE_SIM_89_JSON = OUT / "merge_sim_89_20261101.json"
MERGE_V3_JSON = OUT / "merge_sim_v3_20261101.json"
MERGE_V3_MD = OUT / "merge_sim_v3_20261101.md"
EVAL_V3_MD = OUT / "eval_set_v3_20261101.md"
TOPK = 5

# 新增 13 条：覆盖被合并概念（CG-003/CG-004）+ 跨境电商/东盟/中小微/贸易主题
NEW_QUERIES: list[dict] = [
    {"id": "V3-01", "q": "东盟中小微企业（MSME）经济地位", "type": "短语",
     "graded": {"2": ["东盟中小微企业"], "1": ["MSME", "中小微"]},
     "note": "精确覆盖被合并概念 CG-003"},
    {"id": "V3-02", "q": "跨境电子商务面临的四大挑战", "type": "短语",
     "graded": {"2": ["跨境电子商务面临的四大挑战"], "1": ["跨境电子商务", "跨境电商"]},
     "note": "精确覆盖被合并概念 CG-004"},
    {"id": "V3-03", "q": "东盟 中小微企业 数字化", "type": "AND",
     "graded": {"2": ["东盟", "中小微"], "1": ["MSME", "数字化"]},
     "note": "CG-003 相关（AND）"},
    {"id": "V3-04", "q": "跨境电商 挑战 物流", "type": "AND",
     "graded": {"2": ["跨境电子商务"], "1": ["物流", "支付", "合规"]},
     "note": "CG-004 相关（AND）"},
    {"id": "V3-05", "q": "数字贸易增长预测", "type": "主题",
     "graded": {"2": ["数字贸易增长预测"], "1": ["数字贸易", "增长"]},
     "note": "东盟数字贸易"},
    {"id": "V3-06", "q": "跨境电商行业增长态势", "type": "主题",
     "graded": {"2": ["跨境电商行业增长态势"], "1": ["跨境电商", "增长"]},
     "note": "跨境电商主题"},
    {"id": "V3-07", "q": "商业支持组织 BSO 能力", "type": "主题",
     "graded": {"2": ["商业支持组织"], "1": ["BSO", "能力"]},
     "note": "MSME 支持体系"},
    {"id": "V3-08", "q": "东盟区域贸易地位", "type": "主题",
     "graded": {"2": ["东盟"], "1": ["区域贸易", "贸易"]},
     "note": "东盟贸易地位"},
    {"id": "V3-09", "q": "中国粮食贸易出口市场", "type": "主题",
     "graded": {"2": ["粮食贸易"], "1": ["出口", "贸易"]},
     "note": "贸易主题"},
    {"id": "V3-10", "q": "跨境电商|数字贸易", "type": "OR",
     "graded": {"2": ["跨境电商", "数字贸易"], "1": ["贸易"]},
     "note": "OR 组合"},
    {"id": "V3-11", "q": "东盟 中小微 融资", "type": "AND",
     "graded": {"2": ["东盟", "中小微"], "1": ["融资", "MSME", "资金"]},
     "note": "MSME 融资（AND）"},
    {"id": "V3-12", "q": "跨境电商 合规 -光伏", "type": "排除",
     "graded": {"2": ["跨境电商", "跨境电子商务"], "1": ["合规", "监管"]},
     "note": "排除无关行业"},
    {"id": "V3-13", "q": "东盟 MSME 技能 课程", "type": "同义词",
     "graded": {"2": ["MSME", "东盟"], "1": ["技能", "课程", "中小微"]},
     "note": "MSME 技能课程"},
]
MERGED_CONCEPT_FORMS = ("东盟中小微企业（MSME）经济地位", "跨境电子商务面临的四大挑战")
NEW_IDS = tuple(q["id"] for q in NEW_QUERIES)


# --------------------------------------------------------------------------- #
# v3 资产构建 / freeze
# --------------------------------------------------------------------------- #
def _load_v2() -> dict:
    return json.loads(V2_PATH.read_text(encoding="utf-8"))


def load_units() -> list[dict]:
    data = json.loads(UNITS_PATH.read_text(encoding="utf-8"))
    return list(data.get("units") or [])


def expand_new_queries(units: list[dict]) -> list[dict]:
    """给新增查询补 expected_units（grade 由 kb_qa_eval_v2/unit_grade 规则展开）。"""
    import eval_search_v2_ext2 as e2
    out = []
    for q in NEW_QUERIES:
        exp = []
        for i, u in enumerate(units):
            g = e2.unit_grade(u, q["graded"])
            if g > 0:
                exp.append({"unit": i, "grade": g})
        if not exp:
            raise ValueError(f"{q['id']} 期望单元为空（标注关键词未命中任何单元）")
        out.append({"id": q["id"], "q": q["q"], "type": q["type"],
                    "source": "v3", "graded": q["graded"], "expected_units": exp,
                    "note": q.get("note")})
    return out


def build_v3_asset(v2: dict, units: list[dict]) -> dict:
    new_queries = expand_new_queries(units)
    queries = list(v2.get("queries") or []) + new_queries
    asset = {
        "schema_version": "eval_set.v1",
        "set_id": V3_SET_ID,
        "version": V3_VERSION,
        "name": f"研报检索评测集 v3（{len(queries)} 条：v2 {len(v2.get('queries') or [])} + "
                f"新增 {len(new_queries)} 条 merged-concept 相关查询，冻结）",
        "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "eval_set_20261101_v2 + eval_set_v3 新增查询（跨境电商/东盟/中小微/贸易）",
        "supersedes": {"set_id": v2.get("set_id"), "version": v2.get("version"),
                       "content_hash": v2.get("content_hash")},
        "n_queries": len(queries),
        "type_distribution": dict(sorted(Counter(q["type"] for q in queries).items(),
                                         key=lambda kv: -kv[1])),
        "annotation_policy": v2.get("annotation_policy"),
        "queries": queries,
        "v3_additions": {"n_new": len(new_queries), "ids": list(NEW_IDS),
                         "blind_groups_covered": [g for g in MERGED_CONCEPT_FORMS
                                                  if any(g in (q.get("q") or "") for q in new_queries)],
                         "note": "新增查询覆盖 wave15 AQ 发现的两组 merged-concept 盲区"},
        "content_hash": "",
    }
    asset["content_hash"] = er.compute_hash(asset)
    return asset


def _update_manifest(asset: dict) -> dict:
    manifest_path = EVAL_DIR / "manifest.json"
    manifest = er._read_manifest(manifest_path)
    entry = {
        "version": asset["version"], "set_id": asset["set_id"],
        "file": V3_PATH.name, "content_hash": asset["content_hash"],
        "created_at": asset["frozen_at"], "n_queries": asset["n_queries"],
        "source": asset["source"], "type_distribution": asset["type_distribution"],
        "annotation_policy_ref": (asset.get("annotation_policy") or {}).get("policy_ref"),
        "validation_ok": True,
    }
    sets = [s for s in manifest.get("sets") or [] if int(s.get("version") or 0) != asset["version"]]
    sets.append(entry)
    sets.sort(key=lambda s: int(s.get("version") or 0))
    manifest.update({"schema_version": "eval_manifest.v1",
                     "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "sets": sets})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return manifest


def freeze(write: bool = True) -> dict:
    v2 = _load_v2()
    units = load_units()
    asset = build_v3_asset(v2, units)
    check = er.validate_asset(asset, verify_hash=True)
    if write and check["ok"]:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        V3_PATH.write_text(json.dumps(asset, ensure_ascii=False, indent=1), encoding="utf-8")
        _update_manifest(asset)
    return {"ok": check["ok"], "errors": check["errors"],
            "set_id": asset["set_id"], "version": asset["version"],
            "n_queries": asset["n_queries"], "content_hash": asset["content_hash"],
            "type_distribution": asset["type_distribution"],
            "v3_additions": asset["v3_additions"], "written": write and check["ok"]}


# --------------------------------------------------------------------------- #
# 合并复评（复用 merge_sim_89 内存合并逻辑）
# --------------------------------------------------------------------------- #
def _load_merge_mod():
    import importlib.util
    path = ROOT / "tools" / "kb_corpus" / "merge_sim_89.py"
    spec = importlib.util.spec_from_file_location("merge_sim_89_for_v3", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 merge_sim_89: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _subset_stats(report: dict, ids: set[str]) -> dict:
    """从 merge_sim_89 report 的 baseline/merged rows 中截取子集指标与逐条变化。"""
    rows = report.get("results", {})
    base = {r["id"]: r for r in (rows.get("baseline") or {}).get("rows") or []}
    merged = {r["id"]: r for r in (rows.get("merged") or {}).get("rows") or []}
    def agg(d):
        sel = [d[i] for i in d if i in ids]
        if not sel:
            return {"n": 0}
        return {"n": len(sel),
                "precision_at_k": round(sum(r["precision_at_k"] for r in sel) / len(sel), 4),
                "coverage": round(sum(r["coverage"] for r in sel) / len(sel), 4),
                "hit_rate": round(sum(r["hit"] for r in sel) / len(sel), 4),
                "ndcg_at_k": round(sum(r["ndcg_at_k"] for r in sel) / len(sel), 4)}
    changes = []
    for qid in sorted(ids):
        b, m = base.get(qid), merged.get(qid)
        if not b or not m:
            continue
        dp = round((m["precision_at_k"] or 0) - (b["precision_at_k"] or 0), 4)
        dn = round((m["ndcg_at_k"] or 0) - (b["ndcg_at_k"] or 0), 4)
        if dp != 0 or dn != 0:
            changes.append({"id": qid, "query": b.get("query"), "type": b.get("type"),
                            "before": {"precision_at_k": b["precision_at_k"],
                                       "ndcg_at_k": b["ndcg_at_k"]},
                            "after": {"precision_at_k": m["precision_at_k"],
                                      "ndcg_at_k": m["ndcg_at_k"]},
                            "delta": {"precision_at_k": dp, "ndcg_at_k": dn}})
    return {"before": agg(base), "after": agg(merged), "changed": changes}


def run_merge_eval(write: bool = True) -> dict:
    """v2 盲区 vs v3 覆盖 + 用 v3 重跑合并前后对比（直接复用 merge_sim_89 的视图/评测函数）。"""
    ms = _load_merge_mod()
    if not V3_PATH.exists():
        freeze(write=True)  # 首次生成 v3 评测集（保留写盘副作用；返回值不参与后续计算）
    v3 = json.loads(V3_PATH.read_text(encoding="utf-8"))
    ev, e2, sv, merge_mod = ms.load_stack()
    units_json, _ = ms.read_json_tolerant(ms.UNITS_ALL)
    dedup, _ = ms.read_json_tolerant(ms.DEDUP)
    units_all = (units_json or {}).get("units") or []
    queries = v3.get("queries") or []
    views = ms.build_views(units_all, dedup or {}, merge_mod)
    remapped_queries, remap_records = ms.remap_expected_units(queries, units_all, views, merge_mod)
    baseline = ms.evaluate_view(views["baseline_units"], remapped_queries, sv, e2, TOPK)
    merged_v = ms.evaluate_view(views["merged_units"], remapped_queries, sv, e2, TOPK)
    comparison = ms.compare_views(baseline, merged_v, ev, e2, TOPK)
    rows_pack = {"results": {"baseline": baseline, "merged": merged_v}}
    new_set = set(NEW_IDS)
    new_subset = _subset_stats(rows_pack, new_set)
    affected = comparison.get("affected_queries") or []
    affected_ids = {a["query_id"] for a in affected}
    affected_subset = (_subset_stats(rows_pack, affected_ids) if affected_ids
                       else {"before": {"n": 0}, "after": {"n": 0}, "changed": []})
    # v2 盲区（读旧报告）vs v3 覆盖（用同一概念形式匹配逻辑本地计算）
    v2_blind = {}
    if MERGE_SIM_89_JSON.exists():
        v2_blind = json.loads(MERGE_SIM_89_JSON.read_text(encoding="utf-8")).get("eval_set_coverage") or {}
    groups = []
    for g in views["plan"].get("groups") or []:
        forms = [f for f in (g.get("concept_forms") or []) if f]
        hit_ids = []
        for q in queries:
            text = str(q.get("q") or "")
            graded_text = " ".join(str(k) for gd in (q.get("graded") or {}).values()
                                   for k in (gd or []))
            if any(f and (f in text or f in graded_text) for f in forms):
                hit_ids.append(q.get("id"))
        groups.append({"group_id": g.get("group_id"), "concept_forms": forms,
                       "mentioned_by_queries": hit_ids, "count": len(hit_ids)})
    blind = [x for x in groups if x["count"] == 0]
    v3_cov = {"groups": groups, "blind_group_count": len(blind),
              "note": ("被合并概念已被 v3 查询覆盖，合并前后变化即合并对相关查询的真实影响"
                       if not blind else "仍有被合并概念未被覆盖")}
    out = {
        "schema_version": "merge_sim_v3.v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/eval_set_v3.py",
        "mode": "in_memory_merge_simulation（复用 merge_sim_89 逻辑，不改真实数据）",
        "eval_set": {"set_id": v3.get("set_id"), "version": v3.get("version"),
                     "n_queries": v3.get("n_queries"),
                     "content_hash": v3.get("content_hash")},
        "v3_additions": v3.get("v3_additions"),
        "real_data_unchanged": bool(
            (units_json or {}).get("total") == 498
            and (units_json or {}).get("total") == len(units_all)),
        "merge_plan": views["plan"],
        "baseline_summary": baseline.get("summary"),
        "merged_summary": merged_v.get("summary"),
        "comparison_stats": comparison.get("stats"),
        "affected_queries": affected,
        "new_queries_subset": new_subset,
        "affected_subset": affected_subset,
        "remap_records": remap_records,
        "coverage": {"v2": v2_blind, "v3": v3_cov,
                     "blind_groups_v2": (v2_blind or {}).get("blind_group_count"),
                     "blind_groups_v3": v3_cov.get("blind_group_count")},
        "v2_merge_sim": {"path": str(MERGE_SIM_89_JSON) if MERGE_SIM_89_JSON.exists() else None,
                         "verdict": (json.loads(MERGE_SIM_89_JSON.read_text(encoding="utf-8"))
                                     .get("verdict") if MERGE_SIM_89_JSON.exists() else None)},
        "conclusion": None,
    }
    out["conclusion"] = build_conclusion(out)
    if write:
        MERGE_V3_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        MERGE_V3_MD.write_text(render_merge_md(out), encoding="utf-8")
    return out


def build_conclusion(rep: dict) -> dict:
    base = rep.get("baseline_summary") or {}
    merged = rep.get("merged_summary") or {}
    dp = round((merged.get("precision_at_k") or 0) - (base.get("precision_at_k") or 0), 4)
    dn = round((merged.get("ndcg_at_k") or 0) - (base.get("ndcg_at_k") or 0), 4)
    nw = rep["new_queries_subset"]
    ndp = round((nw["after"].get("precision_at_k") or 0) - (nw["before"].get("precision_at_k") or 0), 4) \
        if nw.get("before", {}).get("n") else 0.0
    ndn = round((nw["after"].get("ndcg_at_k") or 0) - (nw["before"].get("ndcg_at_k") or 0), 4) \
        if nw.get("before", {}).get("n") else 0.0
    blind_v2 = rep["coverage"]["blind_groups_v2"]
    blind_v3 = rep["coverage"]["blind_groups_v3"]
    changed = rep.get("affected_queries") or []
    if changed and (ndp > 0 or ndn > 0):
        verdict = "v3 覆盖 merged-concept 后，合并对相关查询有正向收益 → 合并值得执行（需按 preconditions）"
        stance = "merge_worth"
    elif changed:
        verdict = "v3 覆盖后合并对相关查询有影响但非正向（或混合）→ 需人工核对受影响查询再决定"
        stance = "merge_mixed"
    else:
        verdict = "v3 覆盖 merged-concept 后合并对相关查询仍无影响 → 合并仅为知识库整洁度收益，优先级低"
        stance = "merge_neutral"
    return {
        "verdict": verdict, "stance": stance,
        "blind_groups_v2": blind_v2, "blind_groups_v3": blind_v3,
        "overall_delta": {"precision_at_k": dp, "ndcg_at_k": dn},
        "new_queries_delta": {"precision_at_k": ndp, "ndcg_at_k": ndn},
        "affected_query_count": len(changed),
        "reasons": [
            f"v2 盲区 {blind_v2} 组 → v3 覆盖后盲区 {blind_v3} 组",
            f"全部 {rep['eval_set']['n_queries']} 条：P@5 {base.get('precision_at_k')}→"
            f"{merged.get('precision_at_k')}（Δ={dp:+.4f}）、NDCG "
            f"{base.get('ndcg_at_k')}→{merged.get('ndcg_at_k')}（Δ={dn:+.4f}）",
            f"新增 {rep['v3_additions']['n_new']} 条：P@5 Δ={ndp:+.4f}、NDCG Δ={ndn:+.4f}；"
            f"受影响查询 {len(changed)} 条",
        ],
        "caveats": ["内存合并模拟，不改真实数据；alias 已按理想情形重映射 expected_units；",
                    "合并后单元数 498→496，被合并重复单元的检索召回变化才可能影响查询；",
                    "新增查询 graded 标注为人工挑选关键词展开，样本 13 条为冒烟级；",
                    "结论供是否执行真实合并审批参考，真实合并需备份+同步下游引用。"],
    }


def render_merge_md(rep: dict) -> str:
    c = rep["conclusion"]
    b, m = rep["baseline_summary"] or {}, rep["merged_summary"] or {}
    nw = rep["new_queries_subset"]
    L = ["# 合并模拟复评 v3（补 merged-concept 查询后重跑）", ""]
    L.append(f"- 生成：{rep['generated_at']}；评测集：{rep['eval_set']['set_id']}"
             f"（v{rep['eval_set']['version']}，n={rep['eval_set']['n_queries']}，"
             f"hash `{rep['eval_set']['content_hash']}`）")
    L.append(f"- 模式：{rep['mode']}；真实数据未改：{rep['real_data_unchanged']}")
    plan = rep.get("merge_plan") or {}
    L.append(f"- 合并计划：{plan.get('merge_groups')} 组、单元 {plan.get('units_before')}→"
             f"{plan.get('units_after')}；alias {plan.get('alias_remap')}")
    L.append("")
    L.append("## v2 盲区 → v3 覆盖")
    L.append("")
    L.append("| 组 | 概念形式 | v2 提及查询 | v3 提及查询 |")
    L.append("|---|---|---|---|")
    v2g = {g["group_id"]: g for g in (rep["coverage"]["v2"] or {}).get("groups") or []}
    for g in (rep["coverage"]["v3"] or {}).get("groups") or []:
        g2 = v2g.get(g["group_id"]) or {}
        L.append(f"| {g['group_id']} | {'；'.join(g.get('concept_forms') or [])} | "
                 f"{g2.get('count', '—')} | {g.get('count')}（{', '.join(g.get('mentioned_by_queries') or [])}） |")
    L.append("")
    L.append("## 合并前后（v3 全量 + 新增子集）")
    L.append("")
    L.append("| 范围 | P@5 前 | P@5 后 | ΔP@5 | NDCG 前 | NDCG 后 | ΔNDCG |")
    L.append("|---|---|---|---|---|---|---|")
    L.append(f"| 全部 {rep['eval_set']['n_queries']} 条 | {b.get('precision_at_k')} | "
             f"{m.get('precision_at_k')} | {c['overall_delta']['precision_at_k']:+.4f} | "
             f"{b.get('ndcg_at_k')} | {m.get('ndcg_at_k')} | "
             f"{c['overall_delta']['ndcg_at_k']:+.4f} |")
    nb, na = nw.get("before") or {}, nw.get("after") or {}
    L.append(f"| 新增 {rep['v3_additions']['n_new']} 条 | {nb.get('precision_at_k')} | "
             f"{na.get('precision_at_k')} | {c['new_queries_delta']['precision_at_k']:+.4f} | "
             f"{nb.get('ndcg_at_k')} | {na.get('ndcg_at_k')} | "
             f"{c['new_queries_delta']['ndcg_at_k']:+.4f} |")
    L.append("")
    stats = rep.get("comparison_stats") or {}
    if stats:
        p = (stats.get("precision_at_k") or {})
        n = (stats.get("ndcg_at_k") or {})
        L.append("## 显著性（合并前后配对）")
        L.append("")
        L.append(f"- P@5：mean Δ={p.get('mean_delta')}，paired t="
                 f"{(p.get('paired_t') or {}).get('t')}，p={(p.get('paired_t') or {}).get('p_two_sided')}，"
                 f"bootstrap CI [{(p.get('bootstrap') or {}).get('ci_low')}, "
                 f"{(p.get('bootstrap') or {}).get('ci_high')}]")
        L.append(f"- NDCG@5：mean Δ={n.get('mean_delta')}，paired t="
                 f"{(n.get('paired_t') or {}).get('t')}，p={(n.get('paired_t') or {}).get('p_two_sided')}，"
                 f"bootstrap CI [{(n.get('bootstrap') or {}).get('ci_low')}, "
                 f"{(n.get('bootstrap') or {}).get('ci_high')}]")
        L.append("")
    L.append("## 新增/受影响查询逐条变化")
    L.append("")
    if rep.get("affected_queries"):
        L.append("| 查询 | 类型 | ΔP@5 | ΔNDCG |")
        L.append("|---|---|---|---|")
        for a in rep["affected_queries"]:
            L.append(f"| {a.get('query_id')} `{a.get('query')}` | {a.get('type')} | "
                     f"{a['delta']['precision_at_k']:+.4f} | {a['delta']['ndcg_at_k']:+.4f} |")
    else:
        L.append("- 无受影响查询（合并前后指标/排序完全一致）")
    L.append("")
    L.append("## 结论：合并是否值得")
    L.append("")
    L.append(f"**{c['verdict']}**（stance={c['stance']}）")
    L.append("")
    for x in c["reasons"]:
        L.append(f"- {x}")
    L.append("")
    L.append("### 口径与局限")
    L.append("")
    for x in c["caveats"]:
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# v3 资产说明文档
# --------------------------------------------------------------------------- #
def render_asset_md(asset: dict, check: dict, manifest: dict) -> str:
    L = ["# 评测集 v3 资产说明（eval_set_20261101_v3）", ""]
    L.append(f"- 生成：{asset['frozen_at']}；**{asset['name']}**")
    L.append(f"- set_id：`{asset['set_id']}`；version={asset['version']}；"
             f"n_queries={asset['n_queries']}；content_hash=`{asset['content_hash']}`")
    L.append(f"- 校验：{'通过 ✅' if check['ok'] else '失败 ❌'}；"
             f"{'错误：' + str(check['errors']) if check['errors'] else 'schema/hash/规范字段全过'}")
    L.append(f"- 继承 v2：{asset.get('supersedes')}")
    L.append(f"- 类型分布：{asset['type_distribution']}")
    L.append("")
    L.append("## v3 新增查询（覆盖 wave15 AQ 发现的 merged-concept 盲区）")
    L.append("")
    L.append("| id | 查询 | 类型 | 期望单元数 | 覆盖说明 |")
    L.append("|---|---|---|---|---|")
    new = {q["id"]: q for q in asset["queries"] if q["id"] in NEW_IDS}
    for qid in NEW_IDS:
        q = new.get(qid) or {}
        L.append(f"| {qid} | `{q.get('q')}` | {q.get('type')} | "
                 f"{len(q.get('expected_units') or [])} | {q.get('note') or ''} |")
    L.append("")
    L.append("## 覆盖验证")
    L.append("")
    for form in MERGED_CONCEPT_FORMS:
        hit = [q["id"] for q in asset["queries"]
               if form in str(q.get("q") or "")
               or form in " ".join(str(k) for gd in (q.get("graded") or {}).values()
                                   for k in (gd or []))]
        L.append(f"- 被合并概念「{form}」：v3 中被 {len(hit)} 条查询提及（{', '.join(hit) or '无'}）")
    L.append("")
    L.append("## manifest（v3 条目）")
    L.append("")
    entry = next((s for s in manifest.get("sets") or [] if int(s.get("version") or 0) == 3), {})
    L.append("```json")
    L.append(json.dumps(entry, ensure_ascii=False, indent=1))
    L.append("```")
    L.append("")
    L.append("## 说明")
    L.append("")
    L.append("- v3 = v2 全部 89 条（原样保留，未修改）+ 新增 13 条；v2 资产文件未改动；")
    L.append("- 新增查询的 graded 关键词人工挑选（跨境电商/东盟/中小微/贸易），expected_units 由关键词规则展开；")
    L.append("- 合并复评见 `merge_sim_v3_20261101.md`；本资产供回归评测/质量门使用。")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_freeze(args) -> int:
    rep = freeze(write=not args.dry_run)
    print(json.dumps({k: rep[k] for k in ("ok", "errors", "set_id", "version",
                                          "n_queries", "content_hash", "written")},
                     ensure_ascii=False))
    if not rep["ok"]:
        return 1
    if rep["written"]:
        asset = json.loads(V3_PATH.read_text(encoding="utf-8"))
        check = er.validate_asset(asset, verify_hash=True)
        manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
        EVAL_V3_MD.write_text(render_asset_md(asset, check, manifest), encoding="utf-8")
    return 0


def cmd_merge_eval(args) -> int:
    rep = run_merge_eval(write=not args.dry_run)
    print(json.dumps({"n_queries": rep["eval_set"]["n_queries"],
                      "baseline": rep["baseline_summary"],
                      "merged": rep["merged_summary"],
                      "new_queries_subset": {"before": rep["new_queries_subset"]["before"],
                                             "after": rep["new_queries_subset"]["after"]},
                      "affected": len(rep["affected_queries"]),
                      "blind_v2": rep["coverage"]["blind_groups_v2"],
                      "blind_v3": rep["coverage"]["blind_groups_v3"],
                      "verdict": rep["conclusion"]["verdict"]}, ensure_ascii=False))
    print(f"[eval_set_v3] merge-eval -> {MERGE_V3_JSON} / {MERGE_V3_MD}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_set_v3",
                                description="评测集 v3（补 merged-concept 查询）+ 合并复评")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("freeze", help="构建 v3 资产（hash/校验/manifest）")
    pf.add_argument("--dry-run", action="store_true")
    pf.set_defaults(func=cmd_freeze)
    pm = sub.add_parser("merge-eval", help="用 v3 重跑合并前后对比")
    pm.add_argument("--dry-run", action="store_true")
    pm.set_defaults(func=cmd_merge_eval)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
