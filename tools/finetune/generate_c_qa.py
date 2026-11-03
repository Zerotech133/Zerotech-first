# -*- coding: utf-8 -*-
"""D0-C（并行版）：云端 DeepSeek 从干净单元生成「阅读理解 QA」，4 并发、更大输出预算。"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"C:\项目\老田的AI助手\agent_platform")
from engine.deepseek import DeepSeekClient  # type: ignore

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "research/finetune"
IN = OUT / "pending" / "c_prompts_pending.jsonl"
DST = OUT / "sft" / "c_qa.jsonl"
STAMP = datetime.now().strftime("%Y%m%d")

SYSTEM = ("你是量化知识问答生成器。根据给定知识单元生成 1 个问答对，答案必须完全来自原文事实，"
          "不得引入外部知识，不得给出投资建议。输出 JSON：{\"items\":[{\"i\":0,\"q\":\"...\",\"a\":\"...\",\"concept\":\"...\"}]}。"
          "q 不超过 40 字；a 不超过 200 字；concept 为 4-16 字名词短语。")

prompts = [json.loads(line) for line in IN.open(encoding="utf-8") if line.strip()]
print("pending prompts:", len(prompts), flush=True)
B = 5
CLIENT = DeepSeekClient()


def bigrams(t: str) -> set:
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", t or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def overlap_ok(answer: str, source: str) -> float:
    A, S = bigrams(answer), bigrams(source)
    return len(A & S) / len(A) if A else 0.0


def one_batch(args):
    start, chunk = args
    user = "为以下每个编号的知识单元各生成 1 个问答对（编号与 items 的 i 对应）：\n\n" + "\n\n".join(
        f"[{j}] {it['text'][:700]}" for j, it in enumerate(chunk))
    obj = None
    for _ in range(2):
        try:
            raw = CLIENT.chat([{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": user}],
                              temperature=0.2, max_tokens=3200,
                              response_format={"type": "json_object"})
            obj = json.loads(raw)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[start {start}] retry: {type(exc).__name__} {str(exc)[:70]}", flush=True)
    out = []
    if not obj:
        return out
    for item in obj.get("items") or []:
        try:
            i = int(item.get("i", -1))
            q = str(item.get("q") or "").strip()
            a = str(item.get("a") or "").strip()
            concept = str(item.get("concept") or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not (0 <= i < len(chunk)) or not q or not a:
            continue
        src = chunk[i]
        if overlap_ok(a, src["text"]) < 0.55:
            continue
        out.append({"unit_id": src["unit_id"], "q": q, "a": a,
                    "concept": concept or q[:12],
                    "source": f"{src['source_file']} L{src['source_line']}",
                    "overlap": round(overlap_ok(a, src["text"]), 3)})
    return out


batches = [(s, prompts[s:s + B]) for s in range(0, len(prompts), B)]
results, done = [], 0
with ThreadPoolExecutor(max_workers=4) as ex:
    for outs in ex.map(one_batch, batches):
        results.extend(outs)
        done += 1
        if done % 20 == 0:
            print(f"progress batches {done}/{len(batches)} qa={len(results)}", flush=True)

# 去重 q
seen, final = set(), []
for r in results:
    if r["q"] in seen:
        continue
    seen.add(r["q"])
    final.append(r)
DST.parent.mkdir(parents=True, exist_ok=True)
with DST.open("w", encoding="utf-8") as f:
    for r in final:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
          "model": "deepseek-flash", "pending": len(prompts), "generated": len(final),
          "batches": len(batches), "workers": 4}
(OUT / f"c_generation_report_{STAMP}.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
