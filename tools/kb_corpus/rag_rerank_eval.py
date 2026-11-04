# -*- coding: utf-8 -*-
"""检索重排实验（W25，task-185）：复用已有 hybrid 候选，规则重排并对比 baseline。

目标：在 W13 40 题（可答 30 + 不可答 10）与 held-out 50 题上，对已有 hybrid 检索的
top10 候选做**规则重排**（不联网、不下载模型；本地无 bge-reranker 时仅规则），评估：
  - rerank@5（gold 进 top5 比例，hit@5）
  - recall@5（gold 命中数 / gold 总数）
  - MRR
  - gold_top3（gold 进 top3 比例）
并对比 baseline（原始检索顺序），输出 research/reports/rag_rerank_20261104.{json,md}。

复用（只读）：
  - hydrid 候选：research/reports/rag_hybrid_20261104.json（W6 50 题 top10，按 question 关联 W13）
  - held-out 候选：research/reports/rag_heldout_20261104.json（50 题 top10）
  - W13 题与 gold：research/reports/answer_eval_20261104.json（30 可答，citation_gold）
  - 文档元数据：rag_baseline_eval.load_docs()（1835 文档：units + cards，字段 doc_id/kind/text/concept/source）

规则特征（top10 内重排，权重可复现）：
  0.45 查询词覆盖（CJK bigram 命中率）
  0.20 标题/概念命中（concept 与 query 核心词重叠）
  0.15 来源可信（研报/书 > 投教 > 视频号博主 > 老木笔记）
  0.10 时效（源文件日期年份/月）
  0.10 引用数（同来源文件被文档库引用次数，log 归一）
排序：score 降序，tie 保持原 rank 稳定。仅重排、不扩召回（候选池不变）。

CLI::
    python tools/kb_corpus/rag_rerank_eval.py            # 生成报告
    python tools/kb_corpus/rag_rerank_eval.py --json-stdout
"""
from __future__ import annotations

import argparse
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
OUT_JSON = R / "rag_rerank_20261104.json"
OUT_MD = R / "rag_rerank_20261104.md"
W6 = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"
DATE = "20261104"
TOP_KEY = 5                      # rerank@5/recall@5 的窗口
TOP3_KEY = 3                     # gold_top3 窗口

CJK = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]+")
YEAR = re.compile(r"(20\d{2})")
MONTH = re.compile(r"20\d{2}[-_]?(0[1-9]|1[0-2])")

WEIGHTS = {"coverage": 0.45, "title": 0.20, "trust": 0.15, "fresh": 0.10, "cite": 0.10}
SOURCE_TRUST_RULES = (           # (子串, 可信分)
    ("rp_", 0.95), ("《", 0.90), ("证券研究报告", 0.95), ("投教", 0.80),
    ("视频号学习_第二期", 0.70), ("视频号学习_8博主", 0.65), ("laomu_notes", 0.60),
    ("老木", 0.60), ("backtrader", 0.75),
)


# ---------------------------------------------------------------- 文档加载（只读）
def load_w6_module():
    spec = importlib.util.spec_from_file_location("rag_baseline_eval_mod", W6)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_docs() -> list[dict]:
    return load_w6_module().load_docs()


# ---------------------------------------------------------------- 文本特征
def cjk_bigrams(text: str) -> list[str]:
    out = []
    for run in CJK.findall(str(text or "")):
        out.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return out


def latin_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in LATIN.finditer(str(text or ""))]


def query_tokens(question: str) -> set[str]:
    toks = set(cjk_bigrams(question))
    toks |= set(latin_tokens(question))
    return {t for t in toks if t}


def source_trust(doc: dict) -> float:
    s = str(doc.get("source") or "") + " " + str(doc.get("project") or "")
    for key, score in SOURCE_TRUST_RULES:
        if key in s:
            return score
    return 0.5


