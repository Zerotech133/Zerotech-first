# -*- coding: utf-8 -*-
"""研报/知识单元检索 v2：多字段加权 + 同义词扩展 + 短语/AND/OR + 分组 + 小评测。

背景
----
旧检索器 `tools/kb_corpus/kb_reports_search.py` 是单字段（concept/claim）关键词 +
中文 bigram 重叠的简单匹配（BM25-lite）。本文件是**新版检索器**（不修改旧脚本，
二者可并存对比）：

功能
----
1. 多字段加权：concept / claim / evidence / chapter / book 五个字段权重可配
   （默认 concept=1.0, claim=0.85, evidence=0.6, chapter=0.45, book=0.15）；
2. 同义词/别名扩展：内置 22 组常用词（tools/kb_corpus/synonyms_v2.json，可 --synonyms 自定义），
   查询词命中组内任一词即整组参与字段匹配；
3. 查询语法：
   - 空格 = AND（多词必须同时命中，按词覆盖率给分）
   - `|` 或 ` OR ` = OR（分支取最高分）
   - `"短语"` = 短语精确匹配（引号内整体匹配）
   - `-词` = 排除（该词出现在任一字段则该单元过滤）
4. 分组输出：按 (book, chapter) 聚合，给组相关度（组内最高分 + 0.3×其余分）；
5. 小评测：10 个查询 × precision@5 / 关键词覆盖率，v1（旧式 bigram 重叠）vs v2 对比，
   输出 research/reports/search_v2_eval_20261101.{json,md}；
6. CLI：`search --q ... --topk --explain`、`eval`。

数据
----
默认 portal/static/kb_units_all.json（498 单元，字段 concept/claim/evidence/chapter/book/p1/p2），
可用 --data 指定；也兼容 `C:/项目/PDF/_ingest_reports/*/knowledge_units.jsonl` 的目录结构
（--data 传目录时自动扫描 jsonl）。

用法
----
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/search_v2.py search --q "量柱 分类" --topk 5 --explain
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/search_v2.py search --q "太阳能|光伏" --topk 8
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/search_v2.py eval
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO / "portal" / "static" / "kb_units_all.json"
DEFAULT_SYNONYMS = Path(__file__).resolve().parent / "synonyms_v2.json"
OUT = REPO / "research" / "reports"

FIELDS = ("concept", "claim", "evidence", "chapter", "book")
DEFAULT_WEIGHTS: dict[str, float] = {
    "concept": 1.0, "claim": 0.85, "evidence": 0.6, "chapter": 0.45, "book": 0.15,
}
TOPK_DEFAULT = 10
PHRASE_BONUS = 0.10          # 查询短语（引号/整词）在 concept/claim 精确出现的加分

# 小评测：10 个查询（混合同义词/短语/AND/OR），keywords 为人工标注的相关知识关键词规则
EVAL_QUERIES: list[dict] = [
    {"q": "量柱分类", "keywords": ["量柱", "高低平倍梯缩金"], "note": "书籍-量学"},
    {"q": "量线 支撑", "keywords": ["量线", "斜衡线"], "note": "书籍-量学, AND"},
    {"q": "太阳能", "keywords": ["光伏", "太阳能"], "note": "同义词：太阳能→光伏"},
    {"q": "钠离子电池", "keywords": ["钠电池", "钠离子"], "note": "同义词：钠离子电池→钠电池"},
    {"q": "IDC", "keywords": ["数据中心", "IDC", "算力"], "note": "同义词：IDC→数据中心"},
    {"q": "算力", "keywords": ["数据中心", "算力", "AIDC", "AI"],
     "note": "同义词：算力→数据中心"},
    {"q": "大模型 应用", "keywords": ["大模型", "人工智能", "AI"], "note": "AND 多词"},
    {"q": "光伏|钠电池", "keywords": ["光伏", "钠电池", "钠离子", "储能"],
     "note": "OR 分支"},
    {"q": "房地产 楼市", "keywords": ["房地产", "地产", "楼市"], "note": "同义词+AND"},
    {"q": '"三维互动" 量学', "keywords": ["量学理论", "量柱", "量线", "量学"],
     "note": "短语+AND"},
]


# --------------------------------------------------------------------------- #
# 基础：分词 / 同义词 / 查询解析
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> list[str]:
    """中文 bigram（相邻两字）+ 英文/数字词（≥2 字符）。"""
    s = (text or "").lower()
    zh: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            continue
        zh.extend(run[i:i + 2] for i in range(len(run) - 1))
    en = re.findall(r"[a-z0-9]{2,}", s)
    return zh + en


class SynonymTable:
    """同义词/别名词表：term → 同组全部词（含自身）；未登录词返回 [term]。"""

    def __init__(self, groups: list[list[str]]):
        self.groups: list[list[str]] = []
        self.term_to_group: dict[str, list[str]] = {}
        for g in groups:
            g2 = [str(x).strip().lower() for x in g if str(x).strip()]
            if len(g2) < 2:
                continue
            self.groups.append(g2)
            for t in g2:
                self.term_to_group.setdefault(t, [])
                for x in g2:
                    if x not in self.term_to_group[t]:
                        self.term_to_group[t].append(x)

    def expand(self, term: str) -> list[str]:
        t = str(term).strip().lower()
        if not t:
            return []
        vals = self.term_to_group.get(t)
        if not vals:
            return [t]
        return list(dict.fromkeys([t] + list(vals)))

    def __len__(self) -> int:
        return len(self.groups)


def load_synonyms(path: Path | str | None = DEFAULT_SYNONYMS) -> SynonymTable:
    if path is None:
        return SynonymTable([])
    p = Path(path)
    if not p.exists():
        return SynonymTable([])
    data = json.loads(p.read_text(encoding="utf-8"))
    groups = data.get("groups") if isinstance(data, dict) else data
    return SynonymTable(groups or [])


def parse_query(q: str, synonyms: SynonymTable | None = None) -> dict:
    """解析查询：返回 {or_branches: [[{term, phrase, exclude, variants}...], ...], raw}。

    - `|` / ` OR ` / `或` 分隔 OR 分支；
    - 分支内空格分隔 = AND；`"..."` = 短语；`-词` = 排除词。
    """
    raw = str(q or "").strip()
    syn = synonyms or SynonymTable([])
    parts = re.split(r"\s*\|\s*|\s+OR\s+|\s+或\s+", raw, flags=re.IGNORECASE)
    branches: list[list[dict]] = []
    for part in parts:
        terms: list[dict] = []
        # 先切出带引号的短语：\"...\"
        for m in re.finditer(r'"([^"]+)"|(\S+)', part.strip()):
            piece = m.group(1) if m.group(1) is not None else m.group(2)
            if not piece:
                continue
            exclude = piece.startswith("-") and len(piece) > 1
            term = piece[1:] if exclude else piece
            term = term.strip()
            if not term:
                continue
            phrase = (m.group(1) is not None) or (" " in term)
            terms.append({
                "term": term,
                "phrase": bool(phrase or len(term) >= 2),
                "exclude": bool(exclude),
                "variants": syn.expand(term),
            })
        if terms:
            branches.append(terms)
    return {"raw": raw, "or_branches": branches}


# --------------------------------------------------------------------------- #
# 检索器
# --------------------------------------------------------------------------- #
class SearchV2:
    """多字段加权 + 同义词 + AND/OR + 分组 的检索器。"""

    def __init__(self, units: list[dict], synonyms: SynonymTable | None = None,
                 weights: dict[str, float] | None = None):
        self.units: list[dict] = []
        self.synonyms = synonyms or SynonymTable([])
        self.weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update({str(k): float(v) for k, v in weights.items()})
        self.total_field_weight = sum(self.weights.get(f, 0.0) for f in FIELDS) or 1.0
        for u in units:
            rec = {f: str(u.get(f, "") or "") for f in FIELDS}
            rec.update({
                "book": str(u.get("book", "") or ""),
                "chapter": str(u.get("chapter", "") or ""),
                "p1": u.get("p1") or u.get("page_start"),
                "p2": u.get("p2") or u.get("page_end"),
                "source": u.get("source", ""),
            })
            rec["_tokens"] = {f: set(tokenize(rec[f])) for f in FIELDS}
            rec["_text"] = {f: rec[f].lower() for f in FIELDS}
            self.units.append(rec)

    # -- 单字段/单词匹配 -------------------------------------------------- #
    @staticmethod
    def _term_field_score(term: str, variants: list[str], phrase: bool,
                          text_lower: str, tokens: set[str]) -> tuple[float, str | None]:
        """词在某字段的匹配分 0~1：短语包含=1.0，否则按 bigram 重叠率折算。"""
        for v in variants:
            if v and v in text_lower:
                return 1.0, v
        t_tokens = set(tokenize(term))
        if not t_tokens:
            return 0.0, None
        overlap = len(t_tokens & tokens) / float(len(t_tokens))
        if overlap <= 0:
            return 0.0, None
        # 多词/短语未精确命中时，重叠率打折（0~0.7）
        return 0.7 * overlap, None

    def _unit_score(self, unit: dict, terms: list[dict]) -> dict | None:
        """AND 分支打分：全部词必须命中（else None），返回 score + explain。"""
        field_scores = {f: 0.0 for f in FIELDS}
        matched: list[dict] = []
        phrase_hit = False
        covered = 0
        for t in terms:
            best = 0.0
            best_field = None
            best_var = None
            for f in FIELDS:
                w = self.weights.get(f, 0.0)
                if w <= 0:
                    continue
                s, var = self._term_field_score(
                    t["term"], t["variants"], t["phrase"],
                    unit["_text"][f], unit["_tokens"][f])
                if s > 0:
                    field_scores[f] += w * s
                    if s > best:
                        best, best_field, best_var = s, f, var
            # 排除词：任意字段命中即过滤
            if t["exclude"]:
                if best > 0:
                    return None
                continue
            if best <= 0:
                return None          # AND 语义：缺任一词 → 该分支不匹配
            covered += 1
            if best >= 1.0:
                if best_field in ("concept", "claim"):
                    phrase_hit = True
            matched.append({"term": t["term"], "field": best_field,
                            "score": round(best, 3), "matched_variant": best_var})
        if not matched:
            return None
        weight_sum = sum(self.weights.get(f, 0.0) for f in FIELDS) or 1.0
        base = sum(field_scores.values()) / (weight_sum * max(1, len(terms)))
        base = min(base, 1.0)
        coverage = covered / float(len(terms))
        score = base * (0.8 + 0.2 * coverage)
        if phrase_hit:
            score = min(1.0, score + PHRASE_BONUS * 0.5)
        return {"score": round(float(score), 4),
                "coverage": round(float(coverage), 3),
                "field_scores": {f: round(v, 4) for f, v in field_scores.items() if v > 0},
                "matched": matched}

    def search_units(self, query: str) -> list[dict]:
        """返回逐单元命中（OR 分支取最高分），含相关度与 explain。"""
        parsed = parse_query(query, self.synonyms)
        if not parsed["or_branches"]:
            return []
        results: list[dict] = []
        for unit in self.units:
            best: dict | None = None
            for terms in parsed["or_branches"]:
                hit = self._unit_score(unit, terms)
                if hit is not None and (best is None or hit["score"] > best["score"]):
                    best = hit
            if best is None:
                continue
            results.append({
                "book": unit["book"], "chapter": unit["chapter"],
                "p1": unit["p1"], "p2": unit["p2"], "source": unit["source"],
                "concept": unit["concept"], "claim": unit["claim"],
                "evidence": unit["evidence"],
                "score": best["score"],
                "explain": {"coverage": best["coverage"],
                            "field_scores": best["field_scores"],
                            "matched": best["matched"]},
            })
        results.sort(key=lambda r: (-r["score"], r["book"], r["chapter"]))
        return results

    def group_results(self, results: list[dict], topk: int = TOPK_DEFAULT) -> list[dict]:
        """按 (book, chapter) 聚合：组相关度 = 组内最高分 + 0.3×其余分之和。"""
        groups: dict[tuple[str, str], list[dict]] = {}
        for r in results:
            groups.setdefault((r["book"], r["chapter"]), []).append(r)
        out: list[dict] = []
        for (book, chapter), rows in groups.items():
            rows = sorted(rows, key=lambda r: -r["score"])
            best = rows[0]["score"]
            group_score = best + 0.3 * sum(r["score"] for r in rows[1:])
            out.append({
                "book": book,
                "chapter": chapter,
                "n_units": len(rows),
                "group_score": round(float(group_score), 4),
                "best_score": round(float(best), 4),
                "top_units": [{k: r[k] for k in
                               ("concept", "claim", "evidence", "score", "p1", "p2")}
                              for r in rows[:3]],
            })
        out.sort(key=lambda g: (-g["group_score"], g["book"], g["chapter"]))
        return out[:topk]

    def search(self, query: str, topk: int = TOPK_DEFAULT, group: bool = True,
               explain: bool = False) -> dict:
        results = self.search_units(query)
        parsed = parse_query(query, self.synonyms)
        payload: dict = {
            "query": query,
            "n": len(results),
            "weights": {f: self.weights.get(f, 0.0) for f in FIELDS},
            "parsed": {
                "or_branches": [
                    [{"term": t["term"], "variants": t["variants"],
                      "phrase": t["phrase"], "exclude": t["exclude"]}
                     for t in branch]
                    for branch in parsed["or_branches"]
                ],
            },
            "matches": [],
        }
        if group:
            payload["groups"] = self.group_results(results, topk=topk)
        for r in results[:topk]:
            item = {"book": r["book"], "chapter": r["chapter"],
                    "p1": r["p1"], "p2": r["p2"],
                    "concept": r["concept"], "claim": r["claim"],
                    "evidence": r["evidence"], "score": r["score"]}
            if explain:
                item["explain"] = r["explain"]
            payload["matches"].append(item)
        return payload


# --------------------------------------------------------------------------- #
# 小评测：v1（旧式 bigram 重叠）vs v2（新版）
# --------------------------------------------------------------------------- #
def legacy_search_units(units: list[dict], query: str, topk: int = 5) -> list[dict]:
    """模拟旧检索器：concept×2 + claim×1 的中文 bigram 重叠计数排序。"""
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return []
    scored = []
    for u in units:
        c_tokens = set(tokenize(u.get("concept", "")))
        cl_tokens = set(tokenize(u.get("claim", "")))
        s = 2.0 * len(q_tokens & c_tokens) + 1.0 * len(q_tokens & cl_tokens)
        if s > 0:
            scored.append((s, u))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:topk]]


def _hit_keywords(unit: dict, keywords: list[str]) -> set[str]:
    text = " ".join(str(unit.get(k, "") or "") for k in
                    ("concept", "claim", "evidence", "chapter", "book"))
    return {kw for kw in keywords if kw and kw in text}


def evaluate(engine: SearchV2, queries: list[dict] | None = None,
             topk: int = 5) -> dict:
    """10 查询小基准：precision@k（相关单元占比）与关键词覆盖率（v1 vs v2）。"""
    qs = queries if queries is not None else EVAL_QUERIES
    rows: list[dict] = []
    for item in qs:
        q, kws = item["q"], list(item["keywords"])
        v2 = engine.search_units(q)[:topk]
        v1 = legacy_search_units(engine.units, q, topk=topk)
        p_v2 = float(np_mean([1.0 if _hit_keywords(u, kws) else 0.0 for u in v2])) if v2 else 0.0
        p_v1 = float(np_mean([1.0 if _hit_keywords(u, kws) else 0.0 for u in v1])) if v1 else 0.0
        cov_v2 = _coverage(v2, kws)
        cov_v1 = _coverage(v1, kws)
        rows.append({
            "query": q, "note": item.get("note", ""),
            "keywords": kws,
            "v1": {"n": len(v1), "precision_at_k": round(p_v1, 4),
                   "coverage": round(cov_v1, 4),
                   "top_concepts": [u.get("concept", "") for u in v1]},
            "v2": {"n": len(v2), "precision_at_k": round(p_v2, 4),
                   "coverage": round(cov_v2, 4),
                   "top_concepts": [u.get("concept", "") for u in v2]},
        })
    k = topk
    agg_v1 = {
        "precision_at_k": round(float(np_mean([r["v1"]["precision_at_k"] for r in rows])), 4),
        "coverage": round(float(np_mean([r["v1"]["coverage"] for r in rows])), 4),
    }
    agg_v2 = {
        "precision_at_k": round(float(np_mean([r["v2"]["precision_at_k"] for r in rows])), 4),
        "coverage": round(float(np_mean([r["v2"]["coverage"] for r in rows])), 4),
    }
    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "topk": k,
            "n_queries": len(rows),
            "n_units": len(engine.units),
            "n_synonym_groups": len(engine.synonyms),
            "weights": {f: engine.weights.get(f, 0.0) for f in FIELDS},
            "schema_version": "search_v2_eval.v1",
        },
        "aggregate": {"v1": agg_v1, "v2": agg_v2,
                      "delta_precision": round(agg_v2["precision_at_k"] - agg_v1["precision_at_k"], 4),
                      "delta_coverage": round(agg_v2["coverage"] - agg_v1["coverage"], 4)},
        "queries": rows,
    }


def _coverage(units: list[dict], keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    found: set[str] = set()
    for u in units:
        found |= _hit_keywords(u, keywords)
    return len(found) / float(len(keywords))


def np_mean(vals) -> float:
    vals = list(vals)
    return float(sum(vals) / len(vals)) if vals else 0.0


def write_eval_md(rep: dict, path: Path) -> str:
    agg = rep["aggregate"]
    L = ["# 研报检索 v2 小评测（precision@5 / 覆盖率）", ""]
    L.append(f"- 生成：{rep['meta']['generated_at']}；数据 {rep['meta']['n_units']} 单元；"
             f"同义词组 {rep['meta']['n_synonym_groups']}；查询 {rep['meta']['n_queries']} 个；"
             f"topk={rep['meta']['topk']}")
    L.append(f"- 字段权重：{rep['meta']['weights']}")
    L.append("")
    L.append("## 汇总（v1 旧式 bigram 重叠 vs v2 加权+同义词+AND/OR）")
    L.append("")
    L.append("| 版本 | precision@5 | 关键词覆盖率 |")
    L.append("|---|---|---|")
    L.append(f"| v1（旧检索器） | {agg['v1']['precision_at_k']:.3f} | {agg['v1']['coverage']:.3f} |")
    L.append(f"| **v2（新版）** | **{agg['v2']['precision_at_k']:.3f}** | **{agg['v2']['coverage']:.3f}** |")
    L.append(f"| 提升 | {agg['delta_precision']:+.3f} | {agg['delta_coverage']:+.3f} |")
    L.append("")
    L.append("## 逐查询")
    L.append("")
    L.append("| # | 查询 | 类型 | v1 P@5 | v1 覆盖 | v2 P@5 | v2 覆盖 |")
    L.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rep["queries"], 1):
        L.append(f"| {i} | `{r['query']}` | {r['note']} | {r['v1']['precision_at_k']:.2f} | "
                 f"{r['v1']['coverage']:.2f} | {r['v2']['precision_at_k']:.2f} | "
                 f"{r['v2']['coverage']:.2f} |")
    L.append("")
    L.append("## 口径")
    L.append("")
    L.append("- precision@5：top5 结果中包含任一人工标注关键词（concept/claim/evidence/chapter/book）"
             "的单元占比；覆盖率：人工标注关键词集合在 top5 结果中被命中的比例。")
    L.append("- v1 基线为旧检索器逻辑的等价复刻（concept×2 + claim×1 的中文 bigram 重叠计数），"
             "v2 为本文件（多字段加权 + 同义词扩展 + AND/OR + 短语）。")
    L.append("- 评测查询与关键词为人工标注（10 条，覆盖书籍/研报、同义词、AND、OR、短语场景），"
             "属小样本冒烟级质量对比，不构成统计显著性结论。")
    txt = "\n".join(L) + "\n"
    Path(path).write_text(txt, encoding="utf-8")
    return txt


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_units(path: Path | str) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        units: list[dict] = []
        for ku in sorted(p.glob("*/knowledge_units.jsonl")):
            for line in ku.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    u = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                units.append({"book": u.get("book") or ku.parent.name,
                              "chapter": u.get("chapter", ""),
                              "p1": u.get("page_start"), "p2": u.get("page_end"),
                              "concept": u.get("concept", ""), "claim": u.get("claim", ""),
                              "evidence": u.get("evidence", ""), "source": "units"})
        return units
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("units") or [])
    return list(data)


def build_engine(args) -> SearchV2:
    units = load_units(args.data)
    synonyms = load_synonyms(args.synonyms)
    weights = None
    if getattr(args, "weights_json", None):
        weights = json.loads(Path(args.weights_json).read_text(encoding="utf-8"))
    return SearchV2(units, synonyms=synonyms, weights=weights)


def cmd_search(args) -> int:
    engine = build_engine(args)
    payload = engine.search(args.q, topk=args.topk, group=not args.no_group,
                            explain=args.explain)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


def cmd_eval(args) -> int:
    engine = build_engine(args)
    rep = evaluate(engine, EVAL_QUERIES, topk=args.topk)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    write_eval_md(rep, out_md)
    agg = rep["aggregate"]
    print(json.dumps({"n_units": rep["meta"]["n_units"],
                      "v1": agg["v1"], "v2": agg["v2"],
                      "delta_precision": agg["delta_precision"],
                      "delta_coverage": agg["delta_coverage"]}, ensure_ascii=False))
    print(f"[search_v2] eval -> {out_json} / {out_md}", flush=True)
    return 0


def cmd_synonyms(args) -> int:
    syn = load_synonyms(args.synonyms)
    print(json.dumps({"n_groups": len(syn),
                      "groups": syn.groups,
                      "expand_demo": {t: syn.expand(t)[:4]
                                      for t in ("光伏", "IDC", "量柱")}},
                     ensure_ascii=False, indent=1))
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="search_v2", description="研报/知识单元检索 v2")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--synonyms", default=str(DEFAULT_SYNONYMS))
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("search", help="检索")
    ps.add_argument("--q", required=True)
    ps.add_argument("--topk", type=int, default=TOPK_DEFAULT)
    ps.add_argument("--explain", action="store_true")
    ps.add_argument("--no-group", action="store_true")
    ps.add_argument("--weights-json", default=None)
    ps.set_defaults(func=cmd_search)

    pe = sub.add_parser("eval", help="小评测（10 查询）")
    pe.add_argument("--topk", type=int, default=5)
    pe.add_argument("--out-json", default=str(OUT / "search_v2_eval_20261101.json"))
    pe.add_argument("--out-md", default=str(OUT / "search_v2_eval_20261101.md"))
    pe.add_argument("--weights-json", default=None)
    pe.set_defaults(func=cmd_eval)

    py_ = sub.add_parser("synonyms", help="查看同义词表")
    py_.set_defaults(func=cmd_synonyms)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
