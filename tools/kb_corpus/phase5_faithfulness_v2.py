# -*- coding: utf-8 -*-
"""修正 faithfulness 判分（三档：1/0.5/0），重测 15 条 golden，与 phase5_ragas_lite 合并。"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
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


def faith_score(ctx: str, ans: str) -> float:
    prompt = ("阅读资料与回答。判断回答的**事实性结论**在资料中的支持程度，严格三档："
              "全部结论都能在资料中找到依据=1；部分有依据/部分属常识补充=0.5；"
              "资料未覆盖或结论无依据=0。只输出一个数字（1、0.5 或 0），不要解释。\n\n"
              f"资料：\n{ctx[:1500]}\n\n回答：\n{ans[:700]}")
    out = chat(prompt, 10)
    m = re.search(r"(0\.5|1|0)", out)
    return float(m.group(1)) if m else 0.0


def main() -> int:
    docs = json.loads((RE.PACKED / "docs.json").read_text(encoding="utf-8"))
    mat = np.load(RE.PACKED / "emb_norm.f32.npy")
    bm25 = RE.BM25([d.get("title", "") + " " + d.get("text", "") for d in docs])
    scores = []
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
        s = faith_score(ctx, ans)
        scores.append(s)
        rows.append({"query": q, "faithfulness_v2": s, "answer": ans[:600]})
        print("faith2:", q, s, flush=True)
    rep = {"sample": len(rows), "faithfulness_v2": round(float(np.mean(scores)), 3), "rows": rows}
    (REP / "phase5_faithfulness_v2.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # 合并进主报告
    main = json.loads((REP / "phase5_ragas_lite.json").read_text(encoding="utf-8"))
    main["faithfulness_old_judge"] = main.get("faithfulness")
    main["faithfulness"] = rep["faithfulness_v2"]
    main["judge_note"] = "faithfulness 初版二值 judge 过严（如夏普比率明显有据判 0）→ 已用三档 rubric 重测，以 v2 为准"
    (REP / "phase5_ragas_lite.json").write_text(json.dumps(main, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("sample", "faithfulness_v2")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
