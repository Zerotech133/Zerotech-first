# -*- coding: utf-8 -*-
"""multi 答案质量评估（wave18 支线 BB）：single vs multi 的引用/支撑/幻觉对比（不调 LLM）。

数据来源（复用已有答案，不重新生成）
----------------------------------
- **single**：`research/reports/kb_qa_bank_v3_20261101.json` 的 `answer_validation.samples`
  （wave15 v3 问题库抽验的 12 条单单元答案，含完整 citations：unit_id/book/page/text）；
- **multi**：`research/reports/kb_qa_multi_20261101.json` 的 `comparison`（wave15 multi 评测的同批
  12 条多单元聚合答案；原文含 `[unit_id]` 引用标记，但**未持久化 citation 元数据**）；
- `research/reports/kb_qa_multi_ab_20261101.json`（wave17 AW，30 条）只存统计/无原文 → 本次
  **effective 子集 = 12 对**（12/30，已在报告注明）。

做法（全部复用 wave17 评估逻辑）
------------------------------
1. 统一转 schema v2 样本（expected_refusal/citations_required/answer_type/schema_version）；
2. multi 的引用从答案中的 `[unit_id]` 标记抽取 → 经 kb_qa_questions id 映射解析到 kb_units_all
   （存在性校验；**页码不可得**，标注为 n/a，不参与 page 有效性判定）；
3. 复用 `kb_qa_eval_v2.eval_sample_v2`：引用存在率/页码（single）/支撑度（字符 bigram）/
   幻觉候选 v2/answer_type；统计推断标记数；
4. 对比 single vs multi：可答率、平均引用数、引用存在率、支撑率、幻觉率、回答长度；
   并做「双方均有答案」子集的配对对比；
5. 结论：multi 是否以质量换可答率（可答率↑ 且 支撑率不降/幻觉率不升 → 质量未牺牲；
   支撑率降 >5pp 或幻觉率升 >10pp → 存在质量牺牲）。

输出：research/reports/kb_qa_multi_eval_20261101.{json,md}
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/kb_qa_multi_eval.py eval
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import kb_qa_eval as base  # noqa: E402
import kb_qa_eval_v2 as v2  # noqa: E402
import kb_qa_schema_v2 as sch  # noqa: E402

OUT = ROOT / "research" / "reports"
DEFAULT_V3_BANK = OUT / "kb_qa_bank_v3_20261101.json"
DEFAULT_MULTI_RAW = OUT / "kb_qa_multi_20261101.json"
DEFAULT_AB = OUT / "kb_qa_multi_ab_20261101.json"
DEFAULT_JSON = OUT / "kb_qa_multi_eval_20261101.json"
DEFAULT_MD = OUT / "kb_qa_multi_eval_20261101.md"
SCHEMA_VERSION = "kb_qa_multi_eval.v1"

CITED_ID_RE = re.compile(r"\[([0-9a-fA-F]{6,12}(?:\s*[,，]\s*[0-9a-fA-F]{6,12})*)\]")
INFERENCE_MARKERS = ("推断", "预计", "意味着", "可能", "或将", "有望", "测算", "综合来看",
                     "因此", "判断", "认为", "预期")


# --------------------------------------------------------------------------- #
# 引用标记解析 / 样本转换（纯函数，可单测）
# --------------------------------------------------------------------------- #
def extract_cited_ids(answer: str) -> list[str]:
    """从答案文本抽取 [unit_id] / [id1, id2] 引用标记（去重保序）。"""
    out: list[str] = []
    for m in CITED_ID_RE.finditer(str(answer or "")):
        for part in re.split(r"[,，]", m.group(1)):
            uid = part.strip().lower()
            if uid and uid not in out:
                out.append(uid)
    return out


def count_inference_markers(answer: str) -> int:
    text = str(answer or "")
    return sum(1 for m in INFERENCE_MARKERS if m in text)


def strip_citation_markers(answer: str) -> str:
    """去掉多 id 引用标记与页码标记（避免 id/页码内数字被误当幻觉候选）。"""
    text = CITED_ID_RE.sub("", str(answer or ""))
    return re.sub(r"p\.\s?\d+(?:-\d+)?", "", text)


def load_id_map_merged(v3: dict, base_path: Path | str | None = None) -> dict[str, dict]:
    """合并 id 映射：wave13 kb_qa_questions + v3 问题库（source_unit_id → 单元元数据）。

    multi 答案引用的是 wave15 v3 问题库的单元 id，仅用 wave13 的 32 条映射会解析失败。
    """
    id_map = base.load_id_map(base_path or base.DEFAULT_IDMAP)
    for q in v3.get("questions") or []:
        uid = q.get("source_unit_id")
        if uid and uid not in id_map:
            id_map[uid] = {"source_unit_id": uid, "book": q.get("book", ""),
                           "chapter": q.get("chapter", ""),
                           "page_start": q.get("page_start"),
                           "page_end": q.get("page_end"),
                           "concept": q.get("concept", "")}
    return id_map


def citations_from_ids(ids: list[str], units: list[dict],
                       id_map: dict[str, dict]) -> list[dict]:
    """把 multi 答案里的引用 id（无页码元数据）还原为 citation dict 供存在性校验。"""
    out: list[dict] = []
    for uid in ids:
        meta = id_map.get(uid) or {}
        book = meta.get("book") or ""
        unit_idx = base.match_unit_by_meta(units, book=book,
                                           chapter=meta.get("chapter", ""),
                                           p1=meta.get("page_start"),
                                           p2=meta.get("page_end"),
                                           concept=meta.get("concept", ""))
        text = ""
        if unit_idx is not None:
            u = units[unit_idx]
            text = " ".join(str(u.get(k, "") or "") for k in ("concept", "claim", "evidence"))
        out.append({"unit_id": uid, "book": book, "chapter": meta.get("chapter", ""),
                    "page": None, "page_end": None, "origin": "units",
                    "text": text, "page_available": False})
    return out


def to_v2_sample(sid: str, record: dict, mode: str,
                 units: list[dict], id_map: dict[str, dict]) -> dict:
    """AW/v3 记录 → schema v2 样本（single 用原 citations；multi 从答案标记还原）。"""
    answer = str(record.get("answer") or "")
    if mode == "single":
        citations = list(record.get("citations") or [])
        answer_eval = answer
    else:
        citations = citations_from_ids(extract_cited_ids(answer), units, id_map)
        answer_eval = strip_citation_markers(answer)
    refusal = bool(record.get("no_evidence")) or str(record.get("status")) == "no_evidence"
    confidence = record.get("confidence")
    if not isinstance(confidence, dict):
        confidence = {"level": str(confidence or "unknown")}
    raw = {"id": f"{sid}__{mode}", "question": record.get("question"),
           "answer": answer_eval, "citations": citations,
           "category": record.get("category") or record.get("multi_status") or "unknown",
           "no_evidence": refusal, "fallback": bool(record.get("fallback")),
           "confidence": confidence, "theme": record.get("theme"),
           "page_available": mode == "single"}
    sample = sch.infer_sample(raw)
    if refusal:
        sample["expected_refusal"] = True
        sample["citations_required"] = False
        sample["answer_type"] = "refusal"
    return sample


# --------------------------------------------------------------------------- #
# 逐模式指标（复用 kb_qa_eval_v2）
# --------------------------------------------------------------------------- #
def eval_mode_sample(sample: dict, units: list[dict], id_map: dict[str, dict],
                     mode: str) -> dict:
    row = v2.eval_sample_v2(sample, units, id_map, kind=mode)
    if mode == "multi":
        # multi 无 citation 页码元数据：page_ok 无法判定，citation_valid 改为「id 存在」级
        row["citations_page_ok"] = None
        row["page_available"] = False
        row["citation_valid"] = bool(row["n_citations"] > 0
                                     and row["citations_exists"] == row["n_citations"])
    else:
        row["page_available"] = True
    row["inference_markers"] = count_inference_markers(sample.get("answer"))
    row["answer_length"] = len(str(sample.get("answer") or ""))
    row["answered"] = bool(row["answer_type"] != "refusal"
                           and not row.get("expected_refusal"))
    row["hallucination_flags_count"] = len(row.get("hallucination_flags") or [])
    row.pop("sentence_details", None)   # 报告瘦身
    return row


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    answered = [r for r in rows if r["answered"]]
    graded = [r for r in rows if r["supported_ratio"] is not None]
    total_cit = sum(r["n_citations"] for r in rows)
    exists_cit = sum(r["citations_exists"] for r in rows)
    flags = sum(r["hallucination_flags_count"] for r in rows)
    return {
        "n": n,
        "answered": len(answered),
        "answerable_rate": round(len(answered) / n, 4),
        "avg_citations": round(total_cit / n, 3),
        "citation_exists_rate": round(exists_cit / total_cit, 4) if total_cit else None,
        "avg_supported_ratio_all": (round(sum(r["supported_ratio"] or 0.0 for r in rows) / n, 4)
                                    if n else None),
        "avg_supported_ratio_answered": (round(sum(r["supported_ratio"] or 0.0
                                                  for r in answered) / len(answered), 4)
                                         if answered else None),
        "hallucination_flags_total": flags,
        "hallucination_flag_rate": round(flags / n, 4) if n else None,
        "hallucination_samples": sum(1 for r in rows if r["hallucination_flags_count"]),
        "avg_inference_markers": round(sum(r["inference_markers"] for r in rows) / n, 3),
        "avg_answer_length": round(sum(r["answer_length"] for r in rows) / n, 1),
        "answer_type_counts": dict(collections.Counter(r["answer_type"] for r in rows)),
    }


# --------------------------------------------------------------------------- #
# 主评估
# --------------------------------------------------------------------------- #
def load_sources(v3_path=DEFAULT_V3_BANK, multi_path=DEFAULT_MULTI_RAW,
                 ab_path=DEFAULT_AB) -> tuple[dict, dict, dict]:
    v3 = json.loads(Path(v3_path).read_text(encoding="utf-8"))
    multi = json.loads(Path(multi_path).read_text(encoding="utf-8"))
    ab = json.loads(Path(ab_path).read_text(encoding="utf-8")) if Path(ab_path).exists() else {}
    return v3, multi, ab


def build_pairs(v3: dict, multi: dict) -> list[dict]:
    """single（v3 answer_validation.samples）与 multi（multi comparison）按 id 配对。"""
    singles = {s.get("id"): s for s in
               (v3.get("answer_validation") or {}).get("samples") or []}
    pairs = []
    for rec in multi.get("comparison") or []:
        sid = rec.get("id")
        if sid in singles:
            pairs.append({"id": sid, "question": rec.get("question"),
                          "category": rec.get("category"), "theme": rec.get("theme"),
                          "single_record": singles[sid], "multi_record": rec})
    return pairs


def evaluate(pairs: list[dict], units: list[dict], id_map: dict[str, dict],
             ab: dict | None = None, v3: dict | None = None,
             multi: dict | None = None) -> dict:
    pair_rows: list[dict] = []
    for p in pairs:
        s_sample = to_v2_sample(p["id"], p["single_record"], "single", units, id_map)
        m_sample = to_v2_sample(p["id"], p["multi_record"], "multi", units, id_map)
        s_row = eval_mode_sample(s_sample, units, id_map, "single")
        m_row = eval_mode_sample(m_sample, units, id_map, "multi")
        pair_rows.append({
            "id": p["id"], "question": p.get("question"),
            "category": p.get("category"), "theme": p.get("theme"),
            "single": s_row, "multi": m_row,
            "both_answered": bool(s_row["answered"] and m_row["answered"]),
        })
    singles = [r["single"] for r in pair_rows]
    multis = [r["multi"] for r in pair_rows]
    a_single, a_multi = _agg(singles), _agg(multis)
    both = [r for r in pair_rows if r["both_answered"]]
    paired = {
        "n_both_answered": len(both),
        "avg_supported_ratio_single": (round(sum(r["single"]["supported_ratio"] or 0
                                                 for r in both) / len(both), 4) if both else None),
        "avg_supported_ratio_multi": (round(sum(r["multi"]["supported_ratio"] or 0
                                                for r in both) / len(both), 4) if both else None),
        "support_delta": (round(sum((r["multi"]["supported_ratio"] or 0)
                                    - (r["single"]["supported_ratio"] or 0) for r in both)
                                / len(both), 4) if both else None),
        "hallucination_flags_single": sum(r["single"]["hallucination_flags_count"] for r in both),
        "hallucination_flags_multi": sum(r["multi"]["hallucination_flags_count"] for r in both),
        "avg_citations_single": (round(sum(r["single"]["n_citations"] for r in both)
                                       / len(both), 3) if both else None),
        "avg_citations_multi": (round(sum(r["multi"]["n_citations"] for r in both)
                                      / len(both), 3) if both else None),
    }
    delta = {
        "answerable_rate": round(a_multi["answerable_rate"] - a_single["answerable_rate"], 4),
        "avg_citations": round(a_multi["avg_citations"] - a_single["avg_citations"], 3),
        "citation_exists_rate": (round((a_multi["citation_exists_rate"] or 0)
                                       - (a_single["citation_exists_rate"] or 0), 4)),
        "avg_supported_ratio_all": round((a_multi["avg_supported_ratio_all"] or 0)
                                         - (a_single["avg_supported_ratio_all"] or 0), 4),
        "avg_supported_ratio_answered": round((a_multi["avg_supported_ratio_answered"] or 0)
                                              - (a_single["avg_supported_ratio_answered"] or 0), 4),
        "hallucination_flag_rate": round((a_multi["hallucination_flag_rate"] or 0)
                                         - (a_single["hallucination_flag_rate"] or 0), 4),
        "avg_answer_length": round(a_multi["avg_answer_length"] - a_single["avg_answer_length"], 1),
    }
    conclusion = build_conclusion(a_single, a_multi, delta, paired)
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sources": {"single": str(DEFAULT_V3_BANK), "multi_raw": str(DEFAULT_MULTI_RAW),
                        "ab": str(DEFAULT_AB)},
            "n_pairs": len(pair_rows),
            "note": "不调 LLM：复用 wave15 v3 answer_validation(single) 与 kb_qa_multi comparison(multi) 原文答案；"
                    "multi 无 citation 页码元数据（页码判定 n/a，仅校验 id 存在性）",
            "effective_subset": f"{len(pair_rows)} 对（wave17 AW 30 条仅存统计/无原文，故采用有原文的 12 对）",
            "reuse": "kb_qa_eval_v2.eval_sample_v2（引用/支撑/幻觉 v2）+ kb_qa_schema_v2 schema v2",
            "ab_context": (ab or {}).get("summary") or {},
        },
        "summary": {"single": a_single, "multi": a_multi, "delta": delta, "paired": paired},
        "pairs": pair_rows,
        "conclusion": conclusion,
    }


def build_conclusion(single: dict, multi: dict, delta: dict, paired: dict) -> dict:
    support_all = (multi.get("avg_supported_ratio_all") or 0) - (single.get("avg_supported_ratio_all") or 0)
    hallu = (multi.get("hallucination_flag_rate") or 0) - (single.get("hallucination_flag_rate") or 0)
    answer_gain = (multi.get("answerable_rate") or 0) - (single.get("answerable_rate") or 0)
    quality_loss = support_all < -0.05 or hallu > 0.10
    if answer_gain > 0.1 and not quality_loss:
        verdict = "multi 提升可答率且未观测到质量牺牲（支撑率不降/幻觉率不升）"
        stance = "continue_multi"
    elif answer_gain > 0.1 and quality_loss:
        verdict = "multi 以部分答案质量换可答率（支撑率下降或幻觉率上升）"
        stance = "multi_with_guard"
    else:
        verdict = "multi 可答率提升有限，质量对比无显著差异"
        stance = "keep_retrieval_default"
    return {
        "verdict": verdict,
        "stance": stance,
        "answerable_gain": round(answer_gain, 4),
        "support_rate_delta_all": round(support_all, 4),
        "hallucination_rate_delta": round(hallu, 4),
        "quality_loss_detected": bool(quality_loss),
        "paired_support_delta": paired.get("support_delta"),
        "reasons": [
            f"可答率 single {single.get('answerable_rate')} → multi {multi.get('answerable_rate')}"
            f"（Δ={answer_gain:+.4f}）",
            f"支撑率（全样本）single {single.get('avg_supported_ratio_all')} → "
            f"multi {multi.get('avg_supported_ratio_all')}（Δ={support_all:+.4f}）",
            f"幻觉 flag 率 single {single.get('hallucination_flag_rate')} → "
            f"multi {multi.get('hallucination_flag_rate')}（Δ={hallu:+.4f}）",
            f"引用存在率 single {single.get('citation_exists_rate')} → "
            f"multi {multi.get('citation_exists_rate')}",
            f"双方均有答案子集（n={paired.get('n_both_answered')}）支撑率 Δ="
            f"{paired.get('support_delta')}",
        ],
        "caveats": [
            "multi 引用元数据（页码）未持久化，multi 侧页码校验 n/a，引用只验证 unit_id 存在性；",
            "multi citation 从答案标记抽取，可能漏计未打标记的引用；",
            "支撑度为字符 bigram 覆盖率，长答案（multi 平均更长）天然更易命中，需谨慎解读；",
            "有效样本 12 对（AW 30 条中仅 12 条有原文可评估），结论为冒烟级；",
            "不调 LLM、不改答案原文；评估为规则启发式。",
        ],
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def write_md(rep: dict) -> str:
    s, d, c = rep["summary"], (rep["summary"].get("delta") or {}), rep["conclusion"]
    L = ["# multi 答案质量评估（single vs multi：引用/支撑/幻觉）", ""]
    L.append(f"- 生成：{rep['meta'].get('generated_at')}；{rep['meta'].get('note', '')}")
    L.append(f"- 有效子集：{rep['meta'].get('effective_subset', '—')}；"
             f"复用：{rep['meta'].get('reuse', '—')}")
    ab = rep["meta"].get("ab_context") or {}
    if ab:
        L.append(f"- AW 参考（30 条统计，无原文）：{json.dumps(ab, ensure_ascii=False)[:300]}")
    L.append("")
    L.append("## 汇总对比")
    L.append("")
    L.append("| 指标 | single | multi | Δ |")
    L.append("|---|---|---|---|")
    rows = [("可答率", "answerable_rate", True), ("平均引用数", "avg_citations", False),
            ("引用存在率", "citation_exists_rate", True),
            ("支撑率（全样本）", "avg_supported_ratio_all", True),
            ("支撑率（有答案）", "avg_supported_ratio_answered", True),
            ("幻觉 flag 率", "hallucination_flag_rate", True),
            ("幻觉样本数", "hallucination_samples", False),
            ("平均推断标记", "avg_inference_markers", False),
            ("平均答案长度", "avg_answer_length", False)]
    for label, key, is_pct in rows:
        sv = s["single"].get(key)
        mv = s["multi"].get(key)
        dv = d.get(key)
        if is_pct:
            L.append(f"| {label} | {_pct(sv)} | {_pct(mv)} | {_pct(dv)} |")
        else:
            L.append(f"| {label} | {sv} | {mv} | {dv} |")
    L.append("")
    L.append("## 配对子集（双方均有答案）")
    L.append("")
    p = s.get("paired") or {}
    L.append(f"- n={p.get('n_both_answered')}；支撑率 single "
             f"{p.get('avg_supported_ratio_single')} → "
             f"multi {p.get('avg_supported_ratio_multi')}（Δ={p.get('support_delta')}）")
    L.append(f"- 幻觉 flag 数 single {p.get('hallucination_flags_single')} → multi "
             f"{p.get('hallucination_flags_multi')}；平均引用 "
             f"{p.get('avg_citations_single')} → {p.get('avg_citations_multi')}")
    L.append("")
    L.append("## 逐条")
    L.append("")
    L.append("| id | single 状态 | multi 状态 | single 支撑率 | multi 支撑率 | single 引用 | multi 引用 | "
             "single 幻觉 | multi 幻觉 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rep["pairs"]:
        s1, m1 = r["single"], r["multi"]
        L.append(f"| {r['id']} | {s1['answer_type']} | {m1['answer_type']} | "
                 f"{_pct(s1['supported_ratio'])} | {_pct(m1['supported_ratio'])} | "
                 f"{s1['n_citations']} | {m1['n_citations']} | "
                 f"{s1['hallucination_flags_count']} | {m1['hallucination_flags_count']} |")
    L.append("")
    L.append("## 结论：multi 是否牺牲质量换可答率？")
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
# CLI
# --------------------------------------------------------------------------- #
def cmd_eval(args) -> int:
    v3, multi, ab = load_sources(args.v3_bank, args.multi_raw, args.ab)
    pairs = build_pairs(v3, multi)
    if not pairs:
        rep = {"meta": {"schema_version": SCHEMA_VERSION, "n_pairs": 0,
                        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "note": "无可用配对样本（single/multi 原文缺失或 id 不匹配）"},
               "summary": {"single": {"n": 0}, "multi": {"n": 0}, "delta": {}, "paired": {}},
               "pairs": [],
               "conclusion": {"verdict": "无数据：无法评估", "stance": "no_data",
                              "quality_loss_detected": None, "reasons": [], "caveats": []}}
    else:
        units = base.load_units(args.kb)
        id_map = load_id_map_merged(v3, args.id_map)
        rep = evaluate(pairs, units, id_map, ab=ab, v3=v3, multi=multi)
    out_json, out_md = Path(args.json), Path(args.md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    s = rep["summary"]
    print(json.dumps({"n_pairs": rep["meta"].get("n_pairs"),
                      "single_answerable": s["single"].get("answerable_rate"),
                      "multi_answerable": s["multi"].get("answerable_rate"),
                      "support_delta": rep["summary"]["delta"].get("avg_supported_ratio_all"),
                      "hallucination_delta": rep["summary"]["delta"].get("hallucination_flag_rate"),
                      "verdict": rep["conclusion"]["verdict"]}, ensure_ascii=False))
    print(f"[kb_qa_multi_eval] -> {out_json} / {out_md}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="kb_qa_multi_eval",
                                description="multi 答案质量评估（single vs multi，不调 LLM）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("eval", help="评估（复用已有答案）")
    pe.add_argument("--v3-bank", default=str(DEFAULT_V3_BANK))
    pe.add_argument("--multi-raw", default=str(DEFAULT_MULTI_RAW))
    pe.add_argument("--ab", default=str(DEFAULT_AB))
    pe.add_argument("--kb", default=str(base.DEFAULT_KB))
    pe.add_argument("--id-map", default=str(base.DEFAULT_IDMAP))
    pe.add_argument("--json", default=str(DEFAULT_JSON))
    pe.add_argument("--md", default=str(DEFAULT_MD))
    pe.set_defaults(func=cmd_eval)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
