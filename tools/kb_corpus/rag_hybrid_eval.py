# -*- coding: utf-8 -*-
"""W10 RAG 混合检索评测（task-168）：BM25 + bge-m3 向量 + RRF 融合（k=60）。

复用 W6（rag_baseline_eval.py）的 load_docs / embed_texts / 指标函数；评测集沿用 W6
rag_baseline_20261104.json 的 **50 题/gold**（不重新生成问题）。

流程：
  1) 纯 Python BM25（k1=1.5, b=0.75；CJK bigram + 拉丁词）→ top100；
  2) bge-m3 向量（复用 W6 embed）→ 余弦 top100；
  3) RRF 融合：score = Σ w_i/(k+rank_i)，k=60（权重可配）→ top5/10；
  4) 若融合 recall@5 < 0.85：尝试 (a) concept_synonyms 同义词扩展、(b) BM25 PRF 扩展，
     取最小有效改动（按 recall@5、MRR 选优）；
  5) 产出 research/reports/rag_hybrid_20261104.{json,md}（对比表/结论/阈值）。

只读知识库、本地 ollama、不写生产库；不 commit。
CLI::
    python tools/kb_corpus/rag_hybrid_eval.py build --write [--k 60]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
BASELINE = R / "rag_baseline_20261104.json"
SYNONYMS = R / "concept_synonyms_20261103.json"
OUT_JSON = R / "rag_hybrid_20261104.json"
OUT_MD = R / "rag_hybrid_20261104.md"
W6 = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"
DATE = "20261104"
CJK = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}")
RRF_K = 60
TOP_N = 100
TARGET = 0.85


def _load_w6():
    spec = importlib.util.spec_from_file_location("rag_w6", W6)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rag_w6"] = mod
    spec.loader.exec_module(mod)
    return mod


W6MOD = _load_w6()


def load_questions(path: Path = BASELINE) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return [{"qid": r["qid"], "question": r["question"], "type": r["type"],
             "gold_doc_ids": r["gold_doc_ids"], "gold_text": r.get("gold_text", "")}
            for r in d.get("results") or []]


def tokenize(text: str) -> list[str]:
    """CJK bigram + 拉丁词（小写）；纯规则，无新依赖。"""
    toks = []
    for run in CJK.findall(str(text or "")):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    toks.extend(w.lower() for w in LATIN.findall(str(text or "")))
    return toks


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_tokens = [tokenize(t) for t in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.tf: list[Counter] = [Counter(t) for t in self.doc_tokens]
        self.df: Counter = Counter()
        for tf in self.tf:
            self.df.update(tf.keys())
        self.N = len(docs)

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, query: str | list[str], topn: int = TOP_N) -> list[int]:
        q_terms = tokenize(query) if isinstance(query, str) else list(query)
        scores = [0.0] * self.N
        for term in set(q_terms):
            idf = self.idf(term)
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = self.doc_len[i] or 1
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / max(1e-9, self.avgdl)))
        ranked = sorted(range(self.N), key=lambda i: -scores[i])
        return [i for i in ranked[:topn] if scores[i] > 0]


def rrf_fuse(rank_lists: list[list[int]], weights: list[float] | None = None,
             k: int = RRF_K, topn: int = 10) -> list[int]:
    """RRF：score = Σ w_i/(k+rank_i)，rank 从 1 开始。"""
    n = len(rank_lists)
    weights = weights or [1.0] * n
    score: dict[int, float] = {}
    for lst, w in zip(rank_lists, weights):
        for rank, idx in enumerate(lst, 1):
            score[idx] = score.get(idx, 0.0) + w / (k + rank)
    return [idx for idx, _ in sorted(score.items(), key=lambda kv: -kv[1])][:topn]


def cosine_top(query_vec: list[float], doc_vecs: list[list[float]], docs: list[dict], topn: int) -> list[int]:
    qn = math.sqrt(sum(x * x for x in query_vec))
    scored = []
    for i, vec in enumerate(doc_vecs):
        vn = math.sqrt(sum(x * x for x in vec))
        dot = sum(a * b for a, b in zip(query_vec, vec))
        scored.append((dot / (qn * vn) if qn and vn else 0.0, i))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:topn]]


def load_synonym_map(path: Path = SYNONYMS) -> dict[str, list[str]]:
    """cluster display/members → 同义扩展词表（只读）。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    m: dict[str, list[str]] = {}
    for c in (d.get("clusters") or [])[:2000]:
        display = str(c.get("display") or "").strip()
        members = [str(x.get("concept") or "").strip() for x in (c.get("members") or []) if isinstance(x, dict)]
        names = [display] + [x for x in members if x]
        names = [x for x in names if len(x) >= 2][:6]
        for n in names:
            m.setdefault(n, [])
            for other in names:
                if other != n and other not in m[n]:
                    m[n].append(other)
    return m


def expand_by_synonyms(question: str, syn: dict[str, list[str]]) -> list[str]:
    extra = []
    for name, alts in syn.items():
        if name and name in question:
            extra.extend(alts[:3])
            if len(extra) >= 8:
                break
    return extra[:8]


