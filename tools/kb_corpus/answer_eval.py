# -*- coding: utf-8 -*-
"""W13 答案侧评测（task-171）：hybrid 检索 top5 + qwen3.5:9b 生成，40 题（含 10 不可答）。

复用：
  - W6 load_docs / embed_texts / chat（rag_baseline_eval.py）
  - W10 BM25 / rrf_fuse / cosine_top / tokenize / load_questions（rag_hybrid_eval.py）

评测集：30 道可答（取自 W6 50 题，按题型均衡）+ 10 道库外不可答问题。
检索：BM25 + bge-m3 向量 + RRF（k=60，1:1）top5。
生成：qwen3.5:9b（ollama /api/chat，think=false，temperature=0，num_predict=180），要求按资料回答+引用。
指标（确定性，不靠 LLM 裁判）：
  1) 引用正确率：答案 [n] 是否在上下文 top-k；可答题还要求引用命中 gold 文档；不可答要求正确拒答（不编引用）。
  2) groundedness：答案 CJK bigram 被 top5 上下文覆盖比例。
  3) gold 相似度：答案与 gold 原文 bigram F1（可答题）。
  4) 拒答率（不可答正确拒答率 / 可答过拒率）。
  5) 过度声明率：投资建议/绝对词（必涨/稳赚/保证…）。
  6) 长度异常：<15 或 >400 字。
产出：research/reports/answer_eval_20261104.{json,md}；SFT/DPO 目标阈值。
只读、本地 ollama、不写生产库；不 commit。
CLI::
    python tools/kb_corpus/answer_eval.py build --write [--budget 900]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
OUT_JSON = R / "answer_eval_20261104.json"
OUT_MD = R / "answer_eval_20261104.md"
W6 = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"
W10 = REPO / "tools" / "kb_corpus" / "rag_hybrid_eval.py"
BASE = R / "rag_baseline_20261104.json"
DATE = "20261104"
CITE = re.compile(r"\[([0-9]{1,2})\]")
CJK = re.compile(r"[\u4e00-\u9fff]+")
REFUSAL = ("资料未提及", "资料不足", "无法回答", "不知道", "未找到", "没有提到", "无法确定", "无法从资料")
OVERCLAIM = ("必涨", "必跌", "稳赚", "稳赢", "保证收益", "保证赚", "翻倍", "绝对", "100%", "一定涨",
             "必然上涨", "躺赚", "暴富", "满仓干", "梭哈")
LENGTH_MIN, LENGTH_MAX = 15, 400

UNABLE = [
    ("如何申请申根旅游签证？", "签证/出入境流程，库内无相关资料"),
    ("火星表面的主要矿物成分是什么？", "行星科学，库外"),
    ("如何制作一杯手冲咖啡？", "生活技能，库外"),
    ("2026 年世界杯的冠军是哪支球队？", "体育赛事，库外"),
    ("光合作用的卡尔文循环具体步骤？", "生物学，库外"),
    ("如何用 Rust 实现一个无锁队列？", "编程语言细节，库外"),
    ("高血压的一线用药有哪些？", "医学，库外"),
    ("唐朝安史之乱的起因是什么？", "历史，库外"),
    ("如何给婴儿添加辅食？", "育儿，库外"),
    ("量子纠缠实验的贝尔不等式推导？", "物理学，库外"),
]


def _load_mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


W6MOD = _load_mod("ans_w6", W6)
W10MOD = _load_mod("ans_w10", W10)


def bigrams(text: str) -> set[str]:
    chars = "".join(CJK.findall(str(text or "")))
    return {chars[i:i + 2] for i in range(max(0, len(chars) - 1))}


def cite_indices(answer: str) -> list[int]:
    return [int(x) for x in CITE.findall(str(answer or ""))]


def groundedness(answer: str, ctx_texts: list[str]) -> float:
    a = bigrams(answer)
    if not a:
        return 0.0
    ctx = bigrams(" ".join(ctx_texts))
    return round(len(a & ctx) / len(a), 4)


def gold_similarity(answer: str, gold_text: str) -> float:
    a, g = bigrams(answer), bigrams(gold_text)
    if not a or not g:
        return 0.0
    inter = len(a & g)
    if not inter:
        return 0.0
    p, r = inter / len(a), inter / len(g)
    return round(2 * p * r / (p + r), 4)


def is_refusal(answer: str) -> bool:
    return any(w in str(answer or "") for w in REFUSAL) or len(str(answer or "").strip()) < 8


def overclaim_hit(answer: str) -> bool:
    return any(w in str(answer or "") for w in OVERCLAIM)


def length_anomaly(answer: str) -> bool:
    n = len(str(answer or "").strip())
    return n < LENGTH_MIN or n > LENGTH_MAX


def build_questions(baseline: list[dict], n_answerable: int = 30) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    for q in baseline:
        by_type.setdefault(q["type"], []).append(q)
    quota = {"card_concept": 8, "card_evidence": 8, "unit_howto": 7, "unit_fact": 7}
    picked = []
    for t, k in quota.items():
        picked.extend(by_type.get(t, [])[:k])
    qs = []
    for i, q in enumerate(picked[:n_answerable]):
        qs.append({"qid": f"A{i+1:02d}", "question": q["question"], "type": q["type"],
                   "answerable": True, "gold_doc_ids": q["gold_doc_ids"], "gold_text": q.get("gold_text", "")})
    for j, (q, why) in enumerate(UNABLE):
        qs.append({"qid": f"U{j+1:02d}", "question": q, "type": "unanswerable", "answerable": False,
                   "gold_doc_ids": [], "gold_text": "", "unable_reason": why})
    return qs


def hybrid_retrieve(docs: list[dict], questions: list[dict], embed_fn=None, k: int = 60, topn: int = 5) -> list[list[dict]]:
    embed = embed_fn or W6MOD.embed_texts
    doc_texts = [d["text"] for d in docs]
    doc_vecs = embed(doc_texts)
    bm25 = W10MOD.BM25(doc_texts)
    out = []
    qvecs = embed([q["question"] for q in questions])
    for q, qvec in zip(questions, qvecs):
        v_idx = W10MOD.cosine_top(qvec, doc_vecs, docs, 100)
        b_idx = bm25.search(q["question"], 100)
        fused = W10MOD.rrf_fuse([b_idx, v_idx], [1.0, 1.0], k=k, topn=topn)
        out.append([docs[i] for i in fused])
    return out


def answer_prompt(question: str, ctx: list[dict]) -> str:
    return ("你是投研知识助手。仅依据下列资料回答，不要编造；用 2-4 句话回答，"
            "并在末尾用 [1][2] 标注所依据资料编号；若资料不足以回答，请明确说「资料不足/资料未提及」。\n\n"
            + "\n".join(f"[{i+1}] ({c['doc_id']}) {c['text'][:200]}" for i, c in enumerate(ctx))
            + f"\n\n问题：{question}\n回答：")


def judge_one(q: dict, ctx: list[dict], answer: str) -> dict:
    cited = cite_indices(answer)
    valid = [x for x in cited if 1 <= x <= len(ctx)]
    gold_ids = set(q["gold_doc_ids"])
    gold_cited = [x for x in valid if ctx[x - 1].get("doc_id") in gold_ids] if gold_ids else []
    ctx_texts = [c["text"] for c in ctx]
    refused = is_refusal(answer)
    if q["answerable"]:
        citation_ok = bool(gold_cited) and len(valid) == len(cited) and not refused
    else:
        citation_ok = refused and not cited  # 不可答：正确拒答且不编造引用
    return {
        "qid": q["qid"], "type": q["type"], "answerable": q["answerable"],
        "question": q["question"], "answer": answer,
        "cited": cited, "citation_valid": len(valid) == len(cited),
        "citation_gold": [ctx[x - 1]["doc_id"] for x in gold_cited],
        "citation_ok": bool(citation_ok),
        "groundedness": groundedness(answer, ctx_texts),
        "gold_similarity": gold_similarity(answer, q.get("gold_text", "")) if q["answerable"] else None,
        "refused": refused, "overclaim": overclaim_hit(answer),
        "length": len(answer.strip()), "length_anomaly": length_anomaly(answer),
        "ctx_ids": [c["doc_id"] for c in ctx],
    }


def evaluate(*, write: bool = False, budget_s: int = 900, n_answerable: int = 30,
             embed_fn=None, chat_fn=None, docs: list[dict] | None = None,
             questions: list[dict] | None = None) -> dict:
    t0 = time.monotonic()
    docs = docs if docs is not None else W6MOD.load_docs()
    baseline = W10MOD.load_questions()
    questions = questions if questions is not None else build_questions(baseline, n_answerable)
    embed = embed_fn or W6MOD.embed_texts
    chat = chat_fn or W6MOD.chat
    # W6 结果未存 gold_text：从文档库按 gold_doc_id 回填（保证 gold 相似度可算）
    by_id = {d["doc_id"]: d.get("text") or "" for d in docs}
    for q in questions:
        if q.get("answerable") and not q.get("gold_text") and q.get("gold_doc_ids"):
            q["gold_text"] = by_id.get(q["gold_doc_ids"][0], "")
    contexts = hybrid_retrieve(docs, questions, embed_fn=embed)
    results, sampled_note = [], None
    gen_start = time.monotonic()
    for q, ctx in zip(questions, contexts):
        if time.monotonic() - gen_start > budget_s:
            sampled_note = (f"生成阶段超预算 {budget_s}s：已生成 {len(results)}/{len(questions)} 题后停止"
                            f"（检索 40 题全量完成；抽样 {len(results)} 题并注明）")
            break
        try:
            answer = chat(answer_prompt(q["question"], ctx))
        except Exception as exc:  # noqa: BLE001
            answer = ""
            results.append({**judge_one(q, ctx, answer), "error": f"{type(exc).__name__}: {exc}"})
            continue
        results.append(judge_one(q, ctx, answer))
    n = len(results)
    ans = [r for r in results if r["answerable"]]
    unans = [r for r in results if not r["answerable"]]
    agg = {
        "questions_total": len(questions), "evaluated": n,
        "generation_evaluated": n,
        "citation_accuracy": round(sum(1 for r in results if r["citation_ok"]) / max(1, n), 4),
        "citation_valid_rate": round(sum(1 for r in results if r["citation_valid"]) / max(1, n), 4),
        "groundedness": round(sum(r["groundedness"] for r in results) / max(1, n), 4),
        "gold_similarity": round(sum(r["gold_similarity"] for r in ans if r["gold_similarity"] is not None) / max(1, len(ans)), 4),
        "refusal_rate": round(sum(1 for r in results if r["refused"]) / max(1, n), 4),
        "correct_refusal_rate": round(sum(1 for r in unans if r["refused"]) / max(1, len(unans)), 4),
        "over_refusal_rate": round(sum(1 for r in ans if r["refused"]) / max(1, len(ans)), 4),
        "overclaim_rate": round(sum(1 for r in results if r["overclaim"]) / max(1, n), 4),
        "length_anomaly_rate": round(sum(1 for r in results if r["length_anomaly"]) / max(1, n), 4),
        "avg_answer_len": round(sum(r["length"] for r in results) / max(1, n), 1),
        "answerable_n": len(ans), "unanswerable_n": len(unans),
    }
    targets = {"citation_accuracy": 0.90, "groundedness": 0.60, "correct_refusal_rate": 0.80,
               "gold_similarity": 0.30, "overclaim_rate": 0.05, "length_anomaly_rate": 0.10}
    halluc = [r for r in results if not r["refused"] and r["groundedness"] < 0.3][:8]
    payload = {
        "schema_version": "answer_eval.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W13-答案侧评测（hybrid top5 + qwen3.5:9b，确定性指标）",
        "dataset": {"total": len(questions), "answerable": sum(1 for q in questions if q["answerable"]),
                    "unanswerable": sum(1 for q in questions if not q["answerable"]),
                    "answerable_source": "W6 50 题（按题型均衡取 30）",
                    "unanswerable_items": [{"qid": q["qid"], "question": q["question"]} for q in questions if not q["answerable"]]},
        "method": {
            "retrieval": "hybrid：BM25 + bge-m3 向量 + RRF（k=60，1:1）top5",
            "generation": "qwen3.5:9b /api/chat，think=false，temperature=0，num_predict=180，要求引用/可拒答",
            "metrics": "确定性：引用正确率/groundedness(答案bigram被上下文覆盖)/gold bigram F1/拒答/过度声明/长度异常",
            "budget_s": budget_s, "elapsed_s": round(time.monotonic() - t0, 1),
        },
        "sources": {"baseline": str(BASE), "baseline_sha256": hashlib.sha256(BASE.read_bytes()).hexdigest() if BASE.is_file() else None,
                    "docs": len(docs)},
        "aggregate": agg,
        "targets": targets,
        "target_notes": "SFT/DPO 目标：引用正确率≥0.90、groundedness≥0.60、不可答拒答率≥0.80；过度声明≤0.05、长度异常≤0.10",
        "sampled_note": sampled_note,
        "results": results,
        "hallucination_samples": [{"qid": r["qid"], "question": r["question"], "answer": r["answer"][:160],
                                   "groundedness": r["groundedness"]} for r in halluc],
        "limitations": [
            "评测集 30 可答来自 W6 合成问题（源文档模板生成，非人工独立标注）；10 不可答为人工库外问题；",
            "指标为确定性规则（bigram 覆盖/引用解析/词典），非 LLM 裁判，存在近似误差；",
            "单次生成基线（无重试/重排），用于与 SFT 后同口径对比；",
            "只读知识库、本地 ollama；未写生产数据。",
        ],
        "disclaimer": "研究输出，不构成投资建议。",
    }
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        a = agg
        lines = ["# 答案侧评测基线（W13 · 2026-11-04）", "",
                 f"> 数据集：{a['evaluated']} 题（可答 {a['answerable_n']} + 不可答 {a['unanswerable_n']}）· hybrid top5 + qwen3.5:9b",
                 f"> **引用正确率={a['citation_accuracy']} · groundedness={a['groundedness']} · 不可答拒答率={a['correct_refusal_rate']}**",
                 f"> gold 相似度={a['gold_similarity']} · 过度声明率={a['overclaim_rate']} · 长度异常率={a['length_anomaly_rate']} · 平均长度={a['avg_answer_len']}",
                 f"> sampled_note={sampled_note or '无（40 题全量生成）'}", "",
                 "## 一、核心指标与 SFT 目标", "", "| 指标 | 当前基线 | SFT/DPO 目标 | 达标 |", "|---|---|---|---|"]
        for k, v in targets.items():
            cur = agg.get(k)
            ok = (cur is not None) and ((cur >= v) if k in ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity") else (cur <= v))
            lines.append(f"| {k} | {cur} | {'≥' if k in ('citation_accuracy','groundedness','correct_refusal_rate','gold_similarity') else '≤'} {v} | {'✅' if ok else '❌'} |")
        lines += ["", "## 二、细分", "",
                  f"- 引用格式合法率：{a['citation_valid_rate']}；拒答率（全体）：{a['refusal_rate']}；可答过拒率：{a['over_refusal_rate']}",
                  f"- 生成耗时：{payload['method']['elapsed_s']}s；文档数：{len(docs)}", "",
                  "## 三、幻觉/低 groundedness 样例（未拒答且 groundedness<0.3）", ""]
        for s in payload["hallucination_samples"][:5]:
            lines += [f"### {s['qid']} {s['question']}", f"- groundedness={s['groundedness']}", f"- 回答：{s['answer']}", ""]
        lines += ["## 四、结论与建议", "",
                  f"- 基线整体：引用正确率 {a['citation_accuracy']}、groundedness {a['groundedness']}、不可答拒答率 {a['correct_refusal_rate']}；",
                  "- SFT 目标阈值：引用正确率 ≥0.90、groundedness ≥0.60、不可答拒答率 ≥0.80、gold 相似度 ≥0.30、过度声明 ≤0.05、长度异常 ≤0.10；",
                  "- 若拒答率偏高/引用格式缺失，优先在训练数据中加入「资料不足→拒答」与「句末引用」样本。",
                  "", f"> {payload['disclaimer']}", ""]
        OUT_MD.write_text("\n".join(lines), encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="答案侧评测（hybrid top5 + qwen3.5:9b，确定性指标）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--budget", type=int, default=900)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate(write=args.write, budget_s=args.budget)
    a = payload["aggregate"]
    print(f"[answer_eval] {a['evaluated']}题（可答{a['answerable_n']}/不可答{a['unanswerable_n']}）")
    print(f"  引用正确率={a['citation_accuracy']} | groundedness={a['groundedness']} | "
          f"拒答率={a['correct_refusal_rate']}（过拒={a['over_refusal_rate']}）| gold相似={a['gold_similarity']} | "
          f"过度声明={a['overclaim_rate']} | 长度异常={a['length_anomaly_rate']} | elapsed={payload['method']['elapsed_s']}s")
    if payload.get("sampled_note"):
        print("  sample:", payload["sampled_note"])
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
