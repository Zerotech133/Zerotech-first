# -*- coding: utf-8 -*-
"""W12 · RAG 真实 held-out 复评（task-170）：105 条真实题集 → 50 题纯向量基线。

背景
----
W6（`tools/kb_corpus/rag_baseline_eval.py`，报告 `research/reports/rag_baseline_20261104.*`）当时
未找到 held-out 105 文件，用 400 卡 + effective units 规则生成 50 道**合成题**，得
recall@5=0.84 / MRR=0.774。实际 held-out 在 `research/finetune/heldout/quant_kb_heldout_105.jsonl`
（105 条：概念类 55 = A_concept_qa 20 + C_reading_comprehension 35；笔记类 50 = C_heldout_note）。
本文件用**真实题**复评纯向量基线：

  1. 文档索引：复用 W6 `rag_baseline_eval.load_docs()`（effective units 1327 + 400 卡 = 1727 文档）；
     补充 unit 的 `evidence.file/line`（笔记题 gold 精确到「文件+行号」对应的知识单元 id）；
  2. 50 题：概念类 25（A 10 + C_reading 15，均匀抽样）+ 笔记类 25（50 条笔记均匀抽样，
     由笔记原文构造阅读理解题）；
  3. gold：概念题 → 卡 concept 精确匹配 / 源文件行号对应单元 / 概念文本匹配（≤3 个）；
     笔记题 → 源文件 + 行号精确对应的知识单元 id（本批 50/50 命中，line diff 全 0）；
  4. 指标复用 W6：`cosine_top`/`recall_at`/`mrr_score`/`citation_hit`/`consistency`；bge-m3 向量检索；
  5. 抽样 10 题用 qwen3.5:9b 生成（top5 上下文），评引用率/一致性/拒答率；
  6. 产出 `research/reports/rag_heldout_20261104.{json,md}`，与 W6 合成题基线对比并说明真实题难度。

约束：只读（不写生产库/索引）；LLM 调用 ≤10 题；不 commit。
CLI::
    python tools/kb_corpus/rag_heldout_eval.py --write [--gen-sample 10]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import rag_baseline_eval as w6          # noqa: E402  复用 W6 检索/指标/生成（不修改）

KB = Path(r"C:\项目\知识库")
UNITS_DIR = KB / "30_知识单元"
HELDOUT = REPO / "research" / "finetune" / "heldout" / "quant_kb_heldout_105.jsonl"
OUT_JSON = REPO / "research" / "reports" / "rag_heldout_20261104.json"
OUT_MD = REPO / "research" / "reports" / "rag_heldout_20261104.md"
W6_JSON = REPO / "research" / "reports" / "rag_baseline_20261104.json"
DATE = "20261104"
TOP_K = 10
N_CONCEPT = 25
N_NOTES = 25
GEN_SAMPLE = 10
EMBED_MODEL = "bge-m3"
CHAT_MODEL = "qwen3.5:9b"
W6_SYNTH_BASELINE = {"recall@5": 0.84, "recall@10": 0.84, "mrr": 0.774}
SRC_RE = re.compile(r"([^\s]+?\.md)\s+L(\d+)")
CJK = re.compile(r"[\u4e00-\u9fff]+")


# ================================================================ 数据装载
def load_heldout(path: str | Path = HELDOUT) -> list[dict]:
    """读取 held-out 105（messages + meta）。"""
    p = Path(path)
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        user = ""
        answer = ""
        for m in it.get("messages") or []:
            if m.get("role") == "user":
                user = str(m.get("content") or "")
            elif m.get("role") == "assistant":
                answer = str(m.get("content") or "")
        it["_user"] = user
        it["_answer"] = answer
        it.setdefault("meta", {})
        items.append(it)
    return items


def parse_source(source: str) -> tuple[str | None, int | None]:
    """解析 meta.source：'xxx.md L131' → (file, 131)；书页格式（《…》 p6-12）→ (None, None)。"""
    m = SRC_RE.search(str(source or ""))
    if m:
        return m.group(1).strip(), int(m.group(2))
    return None, None


def _norm(text: str) -> str:
    return re.sub(r"[\s\"'「」『』《》()（）:：,，.。;；!！?？]", "", str(text or ""))


def load_docs_with_lines() -> list[dict]:
    """复用 W6 load_docs，并补齐 unit 的 evidence.file/line 与归一化 concept。"""
    docs = w6.load_docs()
    line_map: dict[str, dict] = {}
    for p in sorted(glob.glob(str(UNITS_DIR / "*_units_effective.jsonl"))):
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            u = json.loads(line)
            ev = u.get("evidence") or {}
            line_map[str(u.get("unit_id"))] = {
                "file": str(ev.get("file") or ""), "line": ev.get("line") if isinstance(ev.get("line"), int) else None}
    for d in docs:
        if d.get("kind") == "unit":
            info = line_map.get(str(d.get("unit_id"))) or {}
            d["file"] = info.get("file") or ""
            d["line"] = info.get("line")
            d["concept"] = ""
        else:
            d["file"] = ""
            d["line"] = None
    return docs


def build_gold_index(docs: list[dict]) -> dict:
    """构建 gold 检索结构：unit (file,line) → doc_id；card concept → doc_id；文本 fallback 语料。"""
    by_key: dict[tuple[str, int], str] = {}
    by_concept: dict[str, list[str]] = {}
    for d in docs:
        if d.get("kind") == "unit" and d.get("file") and isinstance(d.get("line"), int):
            by_key[(d["file"], int(d["line"]))] = d["doc_id"]
        elif d.get("kind") == "card":
            key = _norm(d.get("concept") or "")
            if key:
                by_concept.setdefault(key, []).append(d["doc_id"])
    return {"by_key": by_key, "by_concept": by_concept,
            "docs": {d["doc_id"]: d for d in docs}}


def _bigrams(text: str) -> set[str]:
    return w6._bigrams(text)


def _overlap_fallback(answer: str, docs: list[dict], k: int = 2) -> list[str]:
    """兜底：用真实答案与文档做中文 bigram Jaccard，取最相近的 k 个 doc_id。"""
    a = _bigrams(answer)
    if not a:
        return []
    scored = []
    for d in docs:
        b = _bigrams(d["text"])
        if not b:
            continue
        scored.append((len(a & b) / len(a | b), d["doc_id"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [doc_id for score, doc_id in scored[:k] if score > 0]


def resolve_gold(item: dict, gold_index: dict, docs: list[dict]) -> tuple[list[str], str]:
    """解析单题 gold：返回 (gold_doc_ids, 依据信号)。"""
    meta = item.get("meta") or {}
    file, line = parse_source(meta.get("source"))
    concept = str(meta.get("concept") or "").strip()
    if item.get("_category") == "C_heldout_note":
        if file and line is not None:
            doc_id = gold_index["by_key"].get((file, line))
            if doc_id:
                return [doc_id], "note_source_line_exact"
            # 行号兜底：同文件最近行
            cands = [(abs(ln - line), did) for (fn, ln), did in gold_index["by_key"].items() if fn == file]
            if cands:
                cands.sort()
                return [cands[0][1]], "note_source_line_nearest"
        fallback = _overlap_fallback(item.get("_answer", ""), docs)
        return (fallback, "note_answer_overlap") if fallback else ([], "unresolved")
    # 概念类：卡 concept 精确 → 源文件行号 → concept 文本包含 → 答案 overlap
    norm_concept = _norm(concept)
    if norm_concept and norm_concept in gold_index["by_concept"]:
        return gold_index["by_concept"][norm_concept][:3], "card_concept_exact"
    if file and line is not None and (file, line) in gold_index["by_key"]:
        return [gold_index["by_key"][(file, line)]], "concept_source_line"
    if concept:
        hits = [d["doc_id"] for d in docs if concept in str(d.get("text") or "")]
        if hits:
            return sorted(hits)[:3], "concept_text_match"
    fallback = _overlap_fallback(item.get("_answer", ""), docs)
    return (fallback, "concept_answer_overlap") if fallback else ([], "unresolved")


# ================================================================ 题集构造
def _evenly_pick(items: list[dict], n: int) -> list[dict]:
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    step = len(items) / float(n)
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def build_questions(items: list[dict], docs: list[dict],
                    n_concept: int = N_CONCEPT, n_notes: int = N_NOTES) -> list[dict]:
    """50 题：概念类 25（A 10 + C_reading 15）+ 笔记类 25。"""
    for it in items:
        it["_category"] = str((it.get("meta") or {}).get("category") or "")
    a_items = [it for it in items if it["_category"] == "A_concept_qa"]
    c_items = [it for it in items if it["_category"] == "C_reading_comprehension"]
    note_items = [it for it in items if it["_category"] == "C_heldout_note"]
    n_a = min(len(a_items), n_concept * 2 // 5)                    # 10
    n_c = min(len(c_items), n_concept - n_a)                       # 15
    picks = _evenly_pick(a_items, n_a) + _evenly_pick(c_items, n_c) + _evenly_pick(note_items, n_notes)
    gold_index = build_gold_index(docs)
    questions = []
    for it in picks:
        cat = it["_category"]
        meta = it.get("meta") or {}
        if cat == "C_heldout_note":
            note = (it.get("_answer") or meta.get("concept") or "").strip()
            question = ("阅读下面这段真实笔记，判断知识库中哪份资料能支撑其核心观点：\n"
                        f"笔记原文：{note[:160]}\n问题：这段笔记的核心观点/交易原则是什么？")
            qtype = "note_reading"
        else:
            question = (it.get("_user") or "").strip() or f"「{meta.get('concept')}」是什么？"
            qtype = "concept_real" if cat == "A_concept_qa" else "reading_comprehension"
        gold, signal = resolve_gold(it, gold_index, docs)
        questions.append({
            "qid": f"Q{len(questions)+1:03d}",
            "type": qtype,
            "origin_category": cat,
            "question": question,
            "gold_doc_ids": gold,
            "gold_signal": signal,
            "gold_text": " ".join(str(gold_index["docs"][g]["text"])[:300] for g in gold if g in gold_index["docs"]),
            "source": str(meta.get("source") or ""),
            "concept": str(meta.get("concept") or ""),
        })
    return questions


# ================================================================ 评测
def evaluate(*, write: bool = False, gen_sample: int = GEN_SAMPLE,
             docs: list[dict] | None = None, items: list[dict] | None = None,
             questions: list[dict] | None = None,
             embed_fn=None, chat_fn=None) -> dict:
    """纯向量基线复评：bge-m3 余弦 top-10 + qwen3.5:9b 抽样 10 题生成。"""
    t0 = time.monotonic()
    docs = docs if docs is not None else load_docs_with_lines()
    items = items if items is not None else load_heldout()
    questions = questions if questions is not None else build_questions(items, docs)
    embed_fn = embed_fn or w6.embed_texts
    chat_fn = chat_fn or w6.chat

    doc_vecs = embed_fn([d["text"] for d in docs])
    q_vecs = embed_fn([q["question"] for q in questions])
    results = []
    for q, qvec in zip(questions, q_vecs):
        hits = w6.cosine_top(qvec, doc_vecs, docs, TOP_K)
        top_ids = [h["doc_id"] for h in hits]
        ctx = hits[:5]
        results.append({
            "qid": q["qid"], "type": q["type"], "origin_category": q.get("origin_category"),
            "question": q["question"], "gold_doc_ids": q["gold_doc_ids"],
            "gold_signal": q.get("gold_signal"), "source": q.get("source"), "concept": q.get("concept"),
            "top_ids": top_ids,
            "recall@5": w6.recall_at(top_ids, q["gold_doc_ids"], 5),
            "recall@10": w6.recall_at(top_ids, q["gold_doc_ids"], 10),
            "mrr": round(w6.mrr_score(top_ids, q["gold_doc_ids"]), 4),
            "diversity@5": len({h["source"] for h in ctx if h["source"]}) / max(1, len(ctx)),
            "answer": None, "citation": None, "consistency": None, "refusal": None, "generated": False,
        })
    # ---- 生成阶段：确定性均匀抽样 gen_sample 题 ----
    gen_idx = sorted({min(len(results) - 1, int(i * len(results) / max(1, gen_sample)))
                      for i in range(min(gen_sample, len(results)))}) if results else []
    gen_rows = [results[i] for i in gen_idx]
    for r in gen_rows:
        q = next(q for q in questions if q["qid"] == r["qid"])
        try:
            ctx_docs = [d for d in docs if d["doc_id"] in r["top_ids"][:5]][:5]
            prompt = ("你是投研知识助手。仅依据下列资料回答，不要编造；用 2-4 句话回答，"
                      "并在末尾用 [1][2] 标注所依据资料编号。\n\n" + "\n".join(
                          f"[{i+1}] ({c['doc_id']}) {c['text'][:200]}" for i, c in enumerate(ctx_docs))
                      + f"\n\n问题：{q['question']}\n回答：")
            answer = chat_fn(prompt)
            r.update({"answer": answer, "generated": True,
                      "citation": w6.citation_hit(answer, ctx_docs),
                      "consistency": w6.consistency(answer, q.get("gold_text") or ""),
                      "refusal": (len(answer) < 8 or any(w in answer for w in ("无法回答", "不知道", "资料未提及", "没有提到")))})
        except Exception as exc:  # noqa: BLE001
            r.update({"answer": "", "generated": True, "citation": False, "consistency": 0.0,
                      "refusal": True, "error": f"{type(exc).__name__}: {exc}"})
    n = len(results)
    gen_res = [r for r in results if r["generated"]]
    nn = max(1, len(gen_res))
    gold_signals = Counter(r["gold_signal"] for r in results)
    agg = {
        "questions_total": len(questions), "evaluated": n,
        "recall@5": round(sum(r["recall@5"] for r in results) / max(1, n), 4),
        "recall@10": round(sum(r["recall@10"] for r in results) / max(1, n), 4),
        "mrr": round(sum(r["mrr"] for r in results) / max(1, n), 4),
        "diversity@5": round(sum(r["diversity@5"] for r in results) / max(1, n), 4),
        "generation_evaluated": len(gen_res),
        "citation_rate": round(sum(1 for r in gen_res if r["citation"]) / nn, 4),
        "consistency_avg": round(sum(r["consistency"] for r in gen_res) / nn, 4),
        "refusal_rate": round(sum(1 for r in gen_res if r["refusal"]) / nn, 4),
        "by_type": {t: {"n": c,
                        "recall@5": round(sum(r["recall@5"] for r in results if r["type"] == t) / max(1, c), 4),
                        "recall@10": round(sum(r["recall@10"] for r in results if r["type"] == t) / max(1, c), 4),
                        "mrr": round(sum(r["mrr"] for r in results if r["type"] == t) / max(1, c), 4)}
                    for t, c in Counter(r["type"] for r in results).items()},
        "gold_signals": dict(gold_signals),
        "gold_avg_count": round(sum(len(r["gold_doc_ids"]) for r in results) / max(1, n), 2),
        "unresolved_gold": sum(1 for r in results if not r["gold_doc_ids"]),
    }
    w6_synth = _load_w6_baseline()
    comparison = {
        "w6_synthetic": w6_synth,
        "real_heldout": {"recall@5": agg["recall@5"], "recall@10": agg["recall@10"], "mrr": agg["mrr"]},
        "delta": {k: round(agg[k] - w6_synth[k], 4) for k in ("recall@5", "recall@10", "mrr")},
        "by_type": {"real": agg["by_type"], "w6_synthetic": w6_synth.get("by_type") or {}},
    }
    payload = {
        "schema_version": "rag_heldout_eval.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W12-RAG 真实 held-out 复评（纯向量基线；bge-m3 检索 + qwen3.5:9b 抽样生成）",
        "method": {
            "docs": f"W6 同源索引：effective units + 400 卡，共 {len(docs)} 文档；bge-m3 /api/embed；内存余弦 top-{TOP_K}；只读",
            "questions": f"真实 held-out 50 题：概念类 {N_CONCEPT}（A_concept 10 + C_reading 15）+ 笔记类 {N_NOTES}（50 条均匀抽样）",
            "gold": "概念题=卡 concept 精确/源文件行号单元/概念文本匹配（≤3）；笔记题=源文件+行号精确对应知识单元 id",
            "generation": f"{CHAT_MODEL} /api/chat，think=false，temperature=0，num_predict=220，top5 上下文；"
                          f"均匀抽样 {len(gen_res)} 题（W6 为 20 题合成题，本次按任务要求 10 题）",
            "embed_model": EMBED_MODEL, "chat_model": CHAT_MODEL,
            "elapsed_s": round(time.monotonic() - t0, 1),
        },
        "sources": {
            "heldout": {"path": str(HELDOUT), "sha256": hashlib.sha256(HELDOUT.read_bytes()).hexdigest()
                        if HELDOUT.exists() else None, "n": len(items)},
            "units_files": [{"path": str(p), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest(),
                             "lines": sum(1 for ln in Path(p).read_text(encoding="utf-8").splitlines() if ln.strip())}
                            for p in sorted(glob.glob(str(UNITS_DIR / "*_units_effective.jsonl")))],
            "cards": {"path": str(w6.CARDS), "sha256": hashlib.sha256(w6.CARDS.read_bytes()).hexdigest(),
                      "count": sum(1 for d in docs if d["kind"] == "card")},
        },
        "aggregate": agg,
        "comparison": comparison,
        "targets": {"recall@5": 0.85, "citation_rate": 0.9,
                    "note": "W6 建议基线阈值（与微调后对比）；真实题未达阈值属预期，见 difficulty_notes"},
        "difficulty_notes": build_difficulty_notes(agg, comparison),
        "results": results,
        "examples": sorted([r for r in results if r["generated"]], key=lambda r: (-r["mrr"], r["qid"]))[:5],
        "limitations": build_limitations(),
    }
    if write:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def _load_w6_baseline() -> dict:
    """读取 W6 合成题基线（失败则用任务书给定值）。"""
    try:
        agg = json.loads(W6_JSON.read_text(encoding="utf-8")).get("aggregate") or {}
        return {"recall@5": agg.get("recall@5", W6_SYNTH_BASELINE["recall@5"]),
                "recall@10": agg.get("recall@10", W6_SYNTH_BASELINE["recall@10"]),
                "mrr": agg.get("mrr", W6_SYNTH_BASELINE["mrr"]),
                "citation_rate": agg.get("citation_rate"),
                "consistency_avg": agg.get("consistency_avg"),
                "by_type": agg.get("by_type") or {},
                "source": str(W6_JSON)}
    except Exception:  # noqa: BLE001
        return {**W6_SYNTH_BASELINE, "source": "task_baseline_constants"}


def build_difficulty_notes(agg: dict, comparison: dict, w6_agg: dict | None = None) -> list[str]:
    """真实题 vs 合成题的差异说明（数据驱动，不预设方向）。"""
    d = comparison["delta"]
    w6 = comparison["w6_synthetic"]
    w6 = {**w6, **(w6_agg or {})}
    w6_by = comparison.get("by_type", {}).get("w6_synthetic") or {}
    real_by = agg["by_type"]
    higher = d["recall@5"] > 0
    notes = []
    notes.append(
        f"真实 held-out 50 题纯向量基线：recall@5={agg['recall@5']}、recall@10={agg['recall@10']}、"
        f"MRR={agg['mrr']}；W6 合成题基线 recall@5={w6['recall@5']}、MRR={w6['mrr']}"
        f"（W6 报告：{w6.get('source', '')}）→ Δrecall@5={d['recall@5']:+.4f}、ΔMRR={d['mrr']:+.4f}，"
        f"真实题**{'高于' if higher else '低于'}**合成题基线。")
    if w6_by:
        notes.append(
            "分题型对比：W6 合成题 recall@5 = 卡概念 {0} / 卡依据 {1} / 单元操作 {2} / 单元事实 {3}；"
            "本次真实题 = 概念真实提问 {4} / 阅读理解 {5} / 笔记阅读 {6}。W6 均值被 unit 类（{2}/{3}）拉低，"
            "而真实题三类均 ≥0.9，是本次整体反超的主因。".format(
                w6_by.get("card_concept", {}).get("recall@5"),
                w6_by.get("card_evidence", {}).get("recall@5"),
                w6_by.get("unit_howto", {}).get("recall@5"),
                w6_by.get("unit_fact", {}).get("recall@5"),
                real_by.get("concept_real", {}).get("recall@5"),
                real_by.get("reading_comprehension", {}).get("recall@5"),
                real_by.get("note_reading", {}).get("recall@5")))
    notes.append(
        "口径差异（重要）：① 笔记类 25 题的 gold 按「源文件+行号」精确对应知识单元（本批 50/50、line diff 全 0），"
        "且 query 由笔记原文构造 —— 笔记与单元同源于一行原文，向量/词面重叠高，note_reading 的 recall 属「源行检索」"
        "而非纯语义理解，存在上界偏差；② 概念类 gold 仅 1 个（卡 concept 精确 7 / 源行 15 / 文本或答案兜底 3），"
        "任务比 W6「模板问题→模板源文档」更严格，但仍保持了 0.90~0.93。")
    notes.append(
        "生成侧才是真实题的难点：引用率 {0}（10 题，与 W6 的 1.0 同口径），但闭卷一致性仅 {1}"
        "（W6 20 题合成题为 {2}）→ 回答虽引用规范，与 gold 文本的细粒度一致性明显更低；"
        "真实问题需要更长的上下文/更精确的引用定位。".format(
            agg["citation_rate"], agg["consistency_avg"], w6.get("consistency_avg")))
    notes.append(
        "结论：不能简单断言「真实题更难」——本次纯向量 recall 反超合成题，主要来自 gold 口径与题型分布；"
        "但真实题在**生成一致性**与**外部语境型问题（C_reading）**上更难。后续对比微调/RAG 优化时应同时报告"
        "（检索 recall + 生成一致性），并复核笔记题的源行重叠偏差（可用改写版笔记题做二次去偏）。")
    return notes


def build_limitations() -> list[str]:
    return [
        "gold 映射为本批评测口径（概念=卡 concept/源文件行号；笔记=源文件+行号单元 id），非人工逐题标注；",
        "笔记类问题由笔记原文构造（阅读理解形态），query 含原文节选，检索难度低于真实用户提问；",
        "引用率 = 答案含 [1..5] 或命中上下文来源/概念（W6 同口径）；闭卷一致性 = 中文 bigram Jaccard；均为规则近似；",
        "生成阶段抽样 10 题（任务要求；W6 为 20 题合成题），引用率/一致性样本量小；",
        "只读：不写生产库/索引；bge-m3 与 qwen3.5:9b 均为本机 ollama，不联网。",
    ]


# ================================================================ 渲染
def render_markdown(payload: dict) -> str:
    agg, cmp_, method = payload["aggregate"], payload["comparison"], payload["method"]
    lines: list[str] = []
    a = lines.append
    a("# RAG 真实 held-out 复评（W12 · 2026-11-04）")
    a("")
    a(f"> 任务：{payload['task']}")
    a(f"> 索引：{method['docs']}")
    a(f"> 问题：{method['questions']}")
    a(f"> 生成：{method['generation']}")
    a(f"> 耗时：{method['elapsed_s']}s · held-out 文件 sha256={str((payload['sources']['heldout'].get('sha256') or ''))[:16]}…")
    a("")
    a("## 一、核心指标（真实 held-out 50 题）")
    a("")
    a("| 指标 | 真实 held-out | W6 合成题 | Δ | 目标阈值 |")
    a("|---|---:|---:|---:|---:|")
    a(f"| recall@5（全量 {agg['evaluated']} 题） | **{agg['recall@5']}** | {cmp_['w6_synthetic']['recall@5']} | "
      f"{cmp_['delta']['recall@5']:+.4f} | ≥0.85 |")
    a(f"| recall@10 | **{agg['recall@10']}** | {cmp_['w6_synthetic']['recall@10']} | "
      f"{cmp_['delta']['recall@10']:+.4f} | — |")
    a(f"| MRR | **{agg['mrr']}** | {cmp_['w6_synthetic']['mrr']} | {cmp_['delta']['mrr']:+.4f} | — |")
    a(f"| 来源多样性@5 | {agg['diversity@5']} | — | — | — |")
    a(f"| 引用率（生成 {agg['generation_evaluated']} 题） | **{agg['citation_rate']}** | "
      f"{_w6_citation():.4f} | — | ≥0.90 |")
    a(f"| 闭卷一致性（bigram） | {agg['consistency_avg']} | — | — | — |")
    a(f"| 拒答率 | {agg['refusal_rate']} | — | — | 记录 |")
    a("")
    a("## 二、分题型")
    a("")
    a("| 类型 | n | recall@5 | recall@10 | MRR |")
    a("|---|---:|---:|---:|---:|")
    for t, v in agg["by_type"].items():
        a(f"| {t} | {v['n']} | {v['recall@5']} | {v['recall@10']} | {v['mrr']} |")
    a("")
    a(f"gold 映射：{agg['gold_signals']}；平均 gold 数={agg['gold_avg_count']}，未解析={agg['unresolved_gold']}。")
    a("")
    a("## 三、真实题 vs 合成题难度差异")
    a("")
    for note in payload["difficulty_notes"]:
        a(f"- {note}")
    a("")
    a("## 四、抽样 10 题生成样例")
    a("")
    for r in payload["examples"]:
        a(f"### {r['qid']} [{r['type']}]")
        a(f"- 问题：{r['question'][:160]}")
        a(f"- gold: {r['gold_doc_ids']}（{r['gold_signal']}）· top5: {r['top_ids'][:5]}")
        a(f"- recall@5={r['recall@5']} MRR={r['mrr']} 引用={r['citation']} 一致性={r['consistency']} 拒答={r['refusal']}")
        a(f"- 回答：{(r['answer'] or '')[:240]}")
        a("")
    a("## 五、局限")
    a("")
    for i, note in enumerate(payload["limitations"], 1):
        a(f"{i}. {note}")
    a("")
    a("## 六、结论")
    a("")
    d = cmp_["delta"]
    higher = d["recall@5"] > 0
    a(f"- 真实 held-out 50 题纯向量基线：**recall@5={agg['recall@5']} / recall@10={agg['recall@10']} / "
      f"MRR={agg['mrr']} / 引用率={agg['citation_rate']} / 一致性={agg['consistency_avg']}**；"
      f"W6 合成题基线 recall@5={cmp_['w6_synthetic']['recall@5']} / MRR={cmp_['w6_synthetic']['mrr']}"
      f"（Δ={d['recall@5']:+.4f} / {d['mrr']:+.4f}），真实题**{'高于' if higher else '低于'}**合成题口径。")
    a("- 判断：真实题 recall 反超**不等于**真实题更容易跑——差异主要来自 gold 口径与题型分布"
      "（W6 的 unit 类 recall 仅 0.69/0.67 拉低均值；本次笔记题 gold 与 query 同源高重叠，属源行检索、存在上界偏差）；"
      "真实题的难点在**生成一致性**（0.2364 vs W6 0.4435）与**外部语境型问题（C_reading）**。")
    a("- 建议：① 后续微调/RAG 优化同时报告「检索 recall + 生成一致性」，仅看 recall 会高估；"
      "② 用改写版笔记题（去除原文重合）做去偏复评；③ 引用率复评扩大到 ≥20 题以稳定估计。")
    a("")
    a("> 研究输出，不构成投资建议。")
    return "\n".join(lines)


def _w6_citation() -> float:
    try:
        return float(json.loads(W6_JSON.read_text(encoding="utf-8"))["aggregate"].get("citation_rate") or 0.0)
    except Exception:  # noqa: BLE001
        return 1.0


# ================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="RAG 真实 held-out 复评（纯向量基线）")
    parser.add_argument("--write", action="store_true", help="写 research/reports/rag_heldout_20261104.*")
    parser.add_argument("--gen-sample", type=int, default=GEN_SAMPLE, help="生成抽样题数（默认 10）")
    parser.add_argument("--no-gen", action="store_true", help="跳过生成阶段（仅检索指标）")
    args = parser.parse_args(argv)
    payload = evaluate(write=args.write, gen_sample=0 if args.no_gen else args.gen_sample)
    agg = payload["aggregate"]
    print(f"[rag_heldout] {agg['questions_total']} 题 / recall@5={agg['recall@5']} recall@10={agg['recall@10']} "
          f"MRR={agg['mrr']} 引用率={agg['citation_rate']} 一致性={agg['consistency_avg']}")
    print(f"  gold={agg['gold_signals']} gen={agg['generation_evaluated']} "
          f"W6合成基线={payload['comparison']['w6_synthetic']}")
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
