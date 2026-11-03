# -*- coding: utf-8 -*-
"""W6 RAG 基线评测（task-164）：本地 bge-m3 检索 + qwen3.5:9b 生成，50 题。

流程：
  1) 索引：effective units（1331，30_知识单元/*_units_effective.jsonl）+ 400 卡（kb_learning_cards.json）
     共 1731 文档，bge-m3（ollama /api/embed）编码 → 内存余弦检索（top-k=5/10），不写生产库；
  2) 问题：50 题（25 卡：概念解释/依据；25 单元：操作要点/事实溯源），gold = 文档 id；
  3) 生成：每题取 top5 上下文 → qwen3.5:9b（ollama /api/chat，think=false，temp=0，num_predict=220）回答；
     评 recall@5/10、MRR、来源多样性、引用率、闭卷一致性、拒答数；
  4) 超时：生成阶段预算默认 900s，超时停止并记录 sampled 数（任务规定可抽样 20 题并注明）；
  5) 产出 research/reports/rag_baseline_20261104.{json,md}。

约束：只读知识库/报告；不写生产数据；LLM 调用 ≤50 题；不 commit。
CLI::
    python tools/kb_corpus/rag_baseline_eval.py build --write [--budget 900]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
UNITS_DIR = KB / "30_知识单元"
CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"
R = REPO / "research" / "reports"
OUT_JSON = R / "rag_baseline_20261104.json"
OUT_MD = R / "rag_baseline_20261104.md"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3"
CHAT_MODEL = "qwen3.5:9b"
DATE = "20261104"
TOP_K = 10
CJK = re.compile(r"[\u4e00-\u9fff]+")
CITE = re.compile(r"\[([1-5])\]")


def _http_json(url: str, payload: dict | None = None, timeout: float = 60):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, (json.loads(body.decode("utf-8")) if body else None)


def embed_texts(texts: list[str], model: str = EMBED_MODEL, batch: int = 16) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        _, payload = _http_json(f"{OLLAMA}/api/embed", {"model": model, "input": chunk}, timeout=180)
        vecs = (payload or {}).get("embeddings") or []
        if len(vecs) != len(chunk):
            raise RuntimeError(f"embed 返回数量不符：期望 {len(chunk)}，实际 {len(vecs)}")
        out.extend(vecs)
    return out


def chat(prompt: str, model: str = CHAT_MODEL, timeout: float = 90) -> str:
    _, payload = _http_json(f"{OLLAMA}/api/chat", {
        "model": model, "stream": False, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": 220},
    }, timeout=timeout)
    return str(((payload or {}).get("message") or {}).get("content") or "").strip()


def load_docs() -> list[dict]:
    docs = []
    for p in sorted(UNITS_DIR.glob("*_units_effective.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            u = json.loads(line)
            claim = str(u.get("claim") or "").strip()
            if len(claim) < 10:
                continue
            docs.append({"doc_id": f"U:{u.get('unit_id')}", "kind": "unit",
                         "text": claim, "concept": "", "source": str((u.get("evidence") or {}).get("file") or ""),
                         "project": u.get("project_id"), "unit_id": u.get("unit_id")})
    cards = json.loads(CARDS.read_text(encoding="utf-8-sig")).get("cards") or []
    for i, c in enumerate(cards):
        text = " ".join(str(c.get(k) or "") for k in ("q", "a", "evidence")).strip()
        if len(text) < 10:
            continue
        docs.append({"doc_id": f"C:{i:03d}", "kind": "card", "text": text,
                     "concept": str(c.get("concept") or ""), "source": str(c.get("source") or ""),
                     "project": "cards", "card_index": i})
    return docs


def _concept_from_claim(claim: str) -> str:
    head = re.split(r"[是：:，,。；;！!？?]", claim, maxsplit=1)[0].strip()
    return head[:20] if 2 <= len(head) <= 20 else claim[:20]


def build_questions(docs: list[dict], n: int = 50) -> list[dict]:
    units = [d for d in docs if d["kind"] == "unit"]
    cards = [d for d in docs if d["kind"] == "card"]
    qs = []
    # 25 卡题：概念解释 + 依据（按 topic/来源轮转抽样）
    cards_sorted = sorted(cards, key=lambda d: (d.get("concept") or "", d["doc_id"]))
    step = max(1, len(cards_sorted) // 25)
    pick_cards = cards_sorted[::step][:25]
    for i, d in enumerate(pick_cards):
        qs.append({"qid": f"Q{len(qs)+1:03d}", "type": "card_concept" if i % 2 == 0 else "card_evidence",
                   "question": (f"「{d['concept']}」的核心要点是什么？" if i % 2 == 0
                                else f"关于「{d['concept']}」，资料中的依据/来源是什么？"),
                   "gold_doc_ids": [d["doc_id"]], "gold_text": d["text"][:300]})
    # 25 单元题：操作要点 + 事实溯源（按项目轮转抽样）
    by_proj: dict[str, list[dict]] = {}
    for d in units:
        by_proj.setdefault(d["project"] or "", []).append(d)
    picks = []
    idx = 0
    while len(picks) < 25 and any(by_proj.values()):
        for proj in list(by_proj):
            if by_proj[proj] and len(picks) < 25:
                picks.append(by_proj[proj].pop(0))
        idx += 1
    for i, d in enumerate(picks[:25]):
        concept = _concept_from_claim(d["text"])
        qs.append({"qid": f"Q{len(qs)+1:03d}", "type": "unit_howto" if i % 2 == 0 else "unit_fact",
                   "question": (f"关于「{concept}」，操作要点是什么？" if i % 2 == 0
                                else f"「{concept}」的依据/事实是什么？"),
                   "gold_doc_ids": [d["doc_id"]], "gold_text": d["text"][:300]})
    return qs[:n]


def recall_at(top_ids: list[str], gold: list[str], k: int) -> float:
    return 1.0 if any(g in top_ids[:k] for g in gold) else 0.0


def mrr_score(top_ids: list[str], gold: list[str]) -> float:
    for r, did in enumerate(top_ids, 1):
        if did in gold:
            return 1.0 / r
    return 0.0


def _bigrams(text: str) -> set[str]:
    chars = "".join(CJK.findall(str(text or "")))
    return {chars[i:i + 2] for i in range(max(0, len(chars) - 1))}


def consistency(answer: str, gold_text: str) -> float:
    a, g = _bigrams(answer), _bigrams(gold_text)
    return round(len(a & g) / len(a | g), 4) if a and g else 0.0


def citation_hit(answer: str, contexts: list[dict]) -> bool:
    if CITE.search(answer):
        return True
    for c in contexts:
        src = str(c.get("source") or "")
        if src and src in answer:
            return True
        concept = str(c.get("concept") or "")
        if concept and len(concept) >= 3 and concept in answer:
            return True
    return False


def cosine_top(query_vec: list[float], doc_vecs: list[list[float]], docs: list[dict], k: int = TOP_K) -> list[dict]:
    import math
    qn = math.sqrt(sum(x * x for x in query_vec))
    scored = []
    for doc, vec in zip(docs, doc_vecs):
        vn = math.sqrt(sum(x * x for x in vec))
        dot = sum(a * b for a, b in zip(query_vec, vec))
        scored.append((dot / (qn * vn) if qn and vn else 0.0, doc))
    scored.sort(key=lambda x: -x[0])
    return [{"doc_id": d["doc_id"], "kind": d["kind"], "source": d.get("source"),
             "score": round(s, 4), "text": d["text"][:200]} for s, d in scored[:k]]


def evaluate(*, write: bool = False, n_questions: int = 50, budget_s: int = 600,
             gen_limit: int | None = None, embed_fn=embed_texts, chat_fn=chat,
             docs: list[dict] | None = None, questions: list[dict] | None = None) -> dict:
    t0 = time.monotonic()
    docs = docs if docs is not None else load_docs()
    questions = questions if questions is not None else build_questions(docs, n_questions)
    doc_vecs = embed_fn([d["text"] for d in docs])
    q_vecs = embed_fn([q["question"] for q in questions])
    results = []
    for q, qvec in zip(questions, q_vecs):
        hits = cosine_top(qvec, doc_vecs, docs, TOP_K)
        ctx = hits[:5]
        results.append({
            "qid": q["qid"], "type": q["type"], "question": q["question"],
            "gold_doc_ids": q["gold_doc_ids"],
            "top_ids": [h["doc_id"] for h in hits],
            "recall@5": recall_at([h["doc_id"] for h in hits], q["gold_doc_ids"], 5),
            "recall@10": recall_at([h["doc_id"] for h in hits], q["gold_doc_ids"], 10),
            "mrr": round(mrr_score([h["doc_id"] for h in hits], q["gold_doc_ids"]), 4),
            "diversity@5": len({h["source"] for h in ctx if h["source"]}) / max(1, len(ctx)),
            "answer": None, "citation": None, "consistency": None, "refusal": None,
            "generated": False,
        })
    # ---- 生成阶段（限时抽样：默认全部；gen_limit 控制 ≤N 题）----
    gen_target = len(results) if gen_limit is None else max(0, min(gen_limit, len(results)))
    sampled_note = None
    if gen_target < len(results):
        sampled_note = f"生成阶段按限时抽样 {gen_target}/{len(results)} 题（检索指标为全量 50 题）"
    gen_start = time.monotonic()
    gen_n = 0
    for r, q in zip(results, questions):
        if gen_n >= gen_target:
            break
        if time.monotonic() - gen_start > budget_s:
            sampled_note = (f"生成阶段超预算 {budget_s}s，已生成 {gen_n}/{len(questions)} 题后停止"
                            f"（检索为全量 {len(results)} 题；抽样 {gen_n} 题并注明）")
            break
        try:
            ctx_docs = [d for d in docs if d["doc_id"] in r["top_ids"][:5]][:5]
            prompt = ("你是投研知识助手。仅依据下列资料回答，不要编造；用 2-4 句话回答，"
                      "并在末尾用 [1][2] 标注所依据资料编号。\n\n" + "\n".join(
                          f"[{i+1}] ({c['doc_id']}) {c['text'][:200]}" for i, c in enumerate(ctx_docs))
                      + f"\n\n问题：{q['question']}\n回答：")
            answer = chat_fn(prompt)
            r.update({"answer": answer, "generated": True,
                      "citation": citation_hit(answer, ctx_docs),
                      "consistency": consistency(answer, q["gold_text"]),
                      "refusal": (len(answer) < 8 or any(w in answer for w in ("无法回答", "不知道", "资料未提及", "没有提到")))})
        except Exception as exc:  # noqa: BLE001
            r.update({"answer": "", "generated": True, "citation": False,
                      "consistency": 0.0, "refusal": True, "error": f"{type(exc).__name__}: {exc}"})
        gen_n += 1
    n = len(results)
    gen_res = [r for r in results if r["generated"]]
    nn = max(1, len(gen_res))
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
        "by_type": {t: {"n": c, "recall@5": round(sum(r["recall@5"] for r in results if r["type"] == t) / max(1, c), 4)}
                    for t, c in Counter(r["type"] for r in results).items()},
    }
    payload = {
        "schema_version": "rag_baseline.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W6-RAG 基线评测（bge-m3 检索 + qwen3.5:9b 生成）",
        "method": {
            "index": f"effective units + 400 卡，共 {len(docs)} 文档；bge-m3 /api/embed；内存余弦 top-{TOP_K}；不写生产库",
            "questions": f"{len(questions)} 题（卡概念/卡依据/单元操作/单元事实），gold=文档 id",
            "generation": f"{CHAT_MODEL} /api/chat，think=false，temperature=0，num_predict=220，top5 上下文；"
                          f"实际生成 {len(gen_res)} 题（限时抽样）",
            "retrieval_full": True, "gen_limit": gen_limit,
            "budget_s": budget_s, "elapsed_s": round(time.monotonic() - t0, 1),
        },
        "sources": {
            "units": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                       "lines": sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())}
                      for p in sorted(UNITS_DIR.glob("*_units_effective.jsonl"))],
            "cards": {"path": str(CARDS), "sha256": hashlib.sha256(CARDS.read_bytes()).hexdigest(),
                      "count": sum(1 for d in docs if d["kind"] == "card")},
        },
        "aggregate": agg,
        "targets": {"recall@5": 0.85, "citation_rate": 0.9,
                    "note": "建议基线阈值（与微调后对比）；未达阈值需补检索/引用策略"},
        "sampled_note": sampled_note,
        "limitations": [
            "held-out 105（概念55+笔记50）在本次工作区未找到交付文件（另一并行任务域），50 题由 400 卡+effective units 规则生成；",
            "问题由源文档概念/claim 模板生成，存在一定表面重叠（合成基线，非人工独立标注）；",
            "引用率 = 答案含 [1..5] 或命中上下文来源/概念；闭卷一致性 = 中文 bigram Jaccard；均为规则近似；",
            "生成阶段按限时抽样 ≤20 题（Lead 要求），检索指标为全量 50 题；",
            "本机 ollama 模型：bge-m3 / qwen3.5:9b；仅本地调用，不联网。",
        ],
        "results": results,
        "examples": sorted([r for r in results if r["generated"]],
                          key=lambda r: (-r["mrr"], -(r["consistency"] or 0)))[:5],
    }
    for r in results:
        r.pop("_hits", None)
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        lines = ["# RAG 基线评测（W6 · 2026-11-04）", "",
                 f"> 索引：{len(docs)} 文档（effective units + 400 卡）· bge-m3 · qwen3.5:9b",
                 f"> 问题：{len(questions)} 题；检索全量；生成 {len(gen_res)} 题（限时抽样）", "",
                 f"> **recall@5={agg['recall@5']} · recall@10={agg['recall@10']} · MRR={agg['mrr']} · "
                 f"引用率={agg['citation_rate']} · 一致性={agg['consistency_avg']} · 拒答率={agg['refusal_rate']}**",
                 f"> elapsed={payload['method']['elapsed_s']}s · sampled_note={sampled_note or '无（全量生成）'}", "",
                 "## 一、指标", "", "| 指标 | 值 | 目标阈值 |", "|---|---|---|",
                 f"| recall@5（全量 {n} 题） | {agg['recall@5']} | ≥0.85 |",
                 f"| recall@10（全量 {n} 题） | {agg['recall@10']} | — |",
                 f"| MRR（全量 {n} 题） | {agg['mrr']} | — |",
                 f"| 来源多样性@5 | {agg['diversity@5']} | — |",
                 f"| 引用率（生成 {len(gen_res)} 题） | {agg['citation_rate']} | ≥0.90 |",
                 f"| 闭卷一致性（生成 {len(gen_res)} 题） | {agg['consistency_avg']} | — |",
                 f"| 拒答率（生成 {len(gen_res)} 题） | {agg['refusal_rate']} | 记录 |", "",
                 "## 二、分题型", "", "| 类型 | n | recall@5 |", "|---|---|---|"]
        for t, v in agg["by_type"].items():
            lines.append(f"| {t} | {v['n']} | {v['recall@5']} |")
        lines += ["", "## 三、样例（生成题，按 MRR/一致性）", ""]
        for r in payload["examples"]:
            lines += [f"### {r['qid']} [{r['type']}] {r['question']}", "",
                      f"- gold: {r['gold_doc_ids']} · top5: {r['top_ids'][:5]}",
                      f"- recall@5={r['recall@5']} MRR={r['mrr']} 引用={r['citation']} 一致性={r['consistency']}",
                      f"- 回答：{(r['answer'] or '')[:200]}", ""]
        lines += ["## 四、结论与阈值建议", "",
                  f"- 基线：recall@5={agg['recall@5']}，recall@10={agg['recall@10']}，MRR={agg['mrr']}，引用率={agg['citation_rate']}；",
                  "- 建议与微调后对比阈值：**recall@5 ≥0.85、引用率 ≥0.90**；低于阈值优先补检索（混合检索/重排）与引用约束；",
                  "- 局限：合成问题、规则指标、held-out 105 未在工作区、生成按限时抽样（见 limitations）。", "",
                  "> 研究输出，不构成投资建议。", ""]
        OUT_MD.write_text("\n".join(lines), encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="RAG 基线评测（本地 bge-m3 + qwen3.5:9b）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--budget", type=int, default=600)
    parser.add_argument("--gen-limit", type=int, default=20)
    parser.add_argument("--questions", type=int, default=50)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate(write=args.write, n_questions=args.questions, budget_s=args.budget, gen_limit=args.gen_limit)
    a = payload["aggregate"]
    print(f"[rag_baseline] docs={len(payload['results'])}题 / recall@5={a['recall@5']} recall@10={a['recall@10']} "
          f"MRR={a['mrr']} 引用率={a['citation_rate']} 一致性={a['consistency_avg']} 拒答={a['refusal_rate']}")
    if payload.get("sampled_note"):
        print("  sample:", payload["sampled_note"])
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
