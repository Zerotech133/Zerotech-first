# -*- coding: utf-8 -*-
"""扩索引 · 将 P4-lite 从 pilot100 扩到 core_quant 过滤后的全量视频。

产物（均可续跑）：
  40_索引/full/shards/shard-XXXX.jsonl   （doc_id/video_id/kind/title/text）
  40_索引/full/shards/shard-XXXX.npy     （对应 embedding 矩阵 float32 1024d）
  40_索引/full/progress.json             （已完成 doc 数 / 最后处理位置）

用法：python tools/kb_corpus/build_full_index.py [--max-docs N] [--batch 500]
可中断后重跑（按 progress.json 跳过已完成 shard）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from tools.safe_urlopen import safe_urlopen

KB = Path(r"C:\项目\知识库")
D = KB / "10_规范化"
CLN = KB / "20_清洗" / "comments_clean"
FULL = KB / "40_索引" / "full"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"


def post(path: str, body: dict, timeout=120):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(safe_urlopen(req, timeout=timeout).read().decode())


def embed(text: str) -> list[float]:
    return post("/api/embeddings", {"model": EMBED_MODEL, "prompt": text[:2000]})["embedding"]


def core_quant_channels() -> set[str]:
    out = set()
    fp = KB / "02_索引与读取说明" / "目标频道有效清单_100_v2.jsonl"
    with fp.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            tags = (d.get("candidate") or {}).get("topic_tags") or []
            if "core_quant" in tags and d.get("channel_id"):
                out.add(d["channel_id"])
    return out


def load_docs() -> list[dict]:
    vdf = pd.read_parquet(D / "videos.parquet")
    cq = core_quant_channels()
    vdf = vdf[vdf["channel_id"].isin(cq)].copy()
    vdf["description"] = vdf["description"].fillna("").astype(str).str.slice(0, 1500)
    vdf["title"] = vdf["title"].fillna("").astype(str)
    print("core_quant videos:", len(vdf), flush=True)
    # 评论：每视频 top10
    frames = []
    for f in sorted((CLN).glob("*.parquet")):
        tb = pq.read_table(f, columns=["video_id", "text", "like_count"])
        df = tb.to_pandas()
        df = df[df["video_id"].isin(set(vdf["video_id"]))]
        if len(df):
            frames.append(df)
    cdf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["video_id", "text", "like_count"])
    cdf["like_count"] = pd.to_numeric(cdf["like_count"], errors="coerce").fillna(0)
    cdf = cdf.sort_values("like_count", ascending=False).groupby("video_id").head(10)
    cdf["text"] = cdf["text"].fillna("").astype(str).str.slice(0, 300)
    cgroup = {k: v["text"].tolist() for k, v in cdf.groupby("video_id")}
    print("comments for videos:", len(cgroup), "rows:", len(cdf), flush=True)

    docs = []
    for r in vdf.itertuples():
        docs.append({"doc_id": "v_" + r.video_id, "video_id": r.video_id,
                     "channel_id": r.channel_id, "kind": "video",
                     "title": r.title[:120], "upload_date": str(r.upload_date or ""),
                     "text": "标题：" + r.title + "\n描述：" + r.description})
        cs = cgroup.get(r.video_id)
        if cs:
            docs.append({"doc_id": "c_" + r.video_id, "video_id": r.video_id,
                         "channel_id": r.channel_id, "kind": "comments",
                         "title": r.title[:120], "upload_date": str(r.upload_date or ""),
                         "text": "标题：" + r.title + "\n高赞评论：\n" + "\n".join("- " + t for t in cs)})
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args(argv)
    FULL.mkdir(parents=True, exist_ok=True)
    shards = FULL / "shards"
    shards.mkdir(exist_ok=True)
    progress_f = FULL / "progress.json"
    done = int(json.loads(progress_f.read_text(encoding="utf-8")).get("done", 0)) if progress_f.exists() else 0
    docs = load_docs()
    if args.max_docs:
        docs = docs[: args.max_docs]
    total = len(docs)
    print(f"docs total={total} already_done={done}", flush=True)
    t0 = time.time()
    i = done
    shard_no = len(list(shards.glob("shard-*.jsonl")))
    while i < total:
        batch = docs[i: i + args.batch]
        rows, embs = [], []
        for d in batch:
            rows.append(d)
            embs.append(embed(d["text"]))
        (shards / ("shard-%04d.jsonl" % shard_no)).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        np.save(shards / ("shard-%04d.npy" % shard_no), np.array(embs, dtype=np.float32))
        shard_no += 1
        i += len(batch)
        progress_f.write_text(json.dumps({"done": i, "total": total,
                                          "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                                         ensure_ascii=False), encoding="utf-8")
        el = time.time() - t0
        rate = (i - done) / el if el else 0
        eta = (total - i) / rate / 60 if rate else 0
        print(f"progress {i}/{total} ({i*100.0/max(total,1):.1f}%) rate={rate:.1f}/s eta={eta:.0f}min", flush=True)
    print("DONE total docs:", i, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