def freshness(doc: dict) -> float:
    s = str(doc.get("source") or "")
    m = MONTH.search(s) or YEAR.search(s)
    if not m:
        return 0.3
    y = YEAR.search(s)
    year = int(y.group(1)) if y else 2026
    month = int(MONTH.search(s).group(1)) if MONTH.search(s) else 6
    # 2026 年数据（最新）→ 1.0；2025→0.7；更早→0.4；年内按月微调
    base = {2026: 0.9, 2025: 0.6, 2024: 0.4}.get(year, 0.2)
    return round(min(1.0, base + 0.1 * (month / 12.0)), 4) if year >= 2025 else base


def title_hit(question: str, doc: dict) -> float:
    """标题/概念命中：concept 与其核心词是否出现在 query，或 query 核心词出现在 concept。"""
    concept = str(doc.get("concept") or "").strip()
    if not concept:
        # unit：用文本前 20 字作标题代理
        concept = str(doc.get("text") or "").strip()[:20]
    if not concept:
        return 0.0
    q = str(question or "")
    if concept and (concept in q or q.replace("什么是「", "").replace("」", "")[:8] in concept):
        return 1.0
    qb = set(cjk_bigrams(q))
    if not qb:
        return 0.0
    cb = set(cjk_bigrams(concept))
    overlap = len(qb & cb) / max(1, min(len(qb), len(cb)))
    return 1.0 if overlap >= 0.5 else (0.5 if overlap >= 0.25 else 0.0)


def coverage(tokens: set[str], doc: dict) -> float:
    if not tokens:
        return 0.0
    text = str(doc.get("text") or "")
    hay = text + " " + str(doc.get("concept") or "")
    hit = sum(1 for t in tokens if t in hay)
    return round(hit / len(tokens), 4)


def build_context(docs: list[dict]) -> dict:
    """全局上下文：doc 索引 + 来源被引用次数（引用数代理）+ 最大引用量。"""
    by_id = {d["doc_id"]: d for d in docs}
    src_counts = Counter(str(d.get("source") or "") for d in docs)
    max_cite = max(src_counts.values()) if src_counts else 1
    return {"docs": by_id, "src_counts": src_counts, "max_cite": max_cite}


def rule_score(question: str, doc: dict, ctx: dict, tokens: set[str] | None = None) -> float:
    """规则重排分（0~1）：覆盖/标题/可信/时效/引用数加权。"""
    tokens = tokens if tokens is not None else query_tokens(question)
    cite = math.log1p(ctx["src_counts"].get(str(doc.get("source") or ""), 0)) / math.log1p(ctx["max_cite"])
    feats = {
        "coverage": coverage(tokens, doc),
        "title": title_hit(question, doc),
        "trust": source_trust(doc),
        "fresh": freshness(doc),
        "cite": round(cite, 4),
    }
    score = sum(WEIGHTS[k] * feats[k] for k in WEIGHTS)
    return round(score, 6), feats


# ---------------------------------------------------------------- 重排 / 指标
def rerank_ids(question: str, candidate_ids: list[str], ctx: dict) -> list[str]:
    """top-K 候选规则重排：score 降序，tie 保持原顺序（稳定）。"""
    tokens = query_tokens(question)
    scored = []
    for rank, doc_id in enumerate(candidate_ids):
        doc = ctx["docs"].get(doc_id) or {"doc_id": doc_id, "text": "", "concept": "", "source": ""}
        score, _ = rule_score(question, doc, ctx, tokens)
        scored.append((-score, rank, doc_id))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in scored]


def first_gold_rank(ranked: list[str], gold: list[str]) -> int | None:
    gold_set = set(gold)
    for i, doc_id in enumerate(ranked, 1):
        if doc_id in gold_set:
            return i
    return None


