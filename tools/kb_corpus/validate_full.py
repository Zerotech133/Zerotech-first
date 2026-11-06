# -*- coding: utf-8 -*-
"""全量 packed 索引检索验证（bge-m3 + 余弦 top5 + 可选 qwen 生成）。

用法：python tools/kb_corpus/validate_full.py [--packed 50_RAG/packed_full] [--ask]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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

KB = Path(r"C:\项目\知识库")
OLLAMA = "http://127.0.0.1:11434"
EMBED = "bge-m3:latest"

QS = ["如何避免回测过拟合？", "walk-forward 验证怎么做？", "交易成本和滑点如何影响策略？",
      "仓位管理与凯利公式怎么用？", "机器学习在量化中的风险？"]


def post(path: str, body: dict, timeout: int = 120):
    import urllib.request
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with safe_urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packed", default=str(KB / "50_RAG" / "packed_full"))
    ap.add_argument("--ask", action="store_true")
    args = ap.parse_args(argv)
    p = Path(args.packed)
    meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    docs = json.loads((p / "docs.json").read_text(encoding="utf-8"))
    norm_file = p / "emb_norm.f32.npy"
    t0 = time.time()
    mat = np.load(norm_file) if norm_file.exists() else np.fromfile(p / "emb.f32", dtype="<f4").reshape(meta["rows"], meta["dim"])
    print(f"loaded rows={mat.shape[0]} dim={mat.shape[1]} in {time.time()-t0:.1f}s", flush=True)
    out = []
    for q in QS:
        e = np.array(post("/api/embed", {"model": EMBED, "input": q})["embeddings"][0], dtype=np.float32)
        e /= (np.linalg.norm(e) + 1e-9)
        scores = mat @ e
        idx = np.argsort(-scores)[:5]
        hits = [{"score": round(float(scores[i]), 4), "title": docs[i].get("title", "")[:60],
                 "source": docs[i].get("source"), "kind": docs[i].get("kind")} for i in idx]
        out.append({"question": q, "top1": hits[0]["score"], "hits": hits})
        print(q, "top1=", hits[0]["score"], "|", hits[0]["title"][:40], flush=True)
    rep = {"packed": str(p), "rows": int(mat.shape[0]), "dim": int(mat.shape[1]),
           "load_seconds": round(time.time() - t0, 1), "results": out}
    (KB / "90_报告" / "full_index_validate.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved: 90_报告/full_index_validate.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
