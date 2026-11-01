# -*- coding: utf-8 -*-
"""问答答案质量评估（wave16 支线 AU）：引用校验 + 支撑度分级 + 幻觉启发式（不调 LLM）。

评估对象
--------
- `portal/static/kb_qa_samples.json`（17 条问答样本，wave13 AM）
- `research/reports/kb_qa_samples_expanded_20261101.json`（12 条扩展样本）
知识库：`portal/static/kb_units_all.json`（498 个知识单元）；
单元 id 映射：`research/reports/kb_qa_questions_20261101.json` 的 `source_unit_id`
（kb_units_all 本身不含 id 字段，故用 id→(book/chapter/pages/concept) 映射 + 引用文本兜底解析）。

三项检查（全部本地启发式、不调 LLM）
--------------------------------
1. **引用校验**：每条 citation 的 unit_id 能否解析到真实知识单元（id 映射 / 引用文本匹配）、
   page 是否落在单元页码区间内 → 每样本 `citation_exists_ratio` / `citation_valid`；
2. **支撑度**：答案按句切分，句 vs 引用单元 claim+evidence 的字符 bigram 覆盖率
   → 支持（≥0.5）/ 弱支持（≥0.2）/ 无支持（<0.2）；拒答句（"没有足够依据"等）单列不计入分母
   → 每样本 `supported_ratio`；
3. **幻觉启发式**：答案中的数字、英文专名（如 CoWoP/HVLP4、600 美元、224Gbps）是否在引用单元文本出现，
   未出现 → `hallucination_flags`（启发式，可能误报，需人工复核）。

输出：research/reports/kb_qa_eval_20261101.json / .md（逐样本 + 汇总 + 问题清单）
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/kb_qa_eval.py eval
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
OUT = ROOT / "research" / "reports"
DEFAULT_MAIN = ROOT / "portal" / "static" / "kb_qa_samples.json"
DEFAULT_EXPANDED = OUT / "kb_qa_samples_expanded_20261101.json"
DEFAULT_KB = ROOT / "portal" / "static" / "kb_units_all.json"
DEFAULT_IDMAP = OUT / "kb_qa_questions_20261101.json"
DEFAULT_JSON = OUT / "kb_qa_eval_20261101.json"
DEFAULT_MD = OUT / "kb_qa_eval_20261101.md"
SCHEMA_VERSION = "kb_qa_eval.v1"

SUPPORT_STRONG = 0.5
SUPPORT_WEAK = 0.2
RESOLVE_MIN_OVERLAP = 0.35      # 引用文本→知识单元匹配阈值
REFUSAL_MARKERS = ("没有足够依据", "无法回答", "不足以回答", "未包含", "没有找到",
                   "知识库中没有", "无法从", "拒答")
CITATION_MARK_RE = re.compile(r"\[[0-9a-fA-F]{6,}\]|p\.\s?\d+(?:-\d+)?")
NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-+/]{2,}")
STOP_EN = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "not",
           "pdf", "http", "https", "p", "pp", "gb", "mm"}


# --------------------------------------------------------------------------- #
# 纯函数（可单测）
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """去空白/标点/引用标记后的文本（用于字符重叠与包含判断）。"""
    s = CITATION_MARK_RE.sub("", str(text or ""))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[，。；：、！？,.;:!?\"'“”‘’（）()《》<>\[\]【】\-—_/|%·…]+", "", s)
    return s.lower()


def char_ngrams(text: str, n: int = 2) -> set[str]:
    s = normalize(text)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def coverage_ratio(sentence: str, references: str) -> float:
    """句子字符 bigram 被引用文本覆盖的比例（0~1）。"""
    a = char_ngrams(sentence)
    if not a:
        return 0.0
    b = char_ngrams(references)
    return len(a & b) / float(len(a))


def split_sentences(answer: str) -> list[str]:
    """按句切分并去掉纯引用标记/空行。"""
    raw = str(answer or "")
    pieces = re.split(r"(?<=[。！？!?；;])\s*|\n+", raw)
    out: list[str] = []
    for p in pieces:
        s = CITATION_MARK_RE.sub("", p).strip()
        s = s.strip("*-— 　")
        if not s or len(s) < 4:
            continue
        out.append(s)
    return out


def is_refusal(sentence: str) -> bool:
    return any(m in sentence for m in REFUSAL_MARKERS)


def grade_support(score: float) -> str:
    if score >= SUPPORT_STRONG:
        return "支持"
    if score >= SUPPORT_WEAK:
        return "弱支持"
    return "无支持"


def citation_texts(citations: list[dict]) -> str:
    return " ".join(str(c.get("text") or "") for c in citations or [])


def extract_entities(answer: str) -> dict:
    """提取答案中的数字与英文专名（幻觉启发式的候选 token）。

    过滤明显非实体 token：≥7 位长数字（文件名/日期串）、含 8 位以上数字的英文串、`rp_` 研报号。
    """
    text = CITATION_MARK_RE.sub("", str(answer or ""))
    nums = sorted({n for n in NUM_RE.findall(text) if len(n) <= 6})
    ens = sorted({t for t in EN_TOKEN_RE.findall(text)
                  if t.lower() not in STOP_EN
                  and not re.fullmatch(r"[0-9a-fA-F]{12}", t)
                  and not re.search(r"\d{8,}", t)
                  and not t.lower().startswith("rp_")})
    return {"numbers": nums, "english_tokens": ens}


def check_hallucination(answer: str, reference_text: str) -> list[dict]:
    """数字/英文专名未在引用文本出现 → 标记（启发式）。"""
    ref_norm = normalize(reference_text)
    ref_raw = str(reference_text or "").lower()
    flags: list[dict] = []
    ent = extract_entities(answer)
    for num in ent["numbers"]:
        if normalize(num) not in ref_norm and str(num).lower() not in ref_raw:
            flags.append({"type": "number", "token": num,
                          "reason": "数字未在引用单元文本中出现"})
    for tok in ent["english_tokens"]:
        if tok.lower() not in ref_raw:
            flags.append({"type": "english_token", "token": tok,
                          "reason": "英文专名/术语未在引用单元文本中出现"})
    return flags


# --------------------------------------------------------------------------- #
# 引用解析（unit_id → 知识单元）
# --------------------------------------------------------------------------- #
def load_units(path: Path | str = DEFAULT_KB) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("units") or []) if isinstance(data, dict) else list(data)


def load_id_map(path: Path | str = DEFAULT_IDMAP) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {q["source_unit_id"]: q for q in data.get("questions") or []
            if q.get("source_unit_id")}


def _unit_text(u: dict) -> str:
    return " ".join(str(u.get(k, "") or "") for k in ("concept", "claim", "evidence"))


def match_unit_by_meta(units: list[dict], book: str, chapter: str = "",
                       p1=None, p2=None, concept: str = "") -> int | None:
    """优先按 book+chapter+页码+concept 精确/前缀匹配知识单元，返回索引。"""
    cand = [(i, u) for i, u in enumerate(units) if str(u.get("book", "")) == str(book or "")]
    if not cand:
        return None
    if concept:
        exact = [i for i, u in cand if str(u.get("concept", "")).strip() == str(concept).strip()]
        if exact:
            return exact[0]
    if p1 is not None:
        page_hit = [i for i, u in cand
                    if int(u.get("p1") or -1) == int(p1)
                    or int(u.get("p2") or -1) == int(p2 or -1)]
        if page_hit:
            return page_hit[0]
    if chapter:
        ch = [i for i, u in cand if str(u.get("chapter", "")).strip() == str(chapter).strip()]
        if ch:
            return ch[0]
    return cand[0][0]


def match_unit_by_text(units: list[dict], text: str) -> tuple[int | None, float]:
    """引用文本 → 知识单元（字符 bigram 覆盖率最高者，≥阈值才算命中）。"""
    if not text:
        return None, 0.0
    best_i, best = None, 0.0
    for i, u in enumerate(units):
        r = coverage_ratio(text, _unit_text(u))
        if r > best:
            best_i, best = i, r
    if best_i is not None and best >= RESOLVE_MIN_OVERLAP:
        return best_i, round(best, 3)
    return None, round(best, 3)


def resolve_citation(cit: dict, units: list[dict],
                     id_map: dict[str, dict]) -> dict:
    """解析单条引用：unit_id 是否可映射到知识库 + 页码是否合法。"""
    uid = str(cit.get("unit_id") or "")
    book = str(cit.get("book") or "")
    page = cit.get("page")
    page_end = cit.get("page_end")
    method = None
    unit_idx = None
    id_known = uid in id_map
    if id_known:
        meta = id_map[uid]
        unit_idx = match_unit_by_meta(units, book=meta.get("book") or book,
                                      chapter=meta.get("chapter", ""),
                                      p1=meta.get("page_start"), p2=meta.get("page_end"),
                                      concept=meta.get("concept", ""))
        method = "id_map" if unit_idx is not None else None
    if unit_idx is None and cit.get("text"):
        unit_idx, score = match_unit_by_text(units, str(cit.get("text")))
        method = f"text_match({score})" if unit_idx is not None else "text_unmatched"
    exists = unit_idx is not None
    page_ok = None
    if exists:
        u = units[unit_idx]
        p1, p2 = int(u.get("p1") or 0), int(u.get("p2") or 0)
        try:
            page_i = int(page)
        except (TypeError, ValueError):
            page_i = None
        page_ok = bool(page_i is not None and p1 <= page_i <= max(p2, p1))
        if page_end is not None:
            try:
                page_ok = page_ok and int(page) <= int(page_end) and int(page_end) <= max(p2, p1) + 1
            except (TypeError, ValueError):
                page_ok = False
    return {"unit_id": uid, "book": book, "page": page, "page_end": page_end,
            "exists": exists, "unit_index": unit_idx, "method": method,
            "id_known_in_map": id_known, "page_ok": page_ok,
            "resolved_text": _unit_text(units[unit_idx]) if exists else ""}


# --------------------------------------------------------------------------- #
# 单样本 / 全量评估
# --------------------------------------------------------------------------- #
def eval_sample(sample: dict, units: list[dict], id_map: dict[str, dict],
                kind: str = "main") -> dict:
    answer = str(sample.get("answer") or "")
    citations = sample.get("citations") or []
    resolved = [resolve_citation(c, units, id_map) for c in citations]
    n_cit = len(resolved)
    exists_n = sum(1 for r in resolved if r["exists"])
    page_ok_n = sum(1 for r in resolved if r["exists"] and r["page_ok"])
    citation_valid = bool(n_cit > 0 and exists_n == n_cit and page_ok_n == n_cit)
    # 引用文本（解析失败时用 citation 自带 text 兜底）
    ref_text = citation_texts(citations) + " " + " ".join(
        r["resolved_text"] for r in resolved if r["exists"])
    # 句子支撑度（拒答句单列）
    sentences = split_sentences(answer)
    support_counts = collections.Counter()
    sentence_details = []
    for s in sentences:
        if is_refusal(s):
            support_counts["拒答"] += 1
            sentence_details.append({"sentence": s[:80], "support": "拒答", "score": None})
            continue
        score = coverage_ratio(s, ref_text)
        lvl = grade_support(score)
        support_counts[lvl] += 1
        sentence_details.append({"sentence": s[:80], "support": lvl,
                                 "score": round(score, 3)})
    graded = support_counts["支持"] + support_counts["弱支持"] + support_counts["无支持"]
    supported_ratio = (round(support_counts["支持"] / graded, 4) if graded else None)
    weak_plus = (round((support_counts["支持"] + support_counts["弱支持"]) / graded, 4)
                 if graded else None)
    # 幻觉启发式
    flags = check_hallucination(answer, ref_text)
    issues: list[str] = []
    if not n_cit:
        issues.append("无引用")
    if n_cit and exists_n < n_cit:
        issues.append(f"引用无法解析到知识库: {n_cit - exists_n}/{n_cit}")
    if n_cit and page_ok_n < exists_n:
        issues.append(f"页码超出单元区间: {exists_n - page_ok_n}/{exists_n}")
    if graded and support_counts["无支持"]:
        issues.append(f"无支持句 {support_counts['无支持']}/{graded}")
    if flags:
        issues.append(f"幻觉启发式命中 {len(flags)} 项")
    return {
        "id": sample.get("id"),
        "kind": kind,
        "question": sample.get("question") or sample.get("q"),
        "category": sample.get("category") or sample.get("theme") or sample.get("type"),
        "n_citations": n_cit,
        "citations_exists": exists_n,
        "citations_page_ok": page_ok_n,
        "citation_exists_ratio": round(exists_n / n_cit, 4) if n_cit else 0.0,
        "citation_valid": citation_valid,
        "n_sentences": len(sentences),
        "support_counts": dict(support_counts),
        "supported_ratio": supported_ratio,
        "supported_or_weak_ratio": weak_plus,
        "hallucination_flags": flags,
        "citations": [{k: r[k] for k in ("unit_id", "exists", "method", "page_ok",
                                         "id_known_in_map")} for r in resolved],
        "sentence_details": sentence_details,
        "issues": issues,
    }


def evaluate(main_samples: list[dict], expanded_samples: list[dict],
             units: list[dict], id_map: dict[str, dict]) -> dict:
    # 去重：同一 id 若已在 main（静态样本可能已并入 expanded 样本）则不再重复评估
    seen: set[str] = set()
    rows: list[dict] = []
    for s in main_samples:
        sid = str(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        rows.append(eval_sample(s, units, id_map, kind="main"))
    for s in expanded_samples:
        sid = str(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        rows.append(eval_sample(s, units, id_map, kind="expanded"))
    n = len(rows)
    n_valid = sum(1 for r in rows if r["citation_valid"])
    total_cit = sum(r["n_citations"] for r in rows)
    exists_cit = sum(r["citations_exists"] for r in rows)
    graded_rows = [r for r in rows if r["supported_ratio"] is not None]
    avg_support = (round(sum(r["supported_ratio"] for r in graded_rows) / len(graded_rows), 4)
                   if graded_rows else None)
    flag_rows = [r for r in rows if r["hallucination_flags"]]
    refusal_rows = [r for r in rows if (r["support_counts"].get("拒答", 0) > 0
                                        and r["support_counts"].get("支持", 0) == 0)]
    problems: list[str] = []
    for r in rows:
        for issue in r["issues"]:
            problems.append(f"{r['id']}（{r['kind']}）: {issue}")
    summary = {
        "n_samples": n,
        "n_main": sum(1 for r in rows if r["kind"] == "main"),
        "n_expanded": sum(1 for r in rows if r["kind"] == "expanded"),
        "n_expanded_source": len(expanded_samples),
        "n_dedup_skipped": max(0, len(main_samples) + len(expanded_samples) - n),
        "citation_valid_samples": n_valid,
        "citation_valid_rate": round(n_valid / n, 4) if n else None,
        "total_citations": total_cit,
        "citations_exists": exists_cit,
        "citation_exists_rate": round(exists_cit / total_cit, 4) if total_cit else None,
        "avg_supported_ratio": avg_support,
        "samples_with_hallucination_flags": len(flag_rows),
        "samples_refusal_only": len(refusal_rows),
        "problems_count": len(problems),
        "support_level_totals": dict(collections.Counter(
            f"{k}" for r in rows for k, v in r["support_counts"].items() for _ in range(v))),
        "hallucination_flag_totals": dict(collections.Counter(
            f["type"] for r in rows for f in r["hallucination_flags"])),
    }
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sources": {"main": str(DEFAULT_MAIN), "expanded": str(DEFAULT_EXPANDED),
                        "kb": str(DEFAULT_KB), "id_map": str(DEFAULT_IDMAP)},
            "n_units": len(units),
            "thresholds": {"support_strong": SUPPORT_STRONG, "support_weak": SUPPORT_WEAK,
                           "resolve_min_overlap": RESOLVE_MIN_OVERLAP},
            "heuristics": "字符 bigram 覆盖率支撑度 + 数字/英文专名未在引用出现=幻觉候选；"
                          "全部本地启发式、不调 LLM，可能误报/漏报，需人工复核",
            "note": "kb_units_all.json 无 id 字段：unit_id 先经 kb_qa_questions 的 id 映射解析到"
                    "book/chapter/页码/concept，未命中再用 citation.text 与单元 claim+evidence 文本匹配",
        },
        "summary": summary,
        "samples": rows,
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def write_md(rep: dict) -> str:
    s = rep["summary"]
    L = ["# 问答答案质量评估（引用校验 + 支撑度 + 幻觉启发式）", ""]
    L.append(f"- 生成：{rep['meta']['generated_at']}；知识库单元 {rep['meta']['n_units']}；"
             f"样本 {s['n_samples']}（main {s['n_main']} + expanded {s['n_expanded']}）")
    L.append(f"- 阈值：{rep['meta']['thresholds']}；{rep['meta']['heuristics']}")
    L.append(f"- 口径：{rep['meta']['note']}")
    L.append("")
    L.append("## 汇总")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 引用全部有效的样本 | {s['citation_valid_samples']}/{s['n_samples']}"
             f"（{_pct(s['citation_valid_rate'])}） |")
    L.append(f"| 引用存在率（逐条） | {s['citations_exists']}/{s['total_citations']}"
             f"（{_pct(s['citation_exists_rate'])}） |")
    L.append(f"| 平均答案支撑率（支持句/可评句） | {_pct(s['avg_supported_ratio'])} |")
    L.append(f"| 含幻觉候选的样本 | {s['samples_with_hallucination_flags']} |")
    L.append(f"| 纯拒答样本 | {s['samples_refusal_only']} |")
    L.append(f"| 支撑度分布 | {s['support_level_totals']} |")
    L.append(f"| 幻觉候选类型 | {s['hallucination_flag_totals']} |")
    L.append("")
    L.append("## 逐样本")
    L.append("")
    L.append("| id | 类型 | 引用 | 引用存在 | 引用有效 | 句数 | 支持/弱/无/拒答 | 支撑率 | 幻觉候选 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rep["samples"]:
        sc = r["support_counts"]
        L.append(f"| {r['id']} | {r['kind']} | {r['n_citations']} | "
                 f"{r['citations_exists']}/{r['n_citations']} | "
                 f"{'✅' if r['citation_valid'] else '❌'} | {r['n_sentences']} | "
                 f"{sc.get('支持', 0)}/{sc.get('弱支持', 0)}/{sc.get('无支持', 0)}/"
                 f"{sc.get('拒答', 0)} | {_pct(r['supported_ratio'])} | "
                 f"{len(r['hallucination_flags'])} |")
    L.append("")
    L.append("## 问题清单")
    L.append("")
    if rep["problems"]:
        for p in rep["problems"]:
            L.append(f"- {p}")
    else:
        L.append("- 无")
    L.append("")
    L.append("## 局限")
    L.append("")
    for x in ("支撑度为字符 bigram 覆盖率启发式：改写/同义表述会被低估，需人工复核；",
              "数字/英文专名幻觉检测只覆盖显式 token：中文实体、时间/逻辑错误检测不到；",
              "unit_id 非知识库原生字段，通过 id 映射 + 引用文本匹配解析，少量引用可能无法定位；",
              "样本量小（main 17 + expanded 12），不构成统计性结论；未调用 LLM，属规则级质检。"):
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_samples(path: Path | str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("samples") or []) if isinstance(data, dict) else list(data)


def cmd_eval(args) -> int:
    units = load_units(args.kb)
    id_map = load_id_map(args.id_map)
    main = load_samples(args.main)
    expanded = load_samples(args.expanded) if args.expanded and Path(args.expanded).exists() else []
    rep = evaluate(main, expanded, units, id_map)
    out_json, out_md = Path(args.json), Path(args.md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(rep), encoding="utf-8")
    print(json.dumps({k: rep["summary"][k] for k in
                      ("n_samples", "citation_valid_rate", "citation_exists_rate",
                       "avg_supported_ratio", "samples_with_hallucination_flags",
                       "problems_count")}, ensure_ascii=False))
    print(f"[kb_qa_eval] -> {out_json} / {out_md}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="kb_qa_eval",
                                description="问答答案质量评估（引用/支撑度/幻觉启发式）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("eval", help="评估问答样本")
    pe.add_argument("--main", default=str(DEFAULT_MAIN))
    pe.add_argument("--expanded", default=str(DEFAULT_EXPANDED))
    pe.add_argument("--kb", default=str(DEFAULT_KB))
    pe.add_argument("--id-map", default=str(DEFAULT_IDMAP))
    pe.add_argument("--json", default=str(DEFAULT_JSON))
    pe.add_argument("--md", default=str(DEFAULT_MD))
    pe.set_defaults(func=cmd_eval)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
