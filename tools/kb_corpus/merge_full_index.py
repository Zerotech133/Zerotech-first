# -*- coding: utf-8 -*-
"""合并扩索引 shards → packed_full（门户/验证可用）。

输入：40_索引/full/shards/shard-*.jsonl + shard-*.npy
输出：50_RAG/packed_full/{docs.json, emb.f32, emb_norm.f32.npy, meta.json}

用法：python tools/kb_corpus/merge_full_index.py [--expect 62401]
可重复运行（全量完成后重跑一次即最终版）；正在写入的 shard 自动跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from array import array
from datetime import datetime
from pathlib import Path

import numpy as np

KB = Path(r"C:\项目\知识库")
SHARDS = KB / "40_索引" / "full" / "shards"
OUT = KB / "50_RAG" / "packed_full"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int, default=0)
    args = ap.parse_args(argv)
    shard_files = sorted(SHARDS.glob("shard-*.jsonl"))
    print("shards:", len(shard_files), flush=True)
    all_rows: list[dict] = []
    embs, dim = [], None
    skipped = 0
    for jf in shard_files:
        npy = jf.with_suffix(".npy")
        if not npy.exists():
            skipped += 1
            continue
        try:
            rows = [json.loads(x) for x in jf.read_text(encoding="utf-8").splitlines() if x.strip()]
            arr = np.load(npy)
        except (OSError, ValueError, json.JSONDecodeError):
            skipped += 1
            continue
        if arr.ndim != 2 or arr.shape[0] != len(rows) or (dim is not None and arr.shape[1] != dim):
            skipped += 1
            continue
        dim = int(arr.shape[1])
        all_rows.extend(rows)
        embs.append(arr.astype(np.float32))
    if not all_rows:
        print("no docs", flush=True)
        return 2
    mat = np.concatenate(embs, axis=0)
    OUT.mkdir(parents=True, exist_ok=True)
    full_docs = [{"id": i, "source": r.get("video_id") or r.get("doc_id") or "",
                  "kind": r.get("kind", ""), "title": r.get("title", ""),
                  "text": r.get("text", "")} for i, r in enumerate(all_rows)]
    (OUT / "docs.json").write_text(json.dumps(full_docs, ensure_ascii=False), encoding="utf-8")
    a = array("f")
    a.frombytes(mat.astype("<f4").tobytes())
    with (OUT / "emb.f32").open("wb") as f:
        a.tofile(f)
    norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    np.save(OUT / "emb_norm.f32.npy", norm.astype(np.float32))
    meta = {"dim": dim, "rows": len(full_docs), "shards": len(shard_files),
            "skipped_shards": skipped, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "expected": args.expect or None, "emb_bytes": (OUT / "emb.f32").stat().st_size}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