def expand_by_prf(question: str, bm25: BM25, top_docs: list[int], doc_tokens: list[Counter]) -> list[str]:
    q_terms = set(tokenize(question))
    extra = []
    for idx in top_docs[:5]:
        for term, _f in doc_tokens[idx].most_common(10):
            if term not in q_terms and term not in extra and len(extra) < 8:
                extra.append(term)
    return extra


def _metrics(ranked_ids: list[str], gold: list[str], sources: dict[str, str], k5: int = 5) -> dict:
    def recall(k):
        return 1.0 if any(g in ranked_ids[:k] for g in gold) else 0.0
    mrr = 0.0
    for r, did in enumerate(ranked_ids, 1):
        if did in gold:
            mrr = 1.0 / r
            break
    top5_sources = [sources[d] for d in ranked_ids[:k5] if sources.get(d)]
    return {"recall@5": recall(5), "recall@10": recall(10), "mrr": round(mrr, 4),
            "diversity@5": len(set(top5_sources)) / max(1, len(top5_sources))}


def evaluate(*, write: bool = False, k: int = RRF_K, w_bm25: float = 1.0, w_vec: float = 1.0,
             embed_fn=None, questions: list[dict] | None = None, docs: list[dict] | None = None,
             synonyms: dict[str, list[str]] | None = None) -> dict:
    import time
    t0 = time.monotonic()
    docs = docs if docs is not None else W6MOD.load_docs()
    questions = questions if questions is not None else load_questions()
    syn = synonyms if synonyms is not None else load_synonym_map()
    doc_texts = [d["text"] for d in docs]
    doc_ids = [d["doc_id"] for d in docs]
    sources = {d["doc_id"]: d.get("source") or "" for d in docs}
    embed = embed_fn or W6MOD.embed_texts
    doc_vecs = embed(doc_texts)
    bm25 = BM25(doc_texts)
    variants = ["vector_only", "hybrid", "hybrid_syn", "hybrid_prf"]
    scores = {v: [] for v in variants}
    for q in questions:
        gold = q["gold_doc_ids"]
        qvec = embed([q["question"]])[0]
        v_idx = cosine_top(qvec, doc_vecs, docs, TOP_N)
        b_idx = bm25.search(q["question"], TOP_N)
        syn_terms = expand_by_synonyms(q["question"], syn)
        prf_terms = expand_by_prf(q["question"], bm25, b_idx, bm25.tf)
        rank_lists = {
            "vector_only": [v_idx],
            "hybrid": [b_idx, v_idx],
            "hybrid_syn": [bm25.search(tokenize(q["question"]) + syn_terms, TOP_N), v_idx],
            "hybrid_prf": [bm25.search(tokenize(q["question"]) + prf_terms, TOP_N), v_idx],
        }
        for v in variants:
            lst = rank_lists[v]
            fused = rrf_fuse(lst, [w_bm25, w_vec] if len(lst) > 1 else [w_bm25], k=k, topn=10)
            ids = [doc_ids[i] for i in fused]
            m = _metrics(ids, gold, sources)
            m.update({"qid": q["qid"], "type": q["type"], "ids": ids})
            scores[v].append(m)
    def agg(v: str) -> dict:
        rs = scores[v]
        n = max(1, len(rs))
        return {"recall@5": round(sum(r["recall@5"] for r in rs) / n, 4),
                "recall@10": round(sum(r["recall@10"] for r in rs) / n, 4),
                "mrr": round(sum(r["mrr"] for r in rs) / n, 4),
                "diversity@5": round(sum(r["diversity@5"] for r in rs) / n, 4)}
    comparison = {v: agg(v) for v in variants}
    # 选优：recall@5 → mrr → 简单（vector_only 优先）
    order = ["hybrid", "hybrid_syn", "hybrid_prf", "vector_only"]
    best = max(order, key=lambda v: (comparison[v]["recall@5"], comparison[v]["mrr"], -order.index(v)))
    best_by_type = {}
    for t in {r["type"] for r in scores[best]}:
        rs = [r for r in scores[best] if r["type"] == t]
        best_by_type[t] = {"n": len(rs),
                           "recall@5": round(sum(r["recall@5"] for r in rs) / len(rs), 4),
                           "mrr": round(sum(r["mrr"] for r in rs) / len(rs), 4)}
    payload = {
        "schema_version": "rag_hybrid.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W10-RAG 混合检索（BM25 + bge-m3 + RRF）",
        "dataset": {"questions": len(questions), "docs": len(docs), "source": "W6 rag_baseline_20261104（50 题/gold 复用）"},
        "method": {
            "bm25": "纯 Python BM25 k1=1.5 b=0.75；CJK bigram + 拉丁词；top100",
            "vector": "bge-m3（ollama /api/embed）余弦 top100",
            "rrf": f"RRF score=Σ w/(k+rank)，k={k}，w_bm25={w_bm25}, w_vec={w_vec}，top10",
            "expansion": "融合 <0.85 时尝试 synonyms（concept_synonyms 簇）与 PRF（BM25 top5 文档高频词）",
            "elapsed_s": round(time.monotonic() - t0, 1),
        },
        "sources": {"baseline": str(BASELINE), "synonyms": str(SYNONYMS),
                    "baseline_sha256": hashlib.sha256(BASELINE.read_bytes()).hexdigest()},
        "baseline": {"recall@5": 0.84, "recall@10": 0.84, "mrr": 0.774,
                     "note": "W6 纯 bge-m3 向量基线（全量 50 题）"},
        "comparison": comparison,
        "chosen": best,
        "chosen_by_type": best_by_type,
        "target": {"recall@5": TARGET, "met": bool(comparison[best]["recall@5"] >= TARGET)},
        "expansion_effect": {
            "synonyms_vs_hybrid": {"recall@5_delta": round(comparison["hybrid_syn"]["recall@5"] - comparison["hybrid"]["recall@5"], 4),
                                   "mrr_delta": round(comparison["hybrid_syn"]["mrr"] - comparison["hybrid"]["mrr"], 4)},
            "prf_vs_hybrid": {"recall@5_delta": round(comparison["hybrid_prf"]["recall@5"] - comparison["hybrid"]["recall@5"], 4),
                              "mrr_delta": round(comparison["hybrid_prf"]["mrr"] - comparison["hybrid"]["mrr"], 4)},
            "note": "若同义词/PRF 相对 hybrid 无增益，则最小有效改动=RFF（BM25+bge-m3）融合本身",
        },
        "results": scores[best],
        "conclusion": (
            f"最优变体 {best}：recall@5={comparison[best]['recall@5']}（基线 0.84），"
            f"recall@10={comparison[best]['recall@10']}，MRR={comparison[best]['mrr']}（基线 0.774）；"
            + ("达标 ≥0.85。" if comparison[best]["recall@5"] >= TARGET else "未达 0.85，建议加大权重/扩展或重排。")
            + ("最小有效改动为 BM25+bge-m3 RRF 融合本身（同义词/PRF 相对无增益）。"
               if best == "hybrid" else f"扩展 {best} 有效。")),
        "limitations": [
            "评测集为 W6 合成 50 题（源文档模板生成，非人工独立标注）；",
            "BM25 分词为 CJK bigram 纯规则，未接中文分词器（避免新增依赖）；",
            "同义词扩展仅使用 concept_synonyms 簇（研报概念域）与 PRF，覆盖有限；",
            "检索只读：未写生产索引/知识库；仅本地 ollama。",
        ],
        "disclaimer": "研究输出，不构成投资建议。",
    }
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        lines = ["# RAG 混合检索评测（W10 · 2026-11-04）", "",
                 "> 目标：recall@5 0.84 → ≥0.85 · 数据集：W6 50 题/1727 文档（复用 gold）", "",
                 "## 一、对比表", "", "| 变体 | recall@5 | recall@10 | MRR | 多样性@5 |", "|---|---|---|---|---|"]
        for v in variants:
            c = comparison[v]
            lines.append(f"| {v} | {c['recall@5']} | {c['recall@10']} | {c['mrr']} | {c['diversity@5']} |")
        lines += ["", "- **基线（W6 纯向量）**：recall@5=0.84 / recall@10=0.84 / MRR=0.774",
                  f"- **选定**：{best}（recall@5={'达标' if payload['target']['met'] else '未达标'}）",
                  f"- 方法：BM25（k1=1.5,b=0.75,CJK bigram）+ bge-m3 向量 + RRF（k={k}）；扩展=同义词/PRF 择优",
                  "", "## 二、选定变体分题型", "", "| 类型 | n | recall@5 | MRR |", "|---|---|---|---|"]
        for t, v in best_by_type.items():
            lines.append(f"| {t} | {v['n']} | {v['recall@5']} | {v['mrr']} |")
        lines += ["", "## 三、结论", "", f"- {payload['conclusion']}",
                  f"- 建议阈值：混合检索 recall@5 ≥0.85、MRR ≥0.80；当前 MRR={comparison[best]['mrr']}；",
                  "", "## 四、局限", ""] + [f"- {x}" for x in payload["limitations"]] + ["", f"> {payload['disclaimer']}", ""]
        OUT_MD.write_text("\n".join(lines), encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="RAG 混合检索评测（BM25+bge-m3+RRF）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--k", type=int, default=RRF_K)
    parser.add_argument("--w-bm25", type=float, default=1.0)
    parser.add_argument("--w-vec", type=float, default=1.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate(write=args.write, k=args.k, w_bm25=args.w_bm25, w_vec=args.w_vec)
    c = payload["comparison"]
    print(f"[rag_hybrid] questions={payload['dataset']['questions']} docs={payload['dataset']['docs']} | "
          f"baseline recall@5=0.84 MRR=0.774")
    for v in c:
        print(f"  {v:12s} recall@5={c[v]['recall@5']} recall@10={c[v]['recall@10']} MRR={c[v]['mrr']}")
    print(f"  chosen={payload['chosen']} | met>=0.85: {payload['target']['met']} | elapsed={payload['method']['elapsed_s']}s")
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
