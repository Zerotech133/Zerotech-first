# -*- coding: utf-8 -*-
"""合并模拟扩展：冻结 89 查询评测集（graded）上的 concept 合并影响（内存，不改真实数据）。

背景：
  wave14 AN 用 32 问题自检（@1 +1/@5 0）；本任务改用 wave14 AP 资产化的
  **冻结评测集 v2（89 查询 / graded 2-1-0 / content_hash）** 做更稳的合并影响评估。

做法：
  1. 读 `research/reports/eval_sets/eval_set_20261101_v2.json`（set_id + content_hash + 89 queries）；
  2. 读 kb_units_all.json + wave13 concept_dedup alias_map，**仅在内存**按 action=merge 合并 2 组
     （保留主单元、剔除被合并单元；expected_units 索引按 alias 重映射）；
  3. 用 `search_v2.SearchV2`（当前默认软 AND）分别对「合并前 / 合并后」索引跑 89 查询，
     复用 `eval_search_v2_ext2` 的分级口径计算 P@5 / 覆盖率 / 命中率 / NDCG@5；
  4. 统计：逐查询配对差 → 配对 t（`eval_search_v2_ext.paired_t_p`）+ bootstrap 95% CI
     （`eval_search_v2_ext2.bootstrap_ci`，2000 次固定种子）+ 命中率 McNemar；
  5. 输出 research/reports/merge_sim_89_20261101.{json,md}（报告头带 eval_set set_id + content_hash）。

CLI::

    python tools/kb_corpus/merge_sim_89.py sim [--write] [--json] [--topk 5] [--limit N]

边界：只读真实数据（不写 kb_units_all.json / 不写 eval_set）；只写本任务报告。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
UNITS_ALL = STATIC / "kb_units_all.json"
DEDUP = REPO / "research" / "reports" / "concept_dedup_20261101.json"
EVAL_SET = REPO / "research" / "reports" / "eval_sets" / "eval_set_20261101_v2.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
TOPK = 5
GENERATOR = "tools/kb_corpus/merge_sim_89.py"
DISCLAIMER = ("本模拟仅在内存合并单元并做检索评测，不修改知识库与评测集；"
              "结论供「是否执行真实合并」审批参考，不构成投资建议。")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_stack():
    """加载评测/检索/合并复用模块。"""
    ev = _load_module("merge89_eval_ext", REPO / "tools" / "kb_corpus" / "eval_search_v2_ext.py")
    e2 = _load_module("merge89_eval_ext2", REPO / "tools" / "kb_corpus" / "eval_search_v2_ext2.py")
    sv = _load_module("merge89_search_v2", REPO / "tools" / "kb_corpus" / "search_v2.py")
    merge_mod = _load_module("merge89_merge_sim", REPO / "tools" / "kb_corpus" / "merge_sim.py")
    return ev, e2, sv, merge_mod


# ---------------------------------------------------------------------------
# 读资产 / 合并视图（纯函数）
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def load_eval_set(path: str | Path = EVAL_SET) -> tuple[dict, list[str]]:
    """加载冻结评测集资产（校验 set_id/content_hash/89 queries）。"""
    payload, err = read_json_tolerant(path)
    errors = [err] if err else []
    if not isinstance(payload, dict):
        return {"set_id": None, "content_hash": None, "queries": []}, errors
    queries = [q for q in (payload.get("queries") or []) if isinstance(q, dict) and q.get("id")]
    if not payload.get("set_id"):
        errors.append("评测集缺少 set_id")
    if not payload.get("content_hash"):
        errors.append("评测集缺少 content_hash")
    if len(queries) != 89:
        errors.append(f"评测集查询数应为 89，实际 {len(queries)}")
    return {
        "set_id": payload.get("set_id"),
        "content_hash": payload.get("content_hash"),
        "version": payload.get("version"),
        "schema_version": payload.get("schema_version"),
        "frozen_at": payload.get("frozen_at"),
        "n_queries": len(queries),
        "file_sha256": sha256_file(path),
        "path": str(path),
        "queries": queries,
        "queries_meta": payload.get("queries_meta") or {},
    }, errors


def build_views(units_all: list[dict], dedup: dict, merge_mod, api=None) -> dict:
    """构建合并前/后视图（内存）：返回 plan / 视图列表 / 单元索引→ID 映射。"""
    units_all = [u for u in (units_all or []) if isinstance(u, dict)]
    plan = merge_mod.plan_merge(dedup or {}, units_all, api)
    unit_ids = [merge_mod.retrieval_unit_id(u, api) for u in units_all]
    dropped = set(plan.get("dropped_ids") or [])
    kept_units, kept_idx = [], []
    for i, (unit, uid) in enumerate(zip(units_all, unit_ids)):
        if uid in dropped:
            continue
        kept_units.append(unit)
        kept_idx.append(i)
    return {
        "plan": plan,
        "unit_ids": unit_ids,
        "dropped_ids": dropped,
        "baseline_units": units_all,
        "merged_units": kept_units,
        "kept_indices": kept_idx,
        "dropped_indices": [i for i, uid in enumerate(unit_ids) if uid in dropped],
    }


def remap_expected_units(queries: list[dict], units_all: list[dict], views: dict, merge_mod) -> tuple[list[dict], list[dict]]:
    """把评测集中 expected_units 的 unit 索引按别名重映射（被合并索引→主单元索引）。"""
    unit_ids = views["unit_ids"]
    alias = views["plan"].get("alias_remap") or {}
    id_to_idx = {uid: i for i, uid in enumerate(unit_ids)}
    remapped_queries, records = [], []
    for q in queries:
        new_q = dict(q)
        expected = q.get("expected_units")
        if isinstance(expected, list):
            new_expected, changed = [], []
            for item in expected:
                if not isinstance(item, dict):
                    continue
                idx = item.get("unit")
                new_idx = idx
                if isinstance(idx, int) and 0 <= idx < len(unit_ids):
                    target_id = alias.get(unit_ids[idx])
                    if target_id and target_id in id_to_idx:
                        new_idx = id_to_idx[target_id]
                        changed.append({"query_id": q.get("id"), "from": idx, "to": new_idx})
                new_expected.append({**item, "unit": new_idx})
            # 去重（同一主单元合并后只保留最高 grade）
            by_idx: dict[int, int] = {}
            for item in new_expected:
                if not isinstance(item.get("unit"), int):
                    continue
                g = int(item.get("grade") or 0)
                by_idx[item["unit"]] = max(by_idx.get(item["unit"], 0), g)
            new_q["expected_units"] = [{"unit": i, "grade": g} for i, g in sorted(by_idx.items())]
            records.extend(changed)
        remapped_queries.append(new_q)
    return remapped_queries, records


# ---------------------------------------------------------------------------
# 评测（复用 ext2 口径）
# ---------------------------------------------------------------------------
def evaluate_view(units: list[dict], queries: list[dict], sv, e2, topk: int = TOPK) -> dict:
    """对给定单元集合跑 89 查询 v2 检索评测，返回逐查询 v2 指标 + 汇总。"""
    engine = sv.SearchV2(units)
    rep = e2.evaluate_ext2(engine, units=units, queries=queries, topk=topk)
    rows = []
    for r in rep.get("queries") or []:
        v2 = r.get("v2") or {}
        rows.append({
            "id": r.get("id"), "query": r.get("query"), "type": r.get("type"),
            "n_expected_units": r.get("n_expected_units"),
            "precision_at_k": v2.get("precision_at_k"),
            "coverage": v2.get("coverage"),
            "hit": v2.get("hit"),
            "ndcg_at_k": v2.get("ndcg_at_k"),
            "grades": v2.get("grades"),
        })
    aggregate = (rep.get("aggregate") or {}).get("v2") or {}
    summary = {
        "n": len(rows),
        "precision_at_k": aggregate.get("precision_at_k"),
        "coverage": aggregate.get("coverage"),
        "hit_rate": aggregate.get("hit_rate"),
        "ndcg_at_k": aggregate.get("ndcg_at_k"),
    }
    by_type = {}
    for t, blk in (rep.get("by_type") or {}).items():
        v2b = (blk or {}).get("v2") or {}
        by_type[t] = {"n": (blk or {}).get("n"), "v2": v2b}
    return {"rows": rows, "summary": summary, "by_type": by_type,
            "significance": rep.get("significance") or {},
            "eval_meta": {"topk": topk, "n_units": len(units)}}


def compare_views(baseline: dict, merged: dict, ev, e2, topk: int = TOPK) -> dict:
    """逐查询配对对比 + 配对 t / bootstrap CI / McNemar。"""
    base_rows = {r["id"]: r for r in baseline.get("rows") or [] if isinstance(r, dict)}
    merged_rows = {r["id"]: r for r in merged.get("rows") or [] if isinstance(r, dict)}
    common = [qid for qid in base_rows if qid in merged_rows]
    p_diffs, ndcg_diffs = [], []
    affected = []
    b_hit = c_hit = 0  # McNemar 不一致计数
    for qid in common:
        b, m = base_rows[qid], merged_rows[qid]
        dp = round((m.get("precision_at_k") or 0) - (b.get("precision_at_k") or 0), 4)
        dn = round((m.get("ndcg_at_k") or 0) - (b.get("ndcg_at_k") or 0), 4)
        p_diffs.append(dp)
        ndcg_diffs.append(dn)
        bh, mh = int(b.get("hit") or 0), int(m.get("hit") or 0)
        if mh > bh:
            b_hit += 1
        elif bh > mh:
            c_hit += 1
        if dp != 0 or dn != 0 or (b.get("grades") != m.get("grades")):
            affected.append({
                "query_id": qid, "query": b.get("query"), "type": b.get("type"),
                "before": {"precision_at_k": b.get("precision_at_k"), "coverage": b.get("coverage"),
                           "hit": b.get("hit"), "ndcg_at_k": b.get("ndcg_at_k"), "grades": b.get("grades")},
                "after": {"precision_at_k": m.get("precision_at_k"), "coverage": m.get("coverage"),
                          "hit": m.get("hit"), "ndcg_at_k": m.get("ndcg_at_k"), "grades": m.get("grades")},
                "delta": {"precision_at_k": dp, "ndcg_at_k": dn,
                          "hit": mh - bh,
                          "coverage": round((m.get("coverage") or 0) - (b.get("coverage") or 0), 4)},
            })
    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0
    stats = {
        "precision_at_k": {"mean_delta": _mean(p_diffs), "paired_t": ev.paired_t_p(p_diffs),
                           "bootstrap": e2.bootstrap_ci(p_diffs)},
        "ndcg_at_k": {"mean_delta": _mean(ndcg_diffs), "paired_t": ev.paired_t_p(ndcg_diffs),
                      "bootstrap": e2.bootstrap_ci(ndcg_diffs)},
        "hit_rate": {"mcnemar": ev.mcnemar_exact(b_hit, c_hit),
                     "improved": b_hit, "regressed": c_hit},
        "n_pairs": len(common),
    }
    return {"per_query": affected, "affected_queries": affected, "stats": stats,
            "mean_delta": {"precision_at_k": stats["precision_at_k"]["mean_delta"],
                           "ndcg_at_k": stats["ndcg_at_k"]["mean_delta"]}}


def decide(comparison: dict) -> tuple[str, str]:
    """根据 P@5/NDCG@5 配对差与 CI 给出是否值得真实合并的判定。"""
    st = comparison["stats"]
    p = st["precision_at_k"]
    n = st["ndcg_at_k"]
    mean = p["mean_delta"]
    ci = p["bootstrap"] or {}
    lo = ci.get("ci_low")
    pval = (p["paired_t"] or {}).get("p_two_sided")
    if mean > 0 and ((lo is not None and lo > 0) or (pval is not None and pval < 0.05)):
        return ("worth_merging_robustly",
                f"合并后 P@5 平均 {mean:+.4f}（bootstrap 95% CI [{lo}, {ci.get('ci_high')}]，"
                f"配对 t p={pval}），收益在 89 查询上稳健为正 → 值得执行真实合并（仍需审批+备份+更新引用）。")
    if mean > 0:
        return ("worth_merging_directionally",
                f"合并后 P@5 平均 {mean:+.4f}（CI [{lo}, {ci.get('ci_high')}]，p={pval}）：方向为正但未达统计显著，"
                "可合并但收益不稳健，建议扩样复核或与问题库 alias 更新一起执行。")
    if mean == 0 and n["mean_delta"] == 0:
        return ("neutral",
                "合并后 P@5/NDCG@5 与合并前持平：去重收益主要是知识库整洁度，对 89 查询检索无直接收益；"
                "可合并但优先级不高（需审批+备份）。")
    return ("not_worth_merging_now",
            f"合并后 P@5 平均 {mean:+.4f}（NDCG {n['mean_delta']:+.4f}）为负或不利："
            "不建议直接合并，应先修正评测/引用映射或检索排序。")


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(*, eval_set: dict, views: dict, baseline: dict, merged: dict,
                 comparison: dict, remap_records: list[dict], topk: int,
                 generated_at: str | None = None) -> dict:
    verdict, conclusion = decide(comparison)
    st = comparison["stats"]
    return {
        "schema_version": 1,
        "date": DATE_TAG,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "in_memory_merge_simulation",
        "head": {
            "eval_set_id": eval_set.get("set_id"),
            "eval_set_content_hash": eval_set.get("content_hash"),
            "eval_set_file_sha256": eval_set.get("file_sha256"),
            "eval_set_version": eval_set.get("version"),
            "frozen_at": eval_set.get("frozen_at"),
            "n_queries": eval_set.get("n_queries"),
            "topk": topk,
        },
        "policy": {
            "merge_source": "concept_dedup_20261101.json groups[action=merge] + alias_map",
            "merge_strategy": "保留主单元、剔除被合并单元；expected_units 索引按 alias 重映射",
            "retrieval": "search_v2.SearchV2 当前默认（软 AND），复用 eval_search_v2_ext2 分级口径",
            "metrics": ["precision_at_k(P@5)", "coverage", "hit_rate", "ndcg_at_k(NDCG@5)"],
            "stats": ["paired_t（正态近似）", "bootstrap 95% CI（2000 次，seed=42）", "McNemar（命中率）"],
            "modifies_real_data": False,
        },
        "merge_plan": views["plan"],
        "expected_units_remap": {
            "records": remap_records,
            "count": len(remap_records),
            "note": "评测集 expected_units 中被合并单元的索引已映射到主单元（只用于本模拟，不改资产文件）",
        },
        "results": {
            "baseline": {"summary": baseline.get("summary"), "by_type": baseline.get("by_type"),
                         "eval_meta": baseline.get("eval_meta")},
            "merged": {"summary": merged.get("summary"), "by_type": merged.get("by_type"),
                       "eval_meta": merged.get("eval_meta")},
        },
        "comparison": {
            "n_pairs": comparison["stats"]["n_pairs"],
            "mean_delta": comparison["mean_delta"],
            "stats": st,
            "affected_queries": comparison["affected_queries"],
        },
        "verdict": verdict,
        "conclusion": conclusion,
        "recommendation": {
            "action": {"worth_merging_robustly": "值得合并（进入审批流程）",
                       "worth_merging_directionally": "方向为正，可合并但需扩样/配套引用更新",
                       "neutral": "可合并不紧急",
                       "not_worth_merging_now": "暂缓合并"}.get(verdict, "暂缓合并"),
            "preconditions": [
                "人工确认 2 组跨研报重复内容确为同一知识（claim 全同）",
                "备份 kb_units_all.json 与知识单元源 jsonl",
                "同步更新下游引用（问题库 source_unit_id / 评测 expected_units）或保留 alias 解析层",
                "刷新门户缓存；数据变更按仓库纪律审批后执行（本工具不执行）",
            ],
            "risk": ("若合并后不更新引用，被剔除单元的评测/问题来源会变成假 miss；"
                     "本模拟已按 alias 重映射 expected_units，等价于「引用同步更新」的理想情形。"),
        },
        "real_data_unchanged": None,  # run() 中填充
        "disclaimer": DISCLAIMER,
    }


def render_markdown(report: dict) -> str:
    head = report["head"]
    st = report["comparison"]["stats"]
    p, n = st["precision_at_k"], st["ndcg_at_k"]
    bsum = report["results"]["baseline"]["summary"] or {}
    msum = report["results"]["merged"]["summary"] or {}

    def metric(summary: dict, key: str) -> str:
        total = summary.get("n") or summary.get("total") or head.get("n_queries")
        value = summary.get(key)
        k = {"precision_at_k": "@5", "coverage": "cov", "hit_rate": "hit", "ndcg_at_k": "NDCG@5"}.get(key, key)
        if value is None:
            return "—"
        return f"{value:.3f}" if isinstance(value, float) else str(value)
    lines = [
        "# concept 合并影响模拟 · 89 查询冻结评测（2026-11-01）",
        "",
        f"> 评测集：`{head['eval_set_id']}` v{head['eval_set_version']} · "
        f"**content_hash** `{head['eval_set_content_hash']}` · {head['n_queries']} 查询 · top-{head['topk']}",
        f"> 模式：**内存合并（不改真实数据）** · 生成于 {report['generated_at']}",
        f"> 结论：**{report['recommendation']['action']}**（`{report['verdict']}`）",
        "",
        "## 一、合并计划",
        "",
        f"- 单元：**{report['merge_plan']['units_before']} → {report['merge_plan']['units_after']}**"
        f"（合并 {report['merge_plan']['merge_groups']} 组，剔除 {len(report['merge_plan']['dropped_ids'])} 个被合并单元）",
        f"- expected_units 重映射：{report['expected_units_remap']['count']} 处",
        "",
        "| 组ID | concept | 主单元(保留) | 被合并单元(剔除) |",
        "|---|---|---|---|",
    ]
    for g in report["merge_plan"]["groups"]:
        lines.append(f"| {g['group_id']} | {' / '.join(g['concept_forms'])} | `{g['primary_unit_id']}` | "
                     f"{', '.join('`' + x + '`' for x in g['dropped_unit_ids']) or '—'} |")
    lines += ["", "## 二、合并前后指标（89 查询，搜索 v2 默认软 AND）", "",
              "| 口径 | P@5 | 覆盖率 | 命中率 | NDCG@5 |", "|---|---|---|---|---|"]
    for label, summary in (("合并前", bsum), ("合并后", msum)):
        lines.append(f"| {label} | {metric(summary, 'precision_at_k')} | {metric(summary, 'coverage')} | "
                     f"{metric(summary, 'hit_rate')} | {metric(summary, 'ndcg_at_k')} |")
    lines += ["", "## 三、配对差与显著性", "",
              "| 指标 | 平均 Δ | 配对 t | p(双侧) | bootstrap 95% CI | 显著 |",
              "|---|---|---|---|---|---|"]
    for label, block in (("P@5", p), ("NDCG@5", n)):
        pt = block["paired_t"] or {}
        ci = block["bootstrap"] or {}
        lines.append(f"| {label} | {block['mean_delta']:+.4f} | {pt.get('t')} | {pt.get('p_two_sided')} | "
                     f"[{ci.get('ci_low')}, {ci.get('ci_high')}] | {'是' if ci.get('significant') else '否'} |")
    mc = st["hit_rate"]["mcnemar"]
    lines.append(f"| 命中率 | {st['hit_rate']['improved'] - st['hit_rate']['regressed']:+d}（McNemar） | — | "
                 f"{mc.get('p_two_sided')} | — | 不一致 b/c={mc.get('b')}/{mc.get('c')} |")
    lines += ["", "## 四、受影响查询（逐条）", ""]
    aff = report["comparison"]["affected_queries"]
    if aff:
        lines += ["| # | 查询ID | 查询 | P@5 前→后 | NDCG 前→后 | hit 变化 | 等级前→后 |",
                  "|---|---|---|---|---|---|---|"]
        for i, a in enumerate(aff, 1):
            b, m = a["before"], a["after"]
            lines.append(f"| {i} | `{a['query_id']}` | {a['query']} | {b['precision_at_k']:.3f}→{m['precision_at_k']:.3f} | "
                         f"{b['ndcg_at_k']:.3f}→{m['ndcg_at_k']:.3f} | {a['delta']['hit']:+d} | "
                         f"{b.get('grades')}→{m.get('grades')} |")
    else:
        lines.append("- 无受影响查询（所有指标不变）")
    cov = report.get("eval_set_coverage") or {}
    lines += ["", "## 五、评测集覆盖性（为什么 0 变化）", "",
              f"- 被合并概念未被 89 查询提及的组数：**{cov.get('blind_group_count')}**"]
    for g in cov.get("groups") or []:
        lines.append(f"  - `{g['group_id']}` {' / '.join(g['concept_forms'])}：提及查询 {g['count']} 个"
                     + (f"（{', '.join(g['mentioned_by_queries'][:5])}）" if g["mentioned_by_queries"] else ""))
    lines += ["", f"> {cov.get('note')}", "", "## 六、结论与建议", "", f"**{report['conclusion']}**", "",
              f"- 判定：`{report['verdict']}` → {report['recommendation']['action']}",
              "", "### 前置条件（若执行真实合并）", ""]
    lines += [f"{i}. {x}" for i, x in enumerate(report["recommendation"]["preconditions"], 1)]
    lines += ["", f"### 风险", "", report["recommendation"]["risk"], "",
              "## 七、边界与说明", "",
              "1. 全程**内存模拟**：不写 kb_units_all.json、不写 eval_set 资产；只写本报告。",
              "2. 评测集来自 `research/reports/eval_sets/eval_set_20261101_v2.json`（wave10 冻结 89 查询，未重新标注）；"
              "报告头已携带 set_id + content_hash 便于追溯。",
              "3. 检索/指标/统计全部复用既有评测链（search_v2 / eval_search_v2_ext2 / eval_search_v2_ext），未另立口径。",
              "4. 合并收益仅对「引用已同步更新（expected_units 重映射）」的理想情形成立；真实执行必须同步更新下游引用。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程 / CLI
# ---------------------------------------------------------------------------
def run(*, units_all_path: str | Path = UNITS_ALL, dedup_path: str | Path = DEDUP,
        eval_set_path: str | Path = EVAL_SET, out_dir: str | Path = OUT_DIR,
        topk: int = TOPK, write: bool = False, limit: int | None = None,
        generated_at: str | None = None) -> dict:
    """执行 89 查询合并影响模拟（默认 dry-run：不写盘）。"""
    ev, e2, sv, merge_mod = load_stack()
    units_json, units_err = read_json_tolerant(units_all_path)
    dedup, dedup_err = read_json_tolerant(dedup_path)
    eval_set, eval_errors = load_eval_set(eval_set_path)
    units_all = (units_json or {}).get("units") if isinstance(units_json, dict) else []
    queries = eval_set.get("queries") or []
    if limit:
        queries = queries[:max(1, int(limit))]

    views = build_views(units_all or [], dedup or {}, merge_mod)
    remapped_queries, remap_records = remap_expected_units(queries, units_all or [], views, merge_mod)

    baseline = evaluate_view(views["baseline_units"], remapped_queries, sv, e2, topk)
    merged = evaluate_view(views["merged_units"], remapped_queries, sv, e2, topk)
    comparison = compare_views(baseline, merged, ev, e2, topk)

    # 评测集覆盖性：89 查询是否触及被合并的 2 组概念（决定「0 变化」是安全还是评测盲区）
    eval_coverage = []
    for g in views["plan"].get("groups") or []:
        forms = [f for f in (g.get("concept_forms") or []) if f]
        hit_ids = []
        for q in queries:
            text = str(q.get("q") or "")
            graded_text = " ".join(str(k) for gd in (q.get("graded") or {}).values() for k in (gd or []))
            if any(f and (f in text or f in graded_text) for f in forms):
                hit_ids.append(q.get("id"))
        eval_coverage.append({"group_id": g.get("group_id"), "concept_forms": forms,
                              "mentioned_by_queries": hit_ids, "count": len(hit_ids)})
    blind = [x for x in eval_coverage if x["count"] == 0]

    report = build_report(eval_set=eval_set, views=views, baseline=baseline, merged=merged,
                          comparison=comparison, remap_records=remap_records, topk=topk,
                          generated_at=generated_at)
    report["eval_set_coverage"] = {
        "groups": eval_coverage,
        "blind_group_count": len(blind),
        "note": ("被合并概念未被任何 89 查询提及 → 合并对本评测集指标无影响（0 变化是预期，而非收益/风险证据）；"
                 "收益验证见 wave14 32 问题自检（@1 +1）与问题库侧" if blind else
                 "被合并概念已被评测查询覆盖，0 变化即「合并对检索无伤害」的直接证据"),
    }
    report["sources"] = {
        "units_all": {"path": str(units_all_path), "error": units_err,
                      "declared_total": (units_json or {}).get("total") if isinstance(units_json, dict) else None,
                      "sha256_before": sha256_file(units_all_path)},
        "dedup": {"path": str(dedup_path), "error": dedup_err, "sha256_before": sha256_file(dedup_path)},
        "eval_set": {"path": str(eval_set_path), "errors": eval_errors,
                     "set_id": eval_set.get("set_id"), "content_hash": eval_set.get("content_hash"),
                     "file_sha256_before": eval_set.get("file_sha256")},
    }
    report["real_data_unchanged"] = (
        report["sources"]["units_all"]["sha256_before"] == sha256_file(units_all_path)
        and report["sources"]["eval_set"]["file_sha256_before"] == sha256_file(eval_set_path)
    )
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"merge_sim_89_{DATE_TAG}.json"
        mp = out / f"merge_sim_89_{DATE_TAG}.md"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(report), encoding="utf-8")
        report["_outputs"] = {"json": str(jp), "md": str(mp)}
        report["real_data_unchanged"] = (
            report["sources"]["units_all"]["sha256_before"] == sha256_file(units_all_path)
            and report["sources"]["eval_set"]["file_sha256_before"] == sha256_file(eval_set_path)
        )
    return report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="concept 合并影响模拟 · 冻结 89 查询评测（内存，不改真实数据）")
    parser.add_argument("command", nargs="?", default="sim", choices=["sim"], help="sim=模拟（默认）")
    parser.add_argument("--units-all", default=str(UNITS_ALL))
    parser.add_argument("--dedup", default=str(DEDUP))
    parser.add_argument("--eval-set", default=str(EVAL_SET))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--topk", type=int, default=TOPK)
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 条（0=89 全量）")
    parser.add_argument("--write", action="store_true", help="写 research/reports/merge_sim_89_20261101.{json,md}")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(units_all_path=args.units_all, dedup_path=args.dedup, eval_set_path=args.eval_set,
                 out_dir=args.out_dir, topk=args.topk, write=args.write, limit=args.limit or None)
    head = report["head"]
    b = report["results"]["baseline"]["summary"] or {}
    m = report["results"]["merged"]["summary"] or {}
    if args.json:
        out = {k: v for k, v in report.items() if k != "_outputs"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[merge_sim_89] eval_set={head['eval_set_id']} hash={str(head['eval_set_content_hash'])[:16]}… "
              f"| {head['n_queries']} 查询 top-{head['topk']} | 单元 {report['merge_plan']['units_before']}"
              f"→{report['merge_plan']['units_after']}")
        print(f"  合并前: P@5={b.get('precision_at_k')} 覆盖率={b.get('coverage')} 命中率={b.get('hit_rate')} "
              f"NDCG@5={b.get('ndcg_at_k')}")
        print(f"  合并后: P@5={m.get('precision_at_k')} 覆盖率={m.get('coverage')} 命中率={m.get('hit_rate')} "
              f"NDCG@5={m.get('ndcg_at_k')}")
        st = report["comparison"]["stats"]
        print(f"  配对差: P@5 {st['precision_at_k']['mean_delta']:+.4f} "
              f"(p={ (st['precision_at_k']['paired_t'] or {}).get('p_two_sided') }, "
              f"CI {(st['precision_at_k']['bootstrap'] or {}).get('ci_low')}~"
              f"{(st['precision_at_k']['bootstrap'] or {}).get('ci_high')}) | "
              f"NDCG@5 {st['ndcg_at_k']['mean_delta']:+.4f}")
        print(f"  受影响查询: {len(report['comparison']['affected_queries'])} 条 | 结论: {report['verdict']} "
              f"→ {report['recommendation']['action']}")
        print(f"  {report['conclusion']}")
        print(f"  真实数据未修改: {report['real_data_unchanged']}")
        for name, path in (report.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
