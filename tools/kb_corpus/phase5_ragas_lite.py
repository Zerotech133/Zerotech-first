# -*- coding: utf-8 -*-
"""Phase 5 · RAGAS 风格评测（免安装：本地 qwen3.5 作 judge）。

指标（对齐 RAGAS 定义）：
- faithfulness：答案中的关键声明是否被资料支持（0/1，平均）
- answer_relevancy：答案与问题相关性（1-5 归一）
- context_precision：检索到的 top5 片段中与问题相关的比例（0/1，平均）
- context_recall（近似）：期望关键词是否在检索上下文中出现（0/1，平均）

抽样 15 条 golden，产出 90_报告/phase5_ragas_lite.{json,md}
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

KB = Path(r"C:/项目/知识库")
REP = KB / "90_报告"
sys.path.insert(0, str(Path(r"C:/项目/老田的AI助手/tools/kb_corpus")))
import rag_eval as RE  # noqa: E402
try:
    import phase5_full_eval as P5
    GOLD = RE.GOLD + P5.EXTRA
except Exception:  # noqa: BLE001
    GOLD = RE.GOLD

SAMPLE = GOLD[:15]


def chat(prompt: str, num=300) -> str:
    body = {"model": "qwen3.5:9b", "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.0, "num_predict": num}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return ((json.loads(r.read().decode()).get("message") or {}).get("content") or "").strip()


def judge_yes_no(prompt: str) -> int:
    ans = chat(prompt, 40)
    return 1 if ("是" in ans and "否" not in ans[:6]) or ans.strip().upper().startswith("YES") else 0


def main() -> int:
    docs = json.loads((RE.PACKED / "docs.json").read_text(encoding="utf-8"))
    mat = np.load(RE.PACKED / "emb_norm.f32.npy")
    bm25 = RE.BM25([d.get("title", "") + " " + d.get("text", "") for d in docs])
    rows = []
    for q, keys in SAMPLE:
        vec = RE.embed(q + " " + " ".join(keys))
        vs = mat @ vec
        bs = bm25.score(q + " " + " ".join(keys))
        vr = np.argsort(-vs); br = np.argsort(-bs)
        vrank = np.empty(len(vs), dtype=np.float32); brank = np.empty(len(bs), dtype=np.float32)
        vrank[vr] = np.arange(len(vs)); brank[br] = np.arange(len(bs))
        fused = -(0.7 / (60.0 + vrank) + 0.3 / (60.0 + brank))
        top5 = np.argsort(fused)[:5]
        ctxs = [docs[int(i)].get("text", "")[:600] for i in top5]
        ctx = "\n\n".join(f"[S{i+1}] {c}" for i, c in enumerate(ctxs))
        ans = chat(f"仅根据资料回答，中文，200字内，标注 [S1]-[S5]；资料不足说“资料未覆盖”。\n\n资料：\n{ctx}\n\n问题：{q}\n回答：", 400)
        # faithfulness：声明支持检查
        faith = judge_yes_no(f"资料是否支持下面回答的主要结论？只回答“是”或“否”。\n资料：{ctx[:1200]}\n回答：{ans[:600]}")
        # answer relevancy 1-5
        ar_raw = chat(f"给下面回答与问题的相关性打 1-5 分（只输出数字）。问题：{q}\n回答：{ans[:500]}", 10)
        m = re.search(r"[1-5]", ar_raw)
        ar = (int(m.group(0)) - 1) / 4 if m else 0.5
        # context precision：逐块判断
        prec = []
        for c in ctxs:
            prec.append(judge_yes_no(f"该资料片段是否与问题相关？只回答“是”或“否”。问题：{q}\n片段：{c[:500]}"))
        # context recall（关键词近似）
        joined = " ".join(ctxs).lower()
        recall = 1 if any(k.lower() in joined for k in keys) else 0
        rows.append({"query": q, "faithfulness": faith, "answer_relevancy": round(ar, 3),
                     "context_precision": round(float(np.mean(prec)), 3), "context_recall": recall,
                     "answer_head": ans[:100]})
        print("evoked:", q, rows[-1]["faithfulness"], rows[-1]["answer_relevancy"], rows[-1]["context_precision"], flush=True)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "sample": len(rows),
           "faithfulness": round(float(np.mean([r["faithfulness"] for r in rows])), 3),
           "answer_relevancy": round(float(np.mean([r["answer_relevancy"] for r in rows])), 3),
           "context_precision": round(float(np.mean([r["context_precision"] for r in rows])), 3),
           "context_recall": round(float(np.mean([r["context_recall"] for r in rows])), 3),
           "rows": rows}
    (REP / "phase5_ragas_lite.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# Phase 5 · RAGAS 风格评测（llm judge）", "",
         f"- 样本 {len(rows)} 条 golden；judge=qwen3.5:9b（本地）", "",
         "| 指标 | 值 | 目标 |", "|---|---|---|",
         f"| faithfulness | **{rep['faithfulness']}** | ≥0.9（参考） |",
         f"| answer_relevancy | {rep['answer_relevancy']} | ≥0.8（参考） |",
         f"| context_precision | {rep['context_precision']} | ≥0.7（参考） |",
         f"| context_recall（关键词近似） | {rep['context_recall']} | ≥0.6 |", "",
         "| 查询 | faith | relevancy | precision | recall |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['query'][:18]} | {r['faithfulness']} | {r['answer_relevancy']} | {r['context_precision']} | {r['context_recall']} |")
    L += ["", "## 说明", "- 免安装实现（对齐 RAGAS 四项定义，LLM judge 二值/1-5 打分）；",
          "- faithfulness 为「整体结论是否被资料支持」的近似，非逐句 claim 分解；",
          "- context_precision 逐块 judged；context_recall 为关键词近似，非 LLM 生成对照。"]
    (REP / "phase5_ragas_lite.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("sample", "faithfulness", "answer_relevancy", "context_precision", "context_recall")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
