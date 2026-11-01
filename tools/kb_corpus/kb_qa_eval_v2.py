# -*- coding: utf-8 -*-
"""问答答案质量评估 v2（wave17 支线 AX）：schema v2 + 幻觉规则调优（不调 LLM）。

相对 wave16 `kb_qa_eval.py` 的改进
--------------------------------
1. **样本 schema v2**：读取迁移后的样本（`expected_refusal/citations_required/answer_type/
   schema_version`，见 `kb_qa_schema_v2.py`），评估时区分「预期拒答」与「有引用答案」，
   拒答样本不再因「无引用」判为质量问题；并做 schema 校验。
2. **幻觉检测 v2 降误报**：
   - 数字白名单：1~10 等小整数/序数（"1. **"、"第2"、列表序号）不再算候选；
   - 列表序号/「第N」模式先从答案文本中剔除，再抽数字；
   - 英文 token 仅当 **≥3 个字母** 且未在引用文本出现才算候选（"AI" 等 2 字母不标）；
   - 仍过滤 ≥7 位长数字、`rp_` 研报号、12 位 hex 引用 id。
3. **前后对比**：同一批样本同时用 v1（wave16）与 v2 规则跑幻觉检测，输出
   候选数/样本数变化、被消除的误报 token 清单，以及支撑率与引用指标（复用 wave16 计算）。

输出：research/reports/kb_qa_eval_v2_20261101.json / .md
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/kb_qa_eval_v2.py eval
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
import kb_qa_schema_v2 as sch  # noqa: E402

OUT = ROOT / "research" / "reports"
DEFAULT_MAIN = ROOT / "portal" / "static" / "kb_qa_samples.json"
DEFAULT_EXPANDED = OUT / "kb_qa_samples_expanded_20261101.json"
DEFAULT_JSON = OUT / "kb_qa_eval_v2_20261101.json"
DEFAULT_MD = OUT / "kb_qa_eval_v2_20261101.md"
SCHEMA_VERSION = "kb_qa_eval_v2.v1"

# v2 幻觉规则参数
NUMBER_WHITELIST = {str(i) for i in range(1, 11)}     # 小整数/序数白名单
LIST_MARKER_RE = re.compile(r"(?m)(?:^|\s)\d+[\.、)](?:\s|(?=\*))")
ORDINAL_RE = re.compile(r"第\s*\d+")
MIN_ENGLISH_LETTERS = 3


# --------------------------------------------------------------------------- #
# 幻觉检测 v2（纯函数，可单测）
# --------------------------------------------------------------------------- #
def extract_entities_v2(answer: str) -> dict:
    """v2 实体抽取：剔除列表序号/第N后，数字过滤白名单与长串；英文仅 ≥3 字母。"""
    text = base.CITATION_MARK_RE.sub("", str(answer or ""))
    text = LIST_MARKER_RE.sub(" ", text)
    text = ORDINAL_RE.sub(" ", text)
    nums: list[str] = []
    for n in base.NUM_RE.findall(text):
        if len(n) > 6:
            continue
        if n in NUMBER_WHITELIST:
            continue
        nums.append(n)
    ens: list[str] = []
    for tok in base.EN_TOKEN_RE.findall(text):
        low = tok.lower()
        if low in base.STOP_EN:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{12}", tok) or re.search(r"\d{8,}", tok):
            continue
        if low.startswith("rp_"):
            continue
        if len(re.findall(r"[A-Za-z]", tok)) < MIN_ENGLISH_LETTERS:
            continue
        ens.append(tok)
    return {"numbers": sorted(set(nums)), "english_tokens": sorted(set(ens))}


def check_hallucination_v2(answer: str, reference_text: str) -> list[dict]:
    """v2 幻觉候选：数字/英文专名未在引用文本出现；已做白名单与序数过滤。"""
    ref_norm = base.normalize(reference_text)
    ref_raw = str(reference_text or "").lower()
    flags: list[dict] = []
    ent = extract_entities_v2(answer)
    for num in ent["numbers"]:
        if base.normalize(num) not in ref_norm and num.lower() not in ref_raw:
            flags.append({"type": "number", "token": num,
                          "reason": "数字未在引用单元文本中出现（非白名单小整数）",
                          "rule": "v2.number_whitelist"})
    for tok in ent["english_tokens"]:
        if tok.lower() not in ref_raw:
            flags.append({"type": "english_token", "token": tok,
                          "reason": "英文专名/术语（≥3 字母）未在引用单元文本中出现",
                          "rule": "v2.english_ge3letters"})
    return flags


def _flag_key(f: dict) -> tuple:
    return (f.get("type"), str(f.get("token")))


# --------------------------------------------------------------------------- #
# 评估（复用 wave16 指标 + v2 幻觉规则 + schema 校验）
# --------------------------------------------------------------------------- #
def _reference_text(sample: dict, units: list[dict], id_map: dict) -> str:
    citations = sample.get("citations") or []
    resolved = [base.resolve_citation(c, units, id_map) for c in citations]
    return base.citation_texts(citations) + " " + " ".join(
        r["resolved_text"] for r in resolved if r["exists"])


def eval_sample_v2(sample: dict, units: list[dict], id_map: dict, kind: str = "main") -> dict:
    row = base.eval_sample(sample, units, id_map, kind=kind)
    answer = str(sample.get("answer") or "")
    ref_text = _reference_text(sample, units, id_map)
    flags_v1 = row.get("hallucination_flags") or []
    flags_v2 = check_hallucination_v2(answer, ref_text)
    v1_keys = {_flag_key(f) for f in flags_v1}
    v2_keys = {_flag_key(f) for f in flags_v2}
    removed = [f for f in flags_v1 if _flag_key(f) not in v2_keys]
    added = [f for f in flags_v2 if _flag_key(f) not in v1_keys]
    row["hallucination_flags_v1"] = flags_v1
    row["hallucination_flags"] = flags_v2
    row["false_positives_removed"] = removed
    row["new_flags_added"] = added
    # 更新 issues：替换幻觉相关条目
    issues = [i for i in row.get("issues") or [] if "幻觉启发式命中" not in i]
    if flags_v2:
        issues.append(f"幻觉候选 {len(flags_v2)} 项（v2）")
    row["issues"] = issues
    row["expected_refusal"] = sample.get("expected_refusal")
    row["citations_required"] = sample.get("citations_required")
    row["answer_type"] = sample.get("answer_type")
    row["schema_version"] = sample.get("schema_version")
    return row


def evaluate_v2(main_samples: list[dict], expanded_samples: list[dict],
                units: list[dict], id_map: dict[str, dict]) -> dict:
    seen: set[str] = set()
    rows: list[dict] = []
    included: list[dict] = []
    for s in main_samples:
        sid = str(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        included.append(s)
        rows.append(eval_sample_v2(s, units, id_map, kind="main"))
    for s in expanded_samples:
        sid = str(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        included.append(s)
        rows.append(eval_sample_v2(s, units, id_map, kind="expanded"))
    n = len(rows)
    # schema 校验只针对实际纳入评估（去重后）的样本，避免旧 schema 的重复源文件误报
    schema_errors = sch.validate_samples(included)
    total_cit = sum(r["n_citations"] for r in rows)
    exists_cit = sum(r["citations_exists"] for r in rows)
    graded = [r for r in rows if r["supported_ratio"] is not None]
    flags_v1_total = sum(len(r["hallucination_flags_v1"]) for r in rows)
    flags_v2_total = sum(len(r["hallucination_flags"]) for r in rows)
    samples_v1 = sum(1 for r in rows if r["hallucination_flags_v1"])
    samples_v2 = sum(1 for r in rows if r["hallucination_flags"])
    refusal_rows = [r for r in rows if r.get("expected_refusal") is True]
    required_rows = [r for r in rows if r.get("citations_required") is True]
    problems: list[str] = []
    for r in rows:
        for issue in r["issues"]:
            problems.append(f"{r['id']}（{r['kind']}）: {issue}")
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sources": {"main": str(DEFAULT_MAIN), "expanded": str(DEFAULT_EXPANDED),
                        "backup": str(sch.BACKUP)},
            "n_units": len(units),
            "sample_schema_version": sch.SCHEMA_VERSION,
            "hallucination_rules_v2": {
                "number_whitelist": sorted(NUMBER_WHITELIST),
                "list_marker_filtered": True,
                "ordinal_filtered": True,
                "min_english_letters": MIN_ENGLISH_LETTERS,
                "filters": "≥7 位长数字 / rp_ 研报号 / 12 位 hex 引用 id 均忽略",
            },
            "v1_baseline": "wave16 kb_qa_eval.check_hallucination（无白名单/序数过滤，英文≥1）",
            "no_llm": True,
        },
        "schema_validation": {"ok": not schema_errors, "errors": schema_errors,
                             "n_samples": len(seen),
                             "answer_type_counts": dict(collections.Counter(
                                 r["answer_type"] for r in rows)),
                             "expected_refusal": len(refusal_rows),
                             "citations_required": len(required_rows)},
        "summary": {
            "n_samples": n,
            "n_main": sum(1 for r in rows if r["kind"] == "main"),
            "n_expanded": sum(1 for r in rows if r["kind"] == "expanded"),
            "total_citations": total_cit,
            "citations_exists": exists_cit,
            "citation_exists_rate": round(exists_cit / total_cit, 4) if total_cit else None,
            "citation_valid_samples": sum(1 for r in rows if r["citation_valid"]),
            "avg_supported_ratio": (round(sum(r["supported_ratio"] for r in graded)
                                          / len(graded), 4) if graded else None),
            "hallucination_flags_v1_total": flags_v1_total,
            "hallucination_flags_v2_total": flags_v2_total,
            "hallucination_samples_v1": samples_v1,
            "hallucination_samples_v2": samples_v2,
            "false_positives_removed": flags_v1_total - flags_v2_total,
            "false_positive_reduction_rate": (round((flags_v1_total - flags_v2_total)
                                                    / flags_v1_total, 4)
                                              if flags_v1_total else None),
            "samples_refusal_expected": len(refusal_rows),
            "problems_count": len(problems),
        },
        "samples": rows,
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def write_md(rep: dict) -> str:
    s, sv = rep["summary"], rep["schema_validation"]
    L = ["# 问答质量评估 v2（schema v2 + 幻觉规则调优）", ""]
    L.append(f"- 生成：{rep['meta']['generated_at']}；样本 {s['n_samples']} 条；"
             f"知识库单元 {rep['meta']['n_units']}；样本 schema v{rep['meta']['sample_schema_version']}")
    L.append(f"- schema 校验：{'通过 ✅' if sv['ok'] else '失败 ❌'}；answer_type 分布 "
             f"{sv['answer_type_counts']}；expected_refusal {sv['expected_refusal']} 条、"
             f"citations_required {sv['citations_required']} 条")
    L.append(f"- 幻觉 v2 规则：{rep['meta']['hallucination_rules_v2']}")
    L.append("")
    L.append("## 汇总（v1 规则 vs v2 规则）")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 引用存在率 | {s['citations_exists']}/{s['total_citations']}"
             f"（{_pct(s['citation_exists_rate'])}） |")
    L.append(f"| 平均支撑率 | {_pct(s['avg_supported_ratio'])} |")
    L.append(f"| 幻觉候选（v1 wave16 规则） | {s['hallucination_samples_v1']} 样本 / "
             f"{s['hallucination_flags_v1_total']} 项 |")
    L.append(f"| **幻觉候选（v2 调优规则）** | **{s['hallucination_samples_v2']} 样本 / "
             f"{s['hallucination_flags_v2_total']} 项** |")
    L.append(f"| **消除误报** | **{s['false_positives_removed']} 项"
             f"（降幅 {_pct(s['false_positive_reduction_rate'])}）** |")
    L.append(f"| 预期拒答样本 | {s['samples_refusal_expected']}（不再因无引用判质量问题） |")
    L.append(f"| 问题数（v2） | {s['problems_count']} |")
    L.append("")
    L.append("## 逐样本前后对比")
    L.append("")
    L.append("| id | answer_type | 引用 | 支撑率 | v1 flag | v2 flag | 消除的误报 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rep["samples"]:
        removed = "、".join(f["token"] for f in r.get("false_positives_removed") or []) or "—"
        L.append(f"| {r['id']} | {r.get('answer_type')} | {r['citations_exists']}/"
                 f"{r['n_citations']} | {_pct(r['supported_ratio'])} | "
                 f"{len(r.get('hallucination_flags_v1') or [])} | "
                 f"{len(r.get('hallucination_flags') or [])} | {removed} |")
    L.append("")
    L.append("## 问题清单（v2）")
    L.append("")
    if rep["problems"]:
        for p in rep["problems"]:
            L.append(f"- {p}")
    else:
        L.append("- 无")
    L.append("")
    L.append("## 规则与局限")
    L.append("")
    for x in ("v2 数字白名单 1~10 + 列表序号/「第N」剔除，显著降低序数/编号误报；"
              "但真实小数字（如「3 条引用」）也可能被白名单漏检；",
              "英文 token 提升到 ≥3 字母后，「AI」等缩写不再检测，可能漏掉部分专名幻觉；",
              "支撑度仍为字符 bigram 覆盖率启发式（改写/同义会被低估），需人工复核；",
              "schema v2 字段由旧样本规则推断（迁移规则见 docs/plans/2026-11-01_问答样本schema_v2.md），"
              "拒答/推断边界需人工抽检确认；",
              "全部本地启发式、不调 LLM；样本量 17，不构成统计结论。"):
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_eval(args) -> int:
    units = base.load_units(args.kb)
    id_map = base.load_id_map(args.id_map)
    main = base.load_samples(args.main)
    expanded = base.load_samples(args.expanded) if args.expanded and Path(args.expanded).exists() else []
    rep = evaluate_v2(main, expanded, units, id_map)
    out_json, out_md = Path(args.json), Path(args.md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    print(json.dumps({**{k: rep["summary"][k] for k in
                         ("n_samples", "citation_exists_rate", "avg_supported_ratio",
                          "hallucination_samples_v1", "hallucination_samples_v2",
                          "hallucination_flags_v1_total", "hallucination_flags_v2_total",
                          "false_positives_removed")},
                      "schema_ok": rep["schema_validation"]["ok"]}, ensure_ascii=False))
    print(f"[kb_qa_eval_v2] -> {out_json} / {out_md}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="kb_qa_eval_v2",
                                description="问答质量评估 v2（schema v2 + 幻觉规则调优）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("eval", help="评估（v1 vs v2 幻觉规则对比）")
    pe.add_argument("--main", default=str(DEFAULT_MAIN))
    pe.add_argument("--expanded", default=str(DEFAULT_EXPANDED))
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
