# -*- coding: utf-8 -*-
"""Phase 4 增强验证：LLM rerank（无模型下载）对 Recall@5 的增益（抽样 12 条）。"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np

# 回环禁代理：Ollama 在 127.0.0.1:11434，系统代理会接管回环请求（见 tools/safe_urlopen.py）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

KB = Path(r"C:/项目/知识库")
REP = KB / "90_报告"
sys.path.insert(0, str(Path(r"C:/项目/老田的AI助手/tools/kb_corpus")))
import rag_eval as RE  # noqa: E402

SAMPLE = RE.GOLD[:12]


def chat_rank(query: str, cands: list[str]) -> list[int]:
    lines = "\n".join(f"{i}. {t[:200]}" for i, t in enumerate(cands))
    prompt = ("按与问题的相关性给下面候选排序。只输出数字序号数组（JSON，如 [3,1,0]），不要解释。\n"
              f"问题：{query}\n候选：\n{lines}")
    body = {"model": "qwen3.5:9b", "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.0, "num_predict": 120}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with safe_urlopen(req, timeout=180) as r:
        ans = ((json.loads(r.read().decode()).get("message") or {}).get("content") or "")
    m = re.search(r"\[[\d,\s]+\]", ans)
    if not m:
        return list(range(len(cands)))
    order = [int(x) for x in re.findall(r"\d+", m.group(0))]
    seen = [x for x in order if 0 <= x < len(cands)]
    return seen + [x for x in range(len(cands)) if x not in seen]


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
        top20 = np.argsort(fused)[:20]
        cands = [(docs[int(i)].get("title", "") + " " + docs[int(i)].get("text", "")[:150]) for i in top20]
        order = chat_rank(q, cands)
        reranked = [int(top20[i]) for i in order[:5]]

        def hit(di):
            t = (docs[int(di)].get("title", "") + " " + docs[int(di)].get("text", "")).lower()
            return any(k.lower() in t for k in keys)

        base_top5 = [int(i) for i in top20[:5]]
        rows.append({"query": q, "before_recall5": sum(hit(i) for i in base_top5) / 5,
                     "after_recall5": sum(hit(i) for i in reranked) / 5})
        print("rerank:", q, rows[-1], flush=True)
    avg_b = float(np.mean([r["before_recall5"] for r in rows]))
    avg_a = float(np.mean([r["after_recall5"] for r in rows]))
    rep = {"sample": len(rows), "before_recall5": round(avg_b, 3), "after_recall5": round(avg_a, 3),
           "lift": round(avg_a - avg_b, 3), "rows": rows}
    (REP / "llm_rerank_eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("sample", "before_recall5", "after_recall5", "lift")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
