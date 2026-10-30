# -*- coding: utf-8 -*-
"""bge-reranker-base（ONNX）cross-encoder 重排评测（golden 50）。

依赖：onnxruntime + tokenizers（py312 已内置），模型 C:/项目/models/bge-reranker-base。
流程：RRF 取 top20 → cross-encoder 打分重排 → 取 top5，与 RRF 基线比 Recall@5。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

KB = Path(r"C:/项目/知识库")
MODEL = Path(r"C:/项目/models/bge-reranker-base")
REP = KB / "90_报告"
sys.path.insert(0, str(Path(r"C:/项目/老田的AI助手/tools/kb_corpus")))
import rag_eval as RE  # noqa: E402


def main() -> int:
    import onnxruntime as ort
    import tokenizers

    docs = json.loads((RE.PACKED / "docs.json").read_text(encoding="utf-8"))
    mat = np.load(RE.PACKED / "emb_norm.f32.npy")
    bm25 = RE.BM25([d.get("title", "") + " " + d.get("text", "") for d in docs])
    tok = tokenizers.Tokenizer.from_file(str(MODEL / "tokenizer.json"))
    tok.enable_truncation(max_length=512)
    sess = ort.InferenceSession(str(MODEL / "onnx_model.onnx"), providers=["CPUExecutionProvider"])
    print("inputs:", [i.name for i in sess.get_inputs()], "outputs:", [o.name for o in sess.get_outputs()], flush=True)
    in_names = [i.name for i in sess.get_inputs()]
    try:
        import phase5_full_eval as P5
        GOLD = RE.GOLD + P5.EXTRA
    except Exception:  # noqa: BLE001
        GOLD = RE.GOLD

    def score_pairs(q: str, cands: list[str]) -> list[float]:
        scores = []
        for c in cands:
            enc = tok.encode(q, c)
            ids = [enc.ids]
            am = [enc.attention_mask]
            tt = [enc.type_ids]
            feeds = {}
            for n in in_names:
                if n == "input_ids":
                    feeds[n] = np.array(ids, dtype=np.int64)
                elif n == "attention_mask":
                    feeds[n] = np.array(am, dtype=np.int64)
                elif n == "token_type_ids":
                    feeds[n] = np.array(tt, dtype=np.int64)
            out = sess.run(None, feeds)[0]
            v = float(np.ravel(out)[0])
            scores.append(v)
        return scores

    rows = []
    t0 = time.time()
    for q, keys in GOLD:
        vec = RE.embed(q + " " + " ".join(keys))
        vs = mat @ vec
        bs = bm25.score(q + " " + " ".join(keys))
        vr = np.argsort(-vs); br = np.argsort(-bs)
        vrank = np.empty(len(vs), dtype=np.float32); brank = np.empty(len(bs), dtype=np.float32)
        vrank[vr] = np.arange(len(vs)); brank[br] = np.arange(len(bs))
        fused = -(0.7 / (60.0 + vrank) + 0.3 / (60.0 + brank))
        top20 = np.argsort(fused)[:20]
        cands = [(docs[int(i)].get("title", "") + " " + docs[int(i)].get("text", "")[:300]) for i in top20]
        sc = score_pairs(q, cands)
        order = np.argsort(-np.array(sc))
        reranked = [int(top20[i]) for i in order[:5]]

        def hit(di):
            t = (docs[int(di)].get("title", "") + " " + docs[int(di)].get("text", "")).lower()
            return any(k.lower() in t for k in keys)

        before = [int(i) for i in top20[:5]]
        rows.append({"query": q, "rrf_recall5": sum(hit(i) for i in before) / 5,
                     "rerank_recall5": sum(hit(i) for i in reranked) / 5})
        print("rerank:", q, rows[-1], flush=True)
    avg_b = float(np.mean([r["rrf_recall5"] for r in rows]))
    avg_a = float(np.mean([r["rerank_recall5"] for r in rows]))
    rep = {"model": "bge-reranker-base-onnx", "queries": len(rows),
           "rrf_recall5": round(avg_b, 3), "rerank_recall5": round(avg_a, 3),
           "lift": round(avg_a - avg_b, 3), "seconds": round(time.time() - t0, 1), "rows": rows}
    (REP / "reranker_eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("queries", "rrf_recall5", "rerank_recall5", "lift", "seconds")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
