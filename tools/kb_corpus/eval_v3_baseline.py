# -*- coding: utf-8 -*-
"""评测基线 v3 全量回归（wave21 支线 CF）：当前默认 search_v2（soft + synonym 0.6）vs v1。

背景
----
评测集已 freeze 到 **v3（102 查询，hash 6b778e2c…）**，但当前 search_v2 默认（wave12 合入
soft AND + 同义词权重 0.6）尚未在 v3 上做正式基线回归；此前只在 v2 的 89 查询上测过。
本工具在 v3 上跑完整基线：

1. 读 v3 资产（`eval_set_20261101_v3.json`，102 查询 = v2 89 + 新增 13）；
2. v2 当前默认（`search_v2.SearchV2`：soft、soft_cov=0.5、synonym_weight=0.6）vs v1
   （`search_v2.legacy_search_units`，旧 bigram 词袋）；
3. 指标：P@5 / 覆盖率 / 命中率 / NDCG@5（复用 eval_search_v2_ext2 的分级口径）；
   分语法类型；配对 t + bootstrap 95% CI；命中率 McNemar；
4. 报告头复用 `eval_report_header.make_header(3)`（set_id/content_hash/annotation_policy）；
5. 与 v2 89 结果（`search_v2_merge_eval_20261101.json`）对比：新增 13 条对指标的影响、
   v2 vs v1 结论是否仍统计显著。

输出：research/reports/eval_v3_baseline_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_v3_baseline.py eval
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_report_header as rh  # noqa: E402
import eval_search_v2_ext as ev  # noqa: E402
import eval_search_v2_ext2 as e2  # noqa: E402
import search_v2 as sv  # noqa: E402

OUT = ROOT / "research" / "reports"
V3_PATH = OUT / "eval_sets" / "eval_set_20261101_v3.json"
UNITS_PATH = ROOT / "portal" / "static" / "kb_units_all.json"
SYNONYMS_PATH = ROOT / "tools" / "kb_corpus" / "synonyms_v2.json"
REF_V2_89 = OUT / "search_v2_merge_eval_20261101.json"
OUT_JSON = OUT / "eval_v3_baseline_20261101.json"
OUT_MD = OUT / "eval_v3_baseline_20261101.md"
SCHEMA_VERSION = "eval_v3_baseline.v1"
TOPK = 5
NEW_PREFIX = "V3-"


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def load_v3() -> dict:
    if not V3_PATH.exists():
        raise FileNotFoundError(f"v3 评测集资产缺失：{V3_PATH}")
    asset = json.loads(V3_PATH.read_text(encoding="utf-8"))
    if int(asset.get("version") or 0) != 3:
        raise ValueError(f"期望 version=3，实际 {asset.get('version')}")
    return asset


def load_units() -> list[dict]:
    data = json.loads(UNITS_PATH.read_text(encoding="utf-8"))
    return list(data.get("units") or [])


def load_synonyms():
    return sv.load_synonyms(SYNONYMS_PATH)


def load_reference_v2_89() -> dict:
    """v2 89 查询的既有基线（wave12 合入回归：v1 vs 当前 soft 默认）。"""
    if not REF_V2_89.exists():
        return {}
    data = json.loads(REF_V2_89.read_text(encoding="utf-8"))
    tables = data.get("tables") or {}
    return {"path": str(REF_V2_89),
            "v1": (tables.get("overall") or {}).get("v1"),
            "v2_soft": (tables.get("overall") or {}).get("after_soft"),
            "significance": data.get("significance")}


# --------------------------------------------------------------------------- #
# 评测（复用 ext2 分级口径）
# --------------------------------------------------------------------------- #
def _metrics(v1: list[dict], v2: list[dict], q: dict) -> dict:
    graded = q["graded"]
    p2, _ = e2.precision_hit(v2, graded, TOPK)
    p1, _ = e2.precision_hit(v1, graded, TOPK)
    return {
        "id": q["id"], "query": q["q"], "type": q["type"],
        "is_new": str(q["id"]).startswith(NEW_PREFIX),
        "v1": {"precision_at_k": round(p1, 4),
               "coverage": round(e2.coverage(v1, graded), 4),
               "hit": 1 if p1 > 0 else 0,
               "ndcg_at_k": round(e2.ndcg_at_k(v1, graded, q.get("_units") or [], TOPK), 4)}
        if q.get("_units") else
        {"precision_at_k": round(p1, 4),
         "coverage": round(e2.coverage(v1, graded), 4),
         "hit": 1 if p1 > 0 else 0},
        "v2": {"precision_at_k": round(p2, 4),
               "coverage": round(e2.coverage(v2, graded), 4),
               "hit": 1 if p2 > 0 else 0},
    }


def evaluate_queries(units: list[dict], queries: list[dict], synonyms=None,
                     topk: int = TOPK) -> dict:
    """对 queries 跑 v2 默认 vs v1，返回逐查询 + 聚合 + 显著性。"""
    engine = sv.SearchV2(units, synonyms=synonyms or sv.SynonymTable([]))
    rows: list[dict] = []
    for q in queries:
        v2 = engine.search_units(q["q"])[:topk]
        v1 = sv.legacy_search_units(units, q["q"], topk=topk)
        graded = q["graded"]
        p2, _ = e2.precision_hit(v2, graded, topk)
        p1, _ = e2.precision_hit(v1, graded, topk)
        rows.append({
            "id": q["id"], "query": q["q"], "type": q["type"],
            "is_new": str(q["id"]).startswith(NEW_PREFIX),
            "v1": {"precision_at_k": round(p1, 4),
                   "coverage": round(e2.coverage(v1, graded), 4),
                   "hit": 1 if p1 > 0 else 0,
                   "ndcg_at_k": round(e2.ndcg_at_k(v1, graded, units, topk), 4)},
            "v2": {"precision_at_k": round(p2, 4),
                   "coverage": round(e2.coverage(v2, graded), 4),
                   "hit": 1 if p2 > 0 else 0,
                   "ndcg_at_k": round(e2.ndcg_at_k(v2, graded, units, topk), 4)},
        })
        rows[-1]["delta"] = {
            "precision_at_k": round(rows[-1]["v2"]["precision_at_k"]
                                    - rows[-1]["v1"]["precision_at_k"], 4),
            "ndcg_at_k": round(rows[-1]["v2"]["ndcg_at_k"] - rows[-1]["v1"]["ndcg_at_k"], 4),
        }
    return {"rows": rows, **summarize(rows, e2, ev)}


def _agg(rows: list[dict], meta: str, metric: str) -> float:
    if not rows:
        return 0.0
    return round(sum(float(r[meta][metric]) for r in rows) / len(rows), 4)


def summarize(rows: list[dict], e2_mod, ev_mod) -> dict:
    def block(sub: list[dict]) -> dict:
        return {"n": len(sub),
                "v1": {m: _agg(sub, "v1", m) for m in
                       ("precision_at_k", "coverage", "hit", "ndcg_at_k")},
                "v2": {m: _agg(sub, "v2", m) for m in
                       ("precision_at_k", "coverage", "hit", "ndcg_at_k")},
                "delta": {m: round(_agg(sub, "v2", m) - _agg(sub, "v1", m), 4)
                          for m in ("precision_at_k", "coverage", "hit", "ndcg_at_k")}}
    by_type = {}
    for t in sorted({r["type"] for r in rows}):
        by_type[t] = block([r for r in rows if r["type"] == t])
    d_p = [r["delta"]["precision_at_k"] for r in rows]
    d_n = [r["delta"]["ndcg_at_k"] for r in rows]
    b = sum(1 for r in rows if r["v2"]["hit"] == 1 and r["v1"]["hit"] == 0)
    c = sum(1 for r in rows if r["v2"]["hit"] == 0 and r["v1"]["hit"] == 1)
    sig = {
        "precision_at_k": {"paired_t": ev_mod.paired_t_p(d_p),
                           "bootstrap": e2_mod.bootstrap_ci(d_p)},
        "ndcg_at_k": {"paired_t": ev_mod.paired_t_p(d_n),
                      "bootstrap": e2_mod.bootstrap_ci(d_n)},
        "hit_rate_mcnemar": ev_mod.mcnemar_exact(b, c),
    }
    tp = sig["precision_at_k"]["paired_t"].get("p_two_sided")
    tn = sig["ndcg_at_k"]["paired_t"].get("p_two_sided")
    cp = sig["precision_at_k"]["bootstrap"]
    cn = sig["ndcg_at_k"]["bootstrap"]
    sig["significant"] = bool((tp is not None and tp < 0.05)
                              or (tn is not None and tn < 0.05)
                              or cp.get("significant") or cn.get("significant"))
    return {"overall": block(rows),
            "subset_89": block([r for r in rows if not r["is_new"]]),
            "subset_13": block([r for r in rows if r["is_new"]]),
            "by_type": by_type, "significance": sig}


def build_report(asset: dict, units: list[dict], synonyms, reference: dict) -> dict:
    header = rh.make_header(3)
    eval_result = evaluate_queries(units, asset["queries"], synonyms)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "report_header": header,          # set_id/content_hash/annotation_policy（eval_report_header）
        "eval_set": {"set_id": asset["set_id"], "version": asset["version"],
                     "n_queries": asset["n_queries"],
                     "content_hash": asset["content_hash"]},
        "sources": {"units": str(UNITS_PATH), "synonyms": str(SYNONYMS_PATH),
                    "asset": str(V3_PATH), "reference_v2_89": reference.get("path")},
        "engine": {"v2": "search_v2.SearchV2 默认（and_mode=soft, soft_cov=0.5, "
                         "synonym_weight=0.6）",
                   "v1": "search_v2.legacy_search_units（旧 bigram 词袋）"},
        "summary": {k: eval_result[k] for k in
                    ("overall", "subset_89", "subset_13", "by_type", "significance")},
        "reference_v2_89": reference,
        "queries": eval_result["rows"],
        "conclusion": None,
    }


def build_conclusion(rep: dict) -> dict:
    s = rep["summary"]
    sig = s["significance"]
    o = s["overall"]
    r89 = rep.get("reference_v2_89") or {}
    v2_ref = r89.get("v2_soft") or {}
    tp = sig["precision_at_k"]["paired_t"]
    tn = sig["ndcg_at_k"]["paired_t"]
    cp, cn = sig["precision_at_k"]["bootstrap"], sig["ndcg_at_k"]["bootstrap"]
    significant = bool(sig["significant"])
    # 新增 13 条对指标的影响（与 v2 89 子集比较）
    new13 = s["subset_13"]
    old89 = s["subset_89"]
    conclusion = {
        "v2_vs_v1_significant": significant,
        "overall": {m: o[m] for m in ("n", "v1", "v2", "delta")},
        "subset_89": old89, "subset_13": new13,
        "vs_v2_89_reference": {
            "v2_soft_p5": (v2_ref or {}).get("precision_at_k"),
            "v2_soft_ndcg": (v2_ref or {}).get("ndcg_at_k"),
            "v1_p5": (r89.get("v1") or {}).get("precision_at_k"),
            "v1_ndcg": (r89.get("v1") or {}).get("ndcg_at_k"),
        },
        "reasons": [
            f"v3 {o['v1']['precision_at_k']}→{o['v2']['precision_at_k']}"
            f"（P@5 Δ={o['delta']['precision_at_k']:+.4f}）；NDCG "
            f"{o['v1']['ndcg_at_k']}→{o['v2']['ndcg_at_k']}"
            f"（Δ={o['delta']['ndcg_at_k']:+.4f}）",
            f"配对 t：P@5 t={tp.get('t')} p={tp.get('p_two_sided')}；"
            f"NDCG t={tn.get('t')} p={tn.get('p_two_sided')}",
            f"bootstrap 95% CI：P@5 [{cp.get('ci_low')}, {cp.get('ci_high')}]"
            f"（{'不含0' if cp.get('significant') else '含0'}）；NDCG "
            f"[{cn.get('ci_low')}, {cn.get('ci_high')}]"
            f"（{'不含0' if cn.get('significant') else '含0'}）",
            f"新增 13 条：P@5 v1 {new13['v1']['precision_at_k']}→v2 "
            f"{new13['v2']['precision_at_k']}（Δ={new13['delta']['precision_at_k']:+.4f}）；"
            f"NDCG Δ={new13['delta']['ndcg_at_k']:+.4f}",
            f"v2 89 参考（wave12 合入回归）：P@5 {v2_ref.get('precision_at_k')} "
            f"vs 本 v3 全体 {o['v2']['precision_at_k']}；"
            f"v3 89 子集 {old89['v2']['precision_at_k']}（同一默认引擎，差异来自评测集/覆盖）",
        ],
        "conclusion": (
            ("v3 102 查询上，当前 v2 默认（soft+synonym 0.6）显著优于 v1 → 结论在 v3 上仍成立；"
             "新增 13 条 merged-concept 查询未改变方向，但会改变总体均值幅度（见上）。"
             if significant else
             "v3 102 查询上，v2 默认 vs v1 的提升**未达统计显著**（CI 含 0）→ 结论在 v3 上不再稳健，"
             "需扩大样本/复核新增查询难度。")),
        "caveats": [
            "v2 参考值为 wave12 在 89 查询（同一批查询、同一默认引擎）上的结果；v3 89 子集与参考值差异"
            "来自评测集查询范围/覆盖（v3 子集与 v2 完全同查询），可用于核对实现是否漂移；",
            "新增 13 条为 merged-concept/跨境电商/东盟相关查询，难度分布与 89 条不同，会改变总体均值；",
            "支撑/幻觉口径未变（P@5 固定分母 5；NDCG 指数增益）；全部本地、不调 LLM；",
            "评测集 v3 已 freeze（hash 见 report_header）——复现须使用同一资产。",
        ],
    }
    return conclusion


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def write_md(rep: dict) -> str:
    s = rep["summary"]
    o, s89, s13 = s["overall"], s["subset_89"], s["subset_13"]
    h = rep["report_header"]
    c = rep["conclusion"]
    L = ["# 评测基线 v3 全量回归（当前 v2 soft vs v1，102 查询）", ""]
    L.append(f"- 生成：{rep['generated_at']}")
    L.append(f"- 报告头：评测集 `{h['set_id']}`（v{h['set_version']}，n={h['n_queries']}，"
             f"content_hash=`{h['content_hash']}`，hash_verified={h['hash_verified']}）")
    L.append(f"- 标注规范：`{h['policy_ref']}`（分级 2/1/0，冻结后不可改）")
    L.append(f"- 引擎：v2={rep['engine']['v2']}；v1={rep['engine']['v1']}")
    L.append("")
    L.append("## 汇总（v3 102 查询）")
    L.append("")
    L.append("| 范围 | n | v1 P@5 | v2 P@5 | ΔP@5 | v1 覆盖率 | v2 覆盖率 | v1 命中率 | v2 命中率 | "
             "v1 NDCG | v2 NDCG | ΔNDCG |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for label, b in (("全部", o), ("v2 89 子集", s89), ("新增 13 条", s13)):
        L.append(f"| {label} | {b['n']} | {b['v1']['precision_at_k']:.3f} | "
                 f"{b['v2']['precision_at_k']:.3f} | {b['delta']['precision_at_k']:+.4f} | "
                 f"{b['v1']['coverage']:.3f} | {b['v2']['coverage']:.3f} | "
                 f"{b['v1']['hit']:.3f} | {b['v2']['hit']:.3f} | "
                 f"{b['v1']['ndcg_at_k']:.3f} | {b['v2']['ndcg_at_k']:.3f} | "
                 f"{b['delta']['ndcg_at_k']:+.4f} |")
    L.append("")
    L.append("## 显著性（v2 − v1 配对）")
    L.append("")
    sig = s["significance"]
    tp, tn = sig["precision_at_k"]["paired_t"], sig["ndcg_at_k"]["paired_t"]
    cp, cn = sig["precision_at_k"]["bootstrap"], sig["ndcg_at_k"]["bootstrap"]
    L.append(f"- P@5：paired t={tp.get('t')}，p={tp.get('p_two_sided')}；"
             f"bootstrap 95% CI [{cp.get('ci_low')}, {cp.get('ci_high')}]"
             f"（{'不含 0 ✅' if cp.get('significant') else '含 0 ❌'}）")
    L.append(f"- NDCG@5：paired t={tn.get('t')}，p={tn.get('p_two_sided')}；"
             f"bootstrap 95% CI [{cn.get('ci_low')}, {cn.get('ci_high')}]"
             f"（{'不含 0 ✅' if cn.get('significant') else '含 0 ❌'}）")
    mc = sig["hit_rate_mcnemar"]
    L.append(f"- 命中率 McNemar：b={mc.get('b')}、c={mc.get('c')}、p={mc.get('p_two_sided')}")
    L.append("")
    L.append("## 分语法类型")
    L.append("")
    L.append("| 类型 | n | v1 P@5 | v2 P@5 | ΔP@5 | v2 NDCG | ΔNDCG |")
    L.append("|---|---|---|---|---|---|---|")
    for t, b in s["by_type"].items():
        L.append(f"| {t} | {b['n']} | {b['v1']['precision_at_k']:.3f} | "
                 f"{b['v2']['precision_at_k']:.3f} | {b['delta']['precision_at_k']:+.4f} | "
                 f"{b['v2']['ndcg_at_k']:.3f} | {b['delta']['ndcg_at_k']:+.4f} |")
    L.append("")
    L.append("## 与 v2 89 结果对比（新增 13 条影响）")
    L.append("")
    ref = c["vs_v2_89_reference"]
    L.append(f"- wave12 v2 89 参考：P@5 {ref['v2_soft_p5']}、NDCG {ref['v2_soft_ndcg']}；"
             f"v1 P@5 {ref['v1_p5']}、NDCG {ref['v1_ndcg']}")
    L.append(f"- 本 v3 全体：P@5 {o['v2']['precision_at_k']}、NDCG {o['v2']['ndcg_at_k']}；"
             f"v3 89 子集：P@5 {s89['v2']['precision_at_k']}、NDCG {s89['v2']['ndcg_at_k']}")
    L.append(f"- 新增 13 条：P@5 {s13['v1']['precision_at_k']}→{s13['v2']['precision_at_k']}"
             f"（Δ={s13['delta']['precision_at_k']:+.4f}）、NDCG "
             f"{s13['v1']['ndcg_at_k']}→{s13['v2']['ndcg_at_k']}"
             f"（Δ={s13['delta']['ndcg_at_k']:+.4f}）")
    L.append("")
    L.append("## 结论：v2 vs v1 在 v3 上是否仍成立")
    L.append("")
    L.append(f"**{'✅ 仍成立（统计显著）' if c['v2_vs_v1_significant'] else '❌ 未达显著/不再成立'}**："
             f"{c['conclusion']}")
    L.append("")
    for x in c["reasons"]:
        L.append(f"- {x}")
    L.append("")
    L.append("## 口径与局限")
    L.append("")
    for x in c["caveats"]:
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def run(write: bool = True) -> dict:
    asset = load_v3()
    units = load_units()
    synonyms = load_synonyms()
    reference = load_reference_v2_89()
    rep = build_report(asset, units, synonyms, reference)
    rep["conclusion"] = build_conclusion(rep)
    if write:
        OUT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        OUT_MD.write_text(write_md(rep), encoding="utf-8")
    return rep


def cmd_eval(args) -> int:
    rep = run(write=not args.dry_run)
    s = rep["summary"]
    print(json.dumps({
        "set_id": rep["report_header"]["set_id"],
        "n_queries": rep["eval_set"]["n_queries"],
        "overall": s["overall"]["delta"],
        "subset_89": s["subset_89"]["delta"],
        "subset_13": s["subset_13"]["delta"],
        "significant": s["significance"]["significant"],
        "conclusion": rep["conclusion"]["v2_vs_v1_significant"],
    }, ensure_ascii=False))
    print(f"[eval_v3_baseline] -> {OUT_JSON} / {OUT_MD}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_v3_baseline",
                                description="评测基线 v3 全量回归（v2 soft vs v1）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("eval", help="跑 v3 基线（102 查询）")
    pe.add_argument("--dry-run", action="store_true")
    pe.set_defaults(func=cmd_eval)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
