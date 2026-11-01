# -*- coding: utf-8 -*-
"""检索评测两阶段偏差审计（wave11 支线 AF）：wave9（41 查询）vs wave10（89 查询）。

要回答的问题
------------
两阶段结论冲突：wave9 41 条 P@5 提升 +0.141（p=0.0195，显著）；wave10 扩到 89 条后
+0.045（p=0.204，不显著）。本审计读取两份评测 JSON，做三件事：

1. **重叠子集校验**：wave9 的 41 条在 wave10 中以相同 id 复用。逐条比对：
   - 标注是否一致（wave9.expected_concepts == wave10.graded["2"]）；
   - 用 wave10 的 grades 复算「仅 grade-2 相关」口径的 P@5，与 wave9 存档值对比
     （应逐条一致，否则说明检索/标注存在漂移）；
2. **偏差分解**：把 v2−v1 的 P@5 提升变化拆为
   `base(41,g2口径) → overlap(41,grade>0口径) → full(89,grade>0口径)`，
   归因为：标注口径变化（grade-1 拓宽相关集）与查询集变化（新增 48 条的主题/语法构成）；
   同时给出各阶段配对 t 与 bootstrap 95% CI 的变化；
3. **结论**：哪一阶段可信、v1/v2 对外表述规范（allowed / forbidden 说法）。

输出：research/reports/eval_audit_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_audit.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_search_v2_ext as ev  # noqa: E402
import eval_search_v2_ext2 as e2  # noqa: E402

OUT = ROOT / "research" / "reports"
W9_JSON = OUT / "search_v2_eval_ext_20261101.json"
W10_JSON = OUT / "search_v2_eval_ext2_20261101.json"
SCHEMA_VERSION = "eval_audit.v1"
TOPK = 5


# --------------------------------------------------------------------------- #
# 读取与纯函数
# --------------------------------------------------------------------------- #
def load_report(path: Path = W9_JSON) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def overlap_ids(w9: dict, w10: dict) -> list[str]:
    """wave9 与 wave10 的交集 id（保持 wave9 顺序）。"""
    ids10 = {q["id"] for q in w10["queries"]}
    return [q["id"] for q in w9["queries"] if q["id"] in ids10]


def grade2_precision(row: dict) -> float:
    """从 wave10 的分级结果复算「仅 grade=2 相关」的 P@5（等价 wave9 口径）。"""
    grades = (row.get("v2") or {}).get("grades") or []
    return sum(1 for g in grades if g == 2) / float(TOPK)


def grade_pos_precision(row: dict) -> float:
    """wave10 现行口径 P@5（grade>0 即相关）。"""
    grades = (row.get("v2") or {}).get("grades") or []
    return sum(1 for g in grades if g > 0) / float(TOPK)


def _mean(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def subset_aggregate(w10: dict, ids: list[str], variant: str,
                     relevance: str = "graded") -> dict:
    """wave10 JSON 子集上按口径聚合 P@5/覆盖率/命中率/NDCG。"""
    rows = [q for q in w10["queries"] if q["id"] in set(ids)]
    p5, cov, hit, ndcg = [], [], [], []
    for r in rows:
        grades = r[variant].get("grades") or []
        p5.append((sum(1 for g in grades if g == 2) / TOPK) if relevance == "g2"
                  else (sum(1 for g in grades if g > 0) / TOPK))
        cov.append(r[variant]["coverage"])
        hit.append(1 if any(g > 0 for g in grades) else 0)
        ndcg.append(r[variant].get("ndcg_at_k") or 0.0)
    return {"n": len(rows), "precision_at_k": _mean(p5), "coverage": _mean(cov),
            "hit_rate": _mean([float(x) for x in hit]), "ndcg_at_k": _mean(ndcg)}


def paired_delta_p5(w10: dict, ids: list[str], relevance: str = "graded") -> list[float]:
    rows = [q for q in w10["queries"] if q["id"] in set(ids)]
    out: list[float] = []
    for r in rows:
        def _p(variant: str) -> float:
            grades = r[variant].get("grades") or []
            return (sum(1 for g in grades if g == 2) / TOPK) if relevance == "g2" \
                else (sum(1 for g in grades if g > 0) / TOPK)
        out.append(_p("v2") - _p("v1"))
    return out


def compose(w9: dict, w10: dict) -> dict:
    """重叠子集校验 + 偏差分解 + 显著性变化。"""
    ids = overlap_ids(w9, w10)
    w9_by_id = {q["id"]: q for q in w9["queries"]}
    w10_by_id = {q["id"]: q for q in w10["queries"]}
    # 1) 标注一致性 + 复现校验
    ann_same, ann_changed, repro = 0, [], {"v1": [], "v2": []}
    for qid in ids:
        a9 = list(w9_by_id[qid].get("expected_concepts") or [])
        a10 = list((w10_by_id[qid].get("graded") or {}).get("2") or [])
        if a9 == a10:
            ann_same += 1
        else:
            ann_changed.append({"id": qid, "wave9": a9, "wave10": a10})
        for var in ("v1", "v2"):
            stored = w9_by_id[qid][var]["precision_at_k"]
            recomputed = (sum(1 for g in (w10_by_id[qid][var].get("grades") or [])
                              if g == 2) / TOPK)
            repro[var].append({"id": qid, "wave9": stored, "recomputed": round(recomputed, 4),
                               "diff": round(recomputed - stored, 6)})
    repro_max = {var: round(max((abs(x["diff"]) for x in rows), default=0.0), 6)
                 for var, rows in repro.items()}
    # 2) 分阶段指标（v1/v2 与提升）
    stage_base = {var: subset_aggregate(w10, ids, var, "g2") for var in ("v1", "v2")}
    stage_overlap = {var: subset_aggregate(w10, ids, var, "graded") for var in ("v1", "v2")}
    stage_full = {var: subset_aggregate(w10, [q["id"] for q in w10["queries"]], var, "graded")
                  for var in ("v1", "v2")}
    # wave9 存档聚合（口径=g2、n=41）
    stage_w9 = {"v1": {"n": w9["meta"]["n_queries"],
                       "precision_at_k": w9["aggregate"]["v1"]["precision_at_k"]},
                "v2": {"n": w9["meta"]["n_queries"],
                       "precision_at_k": w9["aggregate"]["v2"]["precision_at_k"]}}
    delta = {
        "wave9_reported": round(stage_w9["v2"]["precision_at_k"]
                                - stage_w9["v1"]["precision_at_k"], 4),
        "wave9_reproduced_g2_41": round(stage_base["v2"]["precision_at_k"]
                                        - stage_base["v1"]["precision_at_k"], 4),
        "wave10_overlap_graded_41": round(stage_overlap["v2"]["precision_at_k"]
                                          - stage_overlap["v1"]["precision_at_k"], 4),
        "wave10_full_89": round(stage_full["v2"]["precision_at_k"]
                                - stage_full["v1"]["precision_at_k"], 4),
    }
    effects = {
        "annotation_effect_on_delta": round(
            delta["wave10_overlap_graded_41"] - delta["wave9_reproduced_g2_41"], 4),
        "query_selection_effect_on_delta": round(
            delta["wave10_full_89"] - delta["wave10_overlap_graded_41"], 4),
        "total_delta_change": round(delta["wave10_full_89"] - delta["wave9_reported"], 4),
    }
    # 3) 显著性变化
    d_g2 = paired_delta_p5(w10, ids, "g2")
    d_graded = paired_delta_p5(w10, ids, "graded")
    all_ids = [q["id"] for q in w10["queries"]]
    d_full = paired_delta_p5(w10, all_ids, "graded")
    sig = {
        "wave9_reported": {
            "t": w9["significance"]["precision_at_k_paired_t"]["t"],
            "p": w9["significance"]["precision_at_k_paired_t"]["p_two_sided"],
            "n": w9["meta"]["n_queries"]},
        "overlap_41_g2_rule": {"paired_t": ev.paired_t_p(d_g2),
                               "bootstrap": e2.bootstrap_ci(d_g2)},
        "overlap_41_graded_rule": {"paired_t": ev.paired_t_p(d_graded),
                                   "bootstrap": e2.bootstrap_ci(d_graded)},
        "full_89_graded_rule": {"paired_t": ev.paired_t_p(d_full),
                                "bootstrap": e2.bootstrap_ci(d_full)},
        "wave9_annotation_sensitivity": w9.get("sensitivity"),
    }
    # 4) 查询构成（重叠 vs 新增）
    new_ids = [q["id"] for q in w10["queries"] if q["id"] not in set(ids)]
    comp = {
        "overlap_types": _type_counts(w10, ids),
        "new_types": _type_counts(w10, new_ids),
        "new_subset": {var: subset_aggregate(w10, new_ids, var, "graded")
                       for var in ("v1", "v2")},
        "new_subset_delta_p5": round(
            subset_aggregate(w10, new_ids, "v2", "graded")["precision_at_k"]
            - subset_aggregate(w10, new_ids, "v1", "graded")["precision_at_k"], 4),
    }
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "wave9_json": str(W9_JSON), "wave10_json": str(W10_JSON),
            "n_wave9": w9["meta"]["n_queries"], "n_wave10": w10["meta"]["n_queries"],
            "n_overlap": len(ids), "overlap_ids": ids, "topk": TOPK,
            "principle": "同一子集只改一个变量：先固定 41 条看口径效应，再固定口径看查询集效应",
        },
        "overlap_validation": {
            "annotation_identical": ann_same,
            "annotation_changed": ann_changed,
            "reproduction_max_abs_diff": repro_max,
            "reproduced_exactly": all(v < 1e-6 for v in repro_max.values()),
            "note": "用 wave10 分级 grades 复算仅 grade-2 口径 P@5，逐条对齐 wave9 存档 → "
                    "相同检索器/数据下两阶段可复现；差异只可能来自标注口径或查询集",
        },
        "stages": {
            "wave9_reported_41": stage_w9,
            "overlap_41_g2": {var: {k: stage_base[var][k] for k in
                                    ("n", "precision_at_k", "coverage", "hit_rate")}
                              for var in ("v1", "v2")},
            "overlap_41_graded": stage_overlap,
            "full_89_graded": stage_full,
        },
        "delta_decomposition": {"stages": delta, "effects": effects},
        "significance": sig,
        "composition": comp,
        "conclusion": build_conclusion(delta, effects, sig, comp),
    }


def _type_counts(w10: dict, ids: list[str]) -> dict:
    out: dict[str, int] = {}
    for q in w10["queries"]:
        if q["id"] in set(ids):
            out[q["type"]] = out.get(q["type"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def build_conclusion(delta: dict, effects: dict, sig: dict, comp: dict) -> dict:
    p9 = sig["wave9_reported"]["p"]
    p_over = sig["overlap_41_graded_rule"]["paired_t"]["p_two_sided"]
    p_full = sig["full_89_graded_rule"]["paired_t"]["p_two_sided"]
    ci_full = sig["full_89_graded_rule"]["bootstrap"]
    return {
        "trustworthy_stage": "wave10（89 条，分级标注 + bootstrap CI）",
        "rationale": (
            f"同一 41 条子集上：仅改标注口径（g2→grade>0），提升从 "
            f"{delta['wave9_reproduced_g2_41']:+.3f} 变为 {delta['wave10_overlap_graded_41']:+.3f}"
            f"（标注效应 {effects['annotation_effect_on_delta']:+.3f}）；"
            f"固定口径再加 48 条新查询，提升回到 {delta['wave10_full_89']:+.3f}"
            f"（查询选择效应 {effects['query_selection_effect_on_delta']:+.3f}）。"
            f"p 值：{p9} → {p_over} → {p_full}；全量 89 条 bootstrap 95% CI "
            f"[{ci_full['ci_low']}, {ci_full['ci_high']}] 含 0 → wave9 的显著结论不可复现，"
            f"属于小样本 + 查询构成（新增查询含更多 AND/主题类，v2 严格 AND 在其中吃亏）"
            f"叠加下的乐观偏差。"
        ),
        "effect_size_note": (
            f"规模效应明显：41 条的 ΔP@5 比 89 条大 "
            f"{abs(delta['wave9_reported'] / delta['wave10_full_89']):.1f}×，"
            f"符合小样本效应量波动规律；不能以 41 条结果宣称 v2 精度显著更优。"
        ),
        "statement_norms": {
            "allowed": [
                "v2 提升覆盖率与命中率（89 条：覆盖率 +0.074、命中率 +0.045，"
                "命中率 95.5%→100%）",
                "v2 在同义词/别名类查询上收益显著（P@5 0.275→0.850，n=8）",
                "v2 的 NDCG@5 方向性更高（+0.050）但 95% CI 含 0，未达统计显著",
                "v1 作为回退稳定可用；v2 默认上线属产品选择（覆盖率收益）而非统计显著精度收益",
            ],
            "forbidden": [
                "「v2 精度显著优于 v1」（89 条 P@5 p=0.204、NDCG p=0.106，均不显著）",
                "「v2 P@5 +0.141」（wave9 41 条结果，扩样后不可复现）",
                "只报 p 值不报 CI/n 或只报显著子集（如只报同义词类）",
                "在评测后修改标注关键词以提高指标（事后调参，需按规范冻结并做敏感性分析）",
            ],
        },
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def write_md(rep: dict) -> str:
    meta = rep["meta"]
    ov, st, dec, sig, comp, c = (rep["overlap_validation"], rep["stages"],
                                 rep["delta_decomposition"], rep["significance"],
                                 rep["composition"], rep["conclusion"])
    L = ["# 检索评测两阶段偏差审计（wave9 41 条 vs wave10 89 条）", ""]
    L.append(f"- 生成：{meta['generated_at']}；wave9 n={meta['n_wave9']}、"
             f"wave10 n={meta['n_wave10']}、重叠 {meta['n_overlap']} 条；topk={meta['topk']}")
    L.append(f"- 原则：{meta['principle']}")
    L.append("")
    L.append("## 1) 重叠子集校验")
    L.append("")
    L.append(f"- 标注一致：{ov['annotation_identical']}/{meta['n_overlap']} 条"
             f"（wave9.expected_concepts == wave10.graded['2']），"
             f"变更 {len(ov['annotation_changed'])} 条")
    L.append(f"- 复现校验：用 wave10 grades 复算「仅 grade-2」口径 P@5，与 wave9 存档"
             f"最大绝对差 = {ov['reproduction_max_abs_diff']} → "
             f"{'逐条可复现 ✅' if ov['reproduced_exactly'] else '存在漂移 ⚠️'}")
    L.append("")
    L.append("## 2) 分阶段指标与提升")
    L.append("")
    L.append("| 阶段 | n | 口径 | v1 P@5 | v2 P@5 | ΔP@5 |")
    L.append("|---|---|---|---|---|---|")
    sts = st["wave9_reported_41"]
    L.append(f"| wave9 存档 | 41 | 仅 g2 | {sts['v1']['precision_at_k']:.3f} | "
             f"{sts['v2']['precision_at_k']:.3f} | "
             f"{sts['v2']['precision_at_k'] - sts['v1']['precision_at_k']:+.3f} |")
    o = st["overlap_41_g2"]
    L.append(f"| wave10 重叠复算 | 41 | 仅 g2 | {o['v1']['precision_at_k']:.3f} | "
             f"{o['v2']['precision_at_k']:.3f} | "
             f"{o['v2']['precision_at_k'] - o['v1']['precision_at_k']:+.3f} |")
    og = st["overlap_41_graded"]
    L.append(f"| wave10 重叠 | 41 | grade>0 | {og['v1']['precision_at_k']:.3f} | "
             f"{og['v2']['precision_at_k']:.3f} | "
             f"{og['v2']['precision_at_k'] - og['v1']['precision_at_k']:+.3f} |")
    f = st["full_89_graded"]
    L.append(f"| wave10 全量 | 89 | grade>0 | {f['v1']['precision_at_k']:.3f} | "
             f"{f['v2']['precision_at_k']:.3f} | "
             f"{f['v2']['precision_at_k'] - f['v1']['precision_at_k']:+.3f} |")
    L.append("")
    L.append("## 3) 偏差分解")
    L.append("")
    L.append(f"- 标注口径效应（g2 → grade>0，同一 41 条）：ΔP@5 "
             f"{dec['effects']['annotation_effect_on_delta']:+.3f}")
    L.append(f"- 查询集效应（41 → 89 条，固定 grade>0）：ΔP@5 "
             f"{dec['effects']['query_selection_effect_on_delta']:+.3f}")
    L.append(f"- 总变化：ΔP@5 {dec['stages']['wave9_reported']:+.3f} → "
             f"{dec['stages']['wave10_full_89']:+.3f}（合计 "
             f"{dec['effects']['total_delta_change']:+.3f}）")
    L.append("")
    L.append("## 4) 显著性变化")
    L.append("")
    L.append("| 口径 | n | 配对 t | p | bootstrap 95% CI | CI 不含 0 |")
    L.append("|---|---|---|---|---|---|")
    for label, key in (("wave9 存档", None),
                       ("重叠 41（g2）", "overlap_41_g2_rule"),
                       ("重叠 41（grade>0）", "overlap_41_graded_rule"),
                       ("全量 89", "full_89_graded_rule")):
        if key is None:
            L.append(f"| {label} | {sig['wave9_reported']['n']} | — | "
                     f"{sig['wave9_reported']['p']} | — | — |")
        else:
            blk = sig[key]
            pt, bs = blk["paired_t"], blk["bootstrap"]
            L.append(f"| {label} | {bs['n']} | {pt['t']} | {pt['p_two_sided']} | "
                     f"[{bs['ci_low']}, {bs['ci_high']}] | {'是' if bs['significant'] else '否'} |")
    L.append("")
    sens = sig.get("wave9_annotation_sensitivity") or {}
    if sens:
        tf = (sens.get("three_field") or {}).get("significance") or {}
        co = (sens.get("concept_only") or {}).get("significance") or {}
        L.append("> wave9 自身的标注口径敏感性（concept-only vs 三字段）：concept-only P@5 提升 p="
                 f"{(co.get('precision_at_k_paired_t') or {}).get('p_two_sided')}；"
                 f"三字段 p={(tf.get('precision_at_k_paired_t') or {}).get('p_two_sided')} "
                 "→ 结论本身对口径敏感，须作为敏感性分析并列报告（规范 §7 禁止只留显著口径）。")
    L.append("")
    L.append("## 5) 查询构成（重叠 vs 新增）")
    L.append("")
    L.append(f"- 重叠 41 类型：{comp['overlap_types']}")
    L.append(f"- 新增 48 类型：{comp['new_types']}")
    L.append(f"- 新增子集 ΔP@5：{comp['new_subset_delta_p5']:+.4f}"
             f"（v1 {comp['new_subset']['v1']['precision_at_k']:.3f} / "
             f"v2 {comp['new_subset']['v2']['precision_at_k']:.3f}）")
    L.append("")
    L.append("## 6) 结论")
    L.append("")
    L.append(f"- **可信阶段**：{c['trustworthy_stage']}")
    L.append(f"- {c['rationale']}")
    L.append(f"- {c['effect_size_note']}")
    L.append("")
    L.append("### v1/v2 表述规范")
    L.append("")
    L.append("**允许的说法：**")
    for x in c["statement_norms"]["allowed"]:
        L.append(f"- ✅ {x}")
    L.append("")
    L.append("**禁止的说法：**")
    for x in c["statement_norms"]["forbidden"]:
        L.append(f"- ❌ {x}")
    L.append("")
    L.append("## 7) 局限")
    L.append("")
    for x in ("审计基于两次评测的 JSON 存档，无法还原标注者主观判断过程；",
              "查询非独立随机抽样，「查询集效应」含主题构成差异而非纯规模效应；",
              "bootstrap 仅覆盖查询抽样波动，不覆盖标注不确定性与检索器版本差异；",
              "两阶段使用同一批检索器实现，无法排除实现微调带来的差异；"
              "结论对 v2 在全量查询分布上的真实收益仍需线上埋点/更大独立query集验证。"):
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_audit",
                                description="检索评测两阶段偏差审计（wave9 41 vs wave10 89）")
    p.add_argument("--wave9-json", default=str(W9_JSON))
    p.add_argument("--wave10-json", default=str(W10_JSON))
    p.add_argument("--out-json", default=str(OUT / "eval_audit_20261101.json"))
    p.add_argument("--out-md", default=str(OUT / "eval_audit_20261101.md"))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    w9 = load_report(Path(args.wave9_json))
    w10 = load_report(Path(args.wave10_json))
    rep = compose(w9, w10)
    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    print(json.dumps({
        "n_overlap": rep["meta"]["n_overlap"],
        "annotation_identical": rep["overlap_validation"]["annotation_identical"],
        "reproduced_exactly": rep["overlap_validation"]["reproduced_exactly"],
        "delta_stages": rep["delta_decomposition"]["stages"],
        "effects": rep["delta_decomposition"]["effects"],
        "p_values": {
            "wave9": rep["significance"]["wave9_reported"]["p"],
            "overlap_graded": rep["significance"]["overlap_41_graded_rule"]["paired_t"]["p_two_sided"],
            "full89": rep["significance"]["full_89_graded_rule"]["paired_t"]["p_two_sided"],
        },
        "trustworthy_stage": rep["conclusion"]["trustworthy_stage"],
    }, ensure_ascii=False))
    print(f"[eval_audit] -> {out_json} / {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
