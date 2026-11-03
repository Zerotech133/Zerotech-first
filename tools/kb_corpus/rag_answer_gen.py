# -*- coding: utf-8 -*-
"""W16 RAG 生成侧优化 A/B/C 对比（task-176）：不改模型，只改生成策略。

复用：
  - W13 answer_eval.py：40 题评测集（30 可答+10 不可答）、hybrid_retrieve、judge_one、指标口径；
  - W6/W10：bge-m3 检索、qwen3.5:9b（ollama /api/chat）生成。

三种策略（同模型 qwen3.5:9b，think=false，temp=0）：
  A 原 prompt（W13 基线，2-4 句 + 句末 [n] + 可拒答）；
  B 强化 prompt（仅用资料/每个事实句末引用/资料不足明确拒答/不扩写/无投资建议/≤180 字）；
  C B + 证据摘录（先从 top5 上下文按查询相关度抽 1-5 句证据，再依据证据回答并引用 [n]）。

指标：引用正确率/groundedness/gold 相似度/拒答率（不可答正确拒答、可答过拒）/过度声明/长度异常。
产出：research/reports/rag_gen_improve_20261104.{json,md}。
只读、本地 ollama、不写生产库；不 commit。
CLI::
    python tools/kb_corpus/rag_answer_gen.py build --write [--budget 900]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
OUT_JSON = R / "rag_gen_improve_20261104.json"
OUT_MD = R / "rag_gen_improve_20261104.md"
W13 = REPO / "tools" / "kb_corpus" / "answer_eval.py"
DATE = "20261104"
CJK = re.compile(r"[\u4e00-\u9fff]+")
SENT = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
STRATEGIES = ("A", "B", "C")
WORKERS = 6


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AE = _load("rag_gen_w13", W13)


def bigrams(text: str) -> set[str]:
    chars = "".join(CJK.findall(str(text or "")))
    return {chars[i:i + 2] for i in range(max(0, len(chars) - 1))}


# ---------------------------------------------------------------------------
# Prompt 策略
# ---------------------------------------------------------------------------
BASE_RULES = ("你是投研知识助手。只能使用下面编号资料回答，禁止使用任何外部知识或推测；"
              "用 2-4 句话回答；每个事实句末用 [n] 标注资料编号；"
              "资料不足以回答时必须明确说「资料不足/资料未提及」；不得给出投资建议或收益承诺；答案不超过 180 字。")
FEWSHOT = ("示例：资料：[1] 均线粘合度衡量多条均线收敛程度。[2] 收敛后常伴随方向选择。\n"
           "问题：均线粘合度的作用？\n答：均线粘合度衡量多条均线收敛程度 [1]；收敛后常伴随方向选择 [2]。")


def build_prompt(strategy: str, question: str, contexts: list[dict]) -> str:
    if strategy == "A":
        return AE.answer_prompt(question, contexts)
    ev = extract_evidence(question, contexts) if strategy == "C" else None
    lines = [BASE_RULES, FEWSHOT, ""]
    if strategy == "C" and ev:
        lines.append("证据摘录（按相关度排序，引用编号即资料编号）：")
        lines += [f"[{e['n']}] {e['text']}" for e in ev]
        lines.append("")
        lines.append("仅依据以上证据回答；每个事实句末引用对应编号。")
    lines.append("资料：")
    lines += [f"[{i+1}] {c['text'][:220]}" for i, c in enumerate(contexts)]
    lines += ["", f"问题：{question}", "回答："]
    return "\n".join(lines)


def extract_evidence(question: str, contexts: list[dict], max_items: int = 5) -> list[dict]:
    """从 top5 上下文抽取与问题最相关的证据句（规则：问句 bigram 覆盖 + 句长）。"""
    qb = bigrams(question)
    cands = []
    for i, c in enumerate(contexts, 1):
        for sent in SENT.findall(str(c.get("text") or "")):
            s = sent.strip()
            if len(s) < 8:
                continue
            sb = bigrams(s)
            score = len(qb & sb) / max(1, len(qb)) + 0.001 * len(sb)
            cands.append({"n": i, "text": s[:160], "score": round(score, 4)})
    cands.sort(key=lambda x: -x["score"])
    out, seen_n = [], {}
    for c in cands:
        if len(out) >= max_items:
            break
        if seen_n.get(c["n"], 0) >= 1:
            continue
        seen_n[c["n"]] = seen_n.get(c["n"], 0) + 1
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 指标聚合（复用 W13 judge_one 结果）
# ---------------------------------------------------------------------------
def aggregate(results: list[dict]) -> dict:
    n = max(1, len(results))
    ans = [r for r in results if r["answerable"]]
    unans = [r for r in results if not r["answerable"]]
    return {
        "evaluated": len(results),
        "citation_accuracy": round(sum(1 for r in results if r["citation_ok"]) / n, 4),
        "citation_valid_rate": round(sum(1 for r in results if r["citation_valid"]) / n, 4),
        "groundedness": round(sum(r["groundedness"] for r in results) / n, 4),
        "gold_similarity": round(sum(r["gold_similarity"] for r in ans if r["gold_similarity"] is not None) / max(1, len(ans)), 4),
        "refusal_rate": round(sum(1 for r in results if r["refused"]) / n, 4),
        "correct_refusal_rate": round(sum(1 for r in unans if r["refused"]) / max(1, len(unans)), 4),
        "over_refusal_rate": round(sum(1 for r in ans if r["refused"]) / max(1, len(ans)), 4),
        "overclaim_rate": round(sum(1 for r in results if r["overclaim"]) / n, 4),
        "length_anomaly_rate": round(sum(1 for r in results if r["length_anomaly"]) / n, 4),
        "avg_answer_len": round(sum(r["length"] for r in results) / n, 1),
        "answerable_n": len(ans), "unanswerable_n": len(unans),
    }


def evaluate(*, write: bool = False, budget_s: int = 900, strategies: tuple[str, ...] = STRATEGIES,
             embed_fn=None, chat_fn=None, docs: list[dict] | None = None,
             questions: list[dict] | None = None) -> dict:
    t0 = time.monotonic()
    docs = docs if docs is not None else AE.W6MOD.load_docs()
    if questions is None:
        questions = AE.build_questions(AE.W10MOD.load_questions(), 30)
    embed = embed_fn or AE.W6MOD.embed_texts
    chat = chat_fn or AE.W6MOD.chat
    contexts = AE.hybrid_retrieve(docs, questions, embed_fn=embed)
    results: dict[str, list[dict]] = {s: [] for s in strategies}
    sampled_note = None
    deadline = time.monotonic() + budget_s
    for s in strategies:
        prompts = [build_prompt(s, q["question"], ctx) for q, ctx in zip(questions, contexts)]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(chat, p) for p in prompts]
            for q, ctx, fut in zip(questions, contexts, futs):
                if time.monotonic() > deadline:
                    sampled_note = f"策略 {s} 生成超预算 {budget_s}s，已生成 {len(results[s])}/{len(questions)} 题（其他策略不受影响）"
                    break
                try:
                    answer = fut.result()
                except Exception as exc:  # noqa: BLE001
                    answer = ""
                    results[s].append({**AE.judge_one(q, ctx, answer), "error": f"{type(exc).__name__}: {exc}"})
                    continue
                results[s].append(AE.judge_one(q, ctx, answer))
        if len(results[s]) < len(questions):
            # 未生成的题目按空答案补齐（拒绝/0 分），保证三策略同题对比
            for q, ctx in list(zip(questions, contexts))[len(results[s]):]:
                results[s].append(AE.judge_one(q, ctx, ""))
    aggs = {s: aggregate(results[s]) for s in strategies}
    deltas = {}
    if "A" in aggs:
        for s in strategies:
            if s == "A":
                continue
            deltas[f"{s}-A"] = {
                k: round(aggs[s][k] - aggs["A"][k], 4)
                for k in ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity", "avg_answer_len")
            }
    best = max(strategies, key=lambda s: (aggs[s]["citation_accuracy"], aggs[s]["groundedness"]))
    payload = {
        "schema_version": "rag_gen_improve.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W16-RAG 生成侧优化：A/B/C 生成策略对比（同模型 qwen3.5:9b，不改模型只改 prompt）",
        "method": {
            "retrieval": "hybrid：BM25 + bge-m3 + RRF（k=60，1:1）top5（复用 W13）",
            "generation": "qwen3.5:9b /api/chat，think=false，temperature=0，num_predict=220",
            "dataset": "W13 40 题（30 可答 + 10 库外不可答）",
            "strategies": {
                "A": "原 prompt（W13 基线）",
                "B": "强化 prompt：仅用资料/每句末引用/资料不足拒答/不扩写/无投资建议/≤180字 + few-shot",
                "C": "B + 证据摘录（先从 top5 抽取相关证据句，再依据证据回答并引用）",
            },
            "elapsed_s": round(time.monotonic() - t0, 1), "budget_s": budget_s,
        },
        "aggregate": aggs,
        "deltas_vs_A": deltas,
        "best": best,
        "targets": {"citation_accuracy": 0.90, "groundedness": 0.60, "correct_refusal_rate": 0.80},
        "sampled_note": sampled_note,
        "samples": {s: [{"qid": r["qid"], "question": r["question"], "answer": r["answer"][:200],
                         "groundedness": r["groundedness"], "citation_ok": r["citation_ok"]}
                        for r in results[s][:2]] for s in strategies},
        "conclusion": "",
        "limitations": [
            "评测集为 W13 合成 40 题（30 可答来自 W6 模板问题，10 不可答为人工库外问题）；",
            "指标为确定性规则（bigram/引用解析/词典），非 LLM 裁判；",
            "单地区单次生成（无重试/集成），qwen3.5:9b 本地；C 策略证据摘录为规则抽取；",
            "只读知识库、本地 ollama；不写生产数据。",
        ],
        "disclaimer": "研究输出，不构成投资建议。",
    }
    payload["conclusion"] = (
        f"最优策略 {best}：引用正确率 {aggs[best]['citation_accuracy']}（A 基线 {aggs.get('A', {}).get('citation_accuracy')}），"
        f"groundedness {aggs[best]['groundedness']}（A {aggs.get('A', {}).get('groundedness')}），"
        f"不可答拒答率 {aggs[best]['correct_refusal_rate']}；"
        f"B/A 提升：引用 {deltas.get('B-A', {}).get('citation_accuracy')}、groundedness {deltas.get('B-A', {}).get('groundedness')}；"
        f"C/A 提升：引用 {deltas.get('C-A', {}).get('citation_accuracy')}、groundedness {deltas.get('C-A', {}).get('groundedness')}。")
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        lines = ["# RAG 生成侧优化 A/B/C 对比（W16 · 2026-11-04）", "",
                 f"> 数据集：W13 40 题（30 可答 + 10 不可答）· hybrid top5 · qwen3.5:9b（同模型，只改生成策略）", "",
                 "## 一、三策略对比", "", "| 指标 | A（基线） | B（强化 prompt） | C（B+证据摘录） | B-A | C-A |", "|---|---|---|---|---|---|"]
        rows = ["citation_accuracy", "groundedness", "correct_refusal_rate", "over_refusal_rate",
                "gold_similarity", "overclaim_rate", "length_anomaly_rate", "avg_answer_len"]
        for k in rows:
            a = aggs.get("A", {}).get(k)
            b = aggs.get("B", {}).get(k)
            c = aggs.get("C", {}).get(k)
            db = deltas.get("B-A", {}).get(k, "—") if k in deltas.get("B-A", {}) else "—"
            dc = deltas.get("C-A", {}).get(k, "—") if k in deltas.get("C-A", {}) else "—"
            lines.append(f"| {k} | {a} | {b} | {c} | {db} | {dc} |")
        lines += ["", f"- 最优策略：**{best}**", f"- {payload['conclusion']}",
                  f"- SFT/DPO 目标：引用正确率 ≥0.90、groundedness ≥0.60、不可答拒答率 ≥0.80；",
                  f"- sampled_note：{sampled_note or '无（三策略均全量 40 题）'}",
                  "", "## 二、策略定义", "",
                  "- **A**：W13 原 prompt（2-4 句 + 句末 [n] + 可拒答）；",
                  "- **B**：强化 prompt —— 仅用资料/每个事实句末引用/资料不足明确拒答/不扩写/无投资建议/≤180 字 + few-shot；",
                  "- **C**：B + 证据摘录 —— 先从 top5 按查询相关度抽证据句（≤5 条，带编号），再依据证据回答并引用。",
                  "", "## 三、样例（每策略前 2 题）", ""]
        for s in strategies:
            lines += [f"### 策略 {s}", ""]
            for smp in payload["samples"][s]:
                lines += [f"- **{smp['qid']}** {smp['question']}", f"  - 回答：{smp['answer']}",
                          f"  - groundedness={smp['groundedness']} · 引用正确={smp['citation_ok']}", ""]
        lines += ["## 四、结论与推荐", "",
                  f"- {payload['conclusion']}",
                  "- 推荐：若 B/C 中引用正确率与 groundedness 同时提升且拒答不降，优先采用该策略作为生成默认 prompt（无需改模型）；",
                  "- 局限：" + "；".join(payload["limitations"][:3]),
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
    parser = argparse.ArgumentParser(description="RAG 生成侧优化 A/B/C 对比")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--budget", type=int, default=900)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    payload = evaluate(write=args.write, budget_s=args.budget)
    print(f"[rag_answer_gen] 40题 × A/B/C | best={payload['best']}")
    for s in STRATEGIES:
        a = payload["aggregate"][s]
        print(f"  {s}: 引用正确率={a['citation_accuracy']} groundedness={a['groundedness']} "
              f"拒答={a['correct_refusal_rate']} 过拒={a['over_refusal_rate']} 过度声明={a['overclaim_rate']} "
              f"长度异常={a['length_anomaly_rate']} avg_len={a['avg_answer_len']}")
    print("  deltas:", json.dumps(payload["deltas_vs_A"], ensure_ascii=False))
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