def metrics(ranked: list[str], gold: list[str]) -> dict:
    """rerank@5(hit@5) / recall@5 / MRR / gold_top3。gold 为空时返回 None（不可答，不计检索指标）。"""
    gold = [g for g in (gold or []) if g]
    if not gold:
        return {}
    gold_set = set(gold)
    top5, top3 = ranked[:TOP_KEY], ranked[:TOP3_KEY]
    hit5 = 1.0 if any(g in gold_set for g in top5) else 0.0
    recall5 = len([g for g in top5 if g in gold_set]) / len(gold_set)
    top3 = 1.0 if any(g in gold_set for g in top3) else 0.0
    rank = first_gold_rank(ranked, gold)
    mrr = 1.0 / rank if rank else 0.0
    return {"rerank@5": round(hit5, 4), "recall@5": round(recall5, 4),
            "mrr": round(mrr, 4), "gold_top3": round(top3, 4)}


def aggregate(rows: list[dict]) -> dict:
    valid = [r for r in rows if r.get("baseline") and r.get("rerank")]
    if not valid:
        return {"n": 0, "baseline": {}, "rerank": {}, "delta": {}}
    def m(side):
        return {k: round(sum(r[side][k] for r in valid) / len(valid), 4)
                for k in ("rerank@5", "recall@5", "mrr", "gold_top3")}
    base, re_ = m("baseline"), m("rerank")
    delta = {k: round(re_[k] - base[k], 4) for k in base}
    return {"n": len(valid), "baseline": base, "rerank": re_, "delta": delta}


# ---------------------------------------------------------------- 数据加载
def load_hybrid_candidates() -> dict[str, list[str]]:
    d = json.loads((R / "rag_hybrid_20261104.json").read_text(encoding="utf-8"))
    return {str(r["qid"]): list(r.get("ids") or []) for r in d.get("results") or []}


def load_w6_base() -> dict[str, dict]:
    d = json.loads((R / "rag_baseline_20261104.json").read_text(encoding="utf-8"))
    return {str(r["qid"]): r for r in d.get("results") or []}


def load_w13_cases() -> dict:
    """W13 40 题：30 可答（gold=citation_gold；候选=hybrid top10，按 question 映射 W6）+ 10 不可答。"""
    ans = json.loads((R / "answer_eval_20261104.json").read_text(encoding="utf-8"))
    base = load_w6_base()
    hybrid = load_hybrid_candidates()
    qmap = {str(r.get("question")): str(r.get("qid")) for r in base.values()}
    cases, unanswerable = [], []
    for r in ans.get("results") or []:
        if not r.get("answerable"):
            unanswerable.append({"qid": r.get("qid"), "question": r.get("question")})
            continue
        q = str(r.get("question") or "")
        gold = sorted({g for g in (r.get("citation_gold") or []) if g})
        w6_qid = qmap.get(q)
        cand = hybrid.get(w6_qid) if w6_qid else None
        if not cand:
            cand = list(r.get("ctx_ids") or [])
        cases.append({"qid": r.get("qid"), "type": r.get("type"), "question": q,
                      "gold_doc_ids": gold, "candidates": list(cand),
                      "candidate_source": "hybrid_top10" if w6_qid else "ctx_top5"})
    no_gold = [c for c in cases if not c["gold_doc_ids"]]
    return {"cases": cases, "unanswerable": unanswerable,
            "total": len(ans.get("results") or []), "evaluated": len(cases),
            "retrieval_evaluated": len(cases) - len(no_gold), "no_gold": len(no_gold),
            "no_gold_qids": [c["qid"] for c in no_gold],
            "mapped_hybrid": sum(1 for c in cases if c["candidate_source"] == "hybrid_top10")}


def load_heldout_cases() -> dict:
    d = json.loads((R / "rag_heldout_20261104.json").read_text(encoding="utf-8"))
    cases = []
    for r in d.get("results") or []:
        cases.append({"qid": r.get("qid"), "type": r.get("type"), "question": r.get("question"),
                      "gold_doc_ids": list(r.get("gold_doc_ids") or []),
                      "candidates": list(r.get("top_ids") or []),
                      "candidate_source": "vector_top10_stored"})
    return {"cases": cases, "total": len(cases), "evaluated": len(cases), "mapped_hybrid": 0}


# ---------------------------------------------------------------- 评测
def reranker_status() -> dict:
    """本批不联网下载模型：仅检测本地 ollama 是否有 rerank 模型（bge-reranker 等）。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            models = [m.get("name", "") for m in json.loads(resp.read().decode("utf-8")).get("models") or []]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "mode": "rule_only", "models": [],
                "reason": f"ollama 不可用：{type(exc).__name__}"}
    rerankers = [m for m in models if "rerank" in m.lower()]
    if rerankers:
        return {"available": True, "mode": "bge_reranker_candidate", "models": rerankers,
                "reason": "发现本地 rerank 模型；本实验仍以规则重排为主口径（模型打分仅可选）"}
    return {"available": False, "mode": "rule_only", "models": [],
            "reason": f"本地无 rerank 模型（现有 {models}），按任务约束不联网下载 → 仅规则重排"}


def eval_set(cases: list[dict], ctx: dict, name: str) -> dict:
    rows, by_type = [], {}
    for c in cases:
        baseline_order = [i for i in c["candidates"] if i]
        if not c["gold_doc_ids"]:
            continue
        rerank_order = rerank_ids(c["question"], baseline_order, ctx)
        b = metrics(baseline_order, c["gold_doc_ids"])
        r = metrics(rerank_order, c["gold_doc_ids"])
        if not b or not r:
            continue
        row = {"qid": c["qid"], "type": c.get("type"), "question": c["question"],
               "gold_doc_ids": c["gold_doc_ids"], "candidate_depth": len(baseline_order),
               "candidate_source": c.get("candidate_source"),
               "baseline": b, "rerank": r,
               "changed": [i for i, (a, b2) in enumerate(zip(baseline_order, rerank_order)) if a != b2]}
        rows.append(row)
        t = str(c.get("type") or "unknown")
        by_type.setdefault(t, []).append(row)
    return {"set": name, "n": len(rows), "agg": aggregate(rows),
            "by_type": {t: aggregate(rs) for t, rs in sorted(by_type.items())}, "results": rows}


def build_report() -> dict:
    docs = load_docs()
    ctx = build_context(docs)
    w13 = load_w13_cases()
    heldout = load_heldout_cases()
    res_w13 = eval_set(w13["cases"], ctx, "w13_40")
    res_held = eval_set(heldout["cases"], ctx, "heldout_50")
    # 总体（按可评题数加权平均）
    all_rows = res_w13["results"] + res_held["results"]
    overall = aggregate(all_rows)
    status = reranker_status()
    # 上线建议
    g3 = overall["delta"].get("gold_top3", 0.0)
    r5 = overall["delta"].get("rerank@5", 0.0)
    mrr = overall["delta"].get("mrr", 0.0)
    if r5 >= 0 and g3 > 0 and mrr >= 0:
        rec = {"recommend": True, "level": "小流量 A/B",
               "reason": f"gold_top3 提升 {g3:+.4f}、rerank@5 不降（{r5:+.4f}）、MRR 不降（{mrr:+.4f}）：建议 top10 内重排小流量 A/B 上线"}
    elif g3 > 0 or r5 > 0:
        rec = {"recommend": False, "level": "仅离线",
               "reason": f"指标互有胜负（gold_top3 {g3:+.4f}、rerank@5 {r5:+.4f}、MRR {mrr:+.4f}）：不建议直接上线，先扩候选池/调权重"}
    else:
        rec = {"recommend": False, "level": "不上线",
               "reason": f"重排无增益（gold_top3 {g3:+.4f}、rerank@5 {r5:+.4f}、MRR {mrr:+.4f}）：维持 baseline"}
    digest = lambda p: __import__("hashlib").sha256(p.read_bytes()).hexdigest()  # noqa: E731
    payload = {
        "schema_version": "rag_rerank.v1",
        "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W25-检索重排：gold top3 提升实验（复用 hybrid 候选，规则重排）",
        "datasets": {
            "w13_40": {"total": w13["total"], "evaluated": w13["evaluated"],
                       "retrieval_evaluated": w13["retrieval_evaluated"],
                       "no_gold": w13["no_gold"], "no_gold_qids": w13["no_gold_qids"],
                       "unanswerable": len(w13["unanswerable"]),
                       "unanswerable_note": "不可答 10 题无 gold，不参与检索指标（仅拒答侧评测）",
                       "candidate_source": f"hybrid top10（{w13['mapped_hybrid']}/{w13['evaluated']} 按 question 映射 W6/hybrid）"},
            "heldout_50": {"total": heldout["total"], "evaluated": heldout["evaluated"],
                           "candidate_source": "现有 W12 纯向量 top10 存储候选（held-out 无 hybrid 候选落盘）"},
            "docs": len(docs),
        },
        "method": {
            "candidate_pool": "仅重排、不扩召回：使用已落盘 top10 候选（W13=hybrid、heldout=W12 向量）",
            "reranker": status,
            "rule_weights": WEIGHTS,
            "score": "0.45×查询词覆盖 + 0.20×标题/概念命中 + 0.15×来源可信 + 0.10×时效 + 0.10×引用数"
                     "（同来源文件被文档库引用次数 log 归一）；score 降序、tie 保持原 rank",
            "metrics": {"rerank@5": "gold 进 top5 比例（hit@5）", "recall@5": "top5 gold 命中数/gold 总数",
                        "mrr": "首个 gold 排名倒数", "gold_top3": "gold 进 top3 比例"},
            "no_network_download": True,
        },
        "sources": {
            "hybrid_candidates": {"path": str(R / "rag_hybrid_20261104.json"),
                                  "sha256": digest(R / "rag_hybrid_20261104.json")},
            "heldout": {"path": str(R / "rag_heldout_20261104.json"),
                        "sha256": digest(R / "rag_heldout_20261104.json")},
            "w13_questions": {"path": str(R / "answer_eval_20261104.json"),
                              "sha256": digest(R / "answer_eval_20261104.json")},
            "docs_loader": str(W6),
        },
        "overall": overall,
        "sets": {"w13_40": {k: v for k, v in res_w13.items() if k != "results"},
                 "heldout_50": {k: v for k, v in res_held.items() if k != "results"}},
        "examples": {
            "improved": [r for r in all_rows
                         if r["rerank"]["mrr"] > r["baseline"]["mrr"]][:5],
            "regressed": [r for r in all_rows
                          if r["rerank"]["mrr"] < r["baseline"]["mrr"]][:5],
        },
        "recommendation": rec,
        "conclusion": (
            f"规则重排（top10 内）：gold_top3 {overall['baseline'].get('gold_top3')}→{overall['rerank'].get('gold_top3')}"
            f"（{overall['delta'].get('gold_top3'):+.4f}）、rerank@5 {overall['baseline'].get('rerank@5')}→"
            f"{overall['rerank'].get('rerank@5')}（{overall['delta'].get('rerank@5'):+.4f}）、MRR "
            f"{overall['baseline'].get('mrr')}→{overall['rerank'].get('mrr')}（{overall['delta'].get('mrr'):+.4f}）；"
            f"建议：{rec['level']}（{rec['reason']}）。"),
        "limitations": [
            "候选池为已落盘 top10（非 top20 重算）：gold 在 11+ 名的题无法通过重排进 top5，提升空间受限；",
            "held-out 候选来自 W12 纯向量 top10（非 hybrid），两数据集候选来源不一致；",
            "本地无 bge-reranker 且按约束不联网下载 → 仅规则重排；规则权重为人工先验，未做权重学习；",
            "W13 可答 30 题由 W6 50 题按题型均衡抽取，question 精确映射（30/30），gold=citation_gold；",
            "只读评测：不改检索服务/索引，不写生产库；结果仅研究参考。",
        ],
        "disclaimer": "研究输出，不构成投资建议。",
    }
    payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def render_markdown(p: dict) -> str:
    def row(name, agg):
        b, r, d = agg["baseline"], agg["rerank"], agg["delta"]
        return (f"| {name} | {agg['n']} | {b['rerank@5']} | {r['rerank@5']} | {d['rerank@5']:+.4f} | "
                f"{b['recall@5']} | {r['recall@5']} | {d['recall@5']:+.4f} | "
                f"{b['mrr']} | {r['mrr']} | {d['mrr']:+.4f} | {b['gold_top3']} | {r['gold_top3']} | {d['gold_top3']:+.4f} |")
    L = ["# 检索重排实验（W25 · 2026-11-04）", "",
         f"> 数据集：W13 40 题（可答 {p['datasets']['w13_40']['evaluated']} + 不可答 "
         f"{p['datasets']['w13_40']['unanswerable']}）与 held-out 50 题 · 文档 {p['datasets']['docs']} 篇",
         f"> 候选：复用已落盘 top10（W13=hybrid；heldout=W12 向量）· 重排=规则（无本地 reranker，不联网下载）",
         f"> 规则分：{p['method']['score']}", "",
         "## 一、核心对比（baseline → rerank）", "",
         "| 数据集 | n | rerank@5 基→重 | Δ | recall@5 基→重 | Δ | MRR 基→重 | Δ | gold_top3 基→重 | Δ |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", ]
    for name, agg in (("overall", p["overall"]), ("w13_40", p["sets"]["w13_40"]["agg"]),
                      ("heldout_50", p["sets"]["heldout_50"]["agg"])):
        L.append(row(name, agg))
    L += ["", "## 二、分题型（gold_top3 / MRR 变化）", "", "| 数据集/类型 | n | gold_top3 基→重 | MRR 基→重 |", "|---|---:|---|---|"]
    for ds in ("w13_40", "heldout_50"):
        for t, agg in p["sets"][ds]["by_type"].items():
            L.append(f"| {ds}/{t} | {agg['n']} | {agg['baseline']['gold_top3']}→{agg['rerank']['gold_top3']} "
                     f"| {agg['baseline']['mrr']}→{agg['rerank']['mrr']} |")
    L += ["", "## 三、示例", "", "### 提升（MRR 上升，前 5）", ""]
    for r in p["examples"]["improved"]:
        L.append(f"- {r['qid']} {r['type']}：MRR {r['baseline']['mrr']}→{r['rerank']['mrr']}，"
                 f"gold_top3 {r['baseline']['gold_top3']}→{r['rerank']['gold_top3']}")
    L += ["", "### 回退（MRR 下降，前 5）", ""]
    for r in p["examples"]["regressed"]:
        L.append(f"- {r['qid']} {r['type']}：MRR {r['baseline']['mrr']}→{r['rerank']['mrr']}，"
                 f"gold_top3 {r['baseline']['gold_top3']}→{r['rerank']['gold_top3']}")
    L += ["", "## 四、结论与上线建议", "", f"- {p['conclusion']}",
          f"- 建议等级：**{p['recommendation']['level']}**（recommend={p['recommendation']['recommend']}）",
          f"- 依据：{p['recommendation']['reason']}", "",
          "## 五、局限", ""] + [f"- {x}" for x in p["limitations"]] + ["", f"> {p['disclaimer']}", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rag_rerank_eval",
                                 description="检索重排实验：规则重排 vs baseline（rerank@5/recall@5/MRR/gold_top3）")
    ap.add_argument("--write", action="store_true", help="写 research/reports/rag_rerank_20261104.{json,md}")
    ap.add_argument("--json-stdout", action="store_true")
    args = ap.parse_args(argv)
    payload = build_report()
    if args.write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
        print(f"written: {OUT_JSON} / {OUT_MD}")
    o = payload["overall"]
    print(f"overall n={o['n']} | rerank@5 {o['baseline']['rerank@5']}→{o['rerank']['rerank@5']} "
          f"| recall@5 {o['baseline']['recall@5']}→{o['rerank']['recall@5']} "
          f"| MRR {o['baseline']['mrr']}→{o['rerank']['mrr']} "
          f"| gold_top3 {o['baseline']['gold_top3']}→{o['rerank']['gold_top3']} "
          f"| 建议 {payload['recommendation']['level']}")
    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
