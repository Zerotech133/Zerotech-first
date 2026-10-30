# -*- coding: utf-8 -*-
"""可行性验证：YouTube-Commons（PleIAs，HF 镜像）与本语料 video_id 交叉匹配。

下载 cctube_0.parquet（~0.4GB，hf-mirror）→ 读取 ID 列 → 与知识库 videos.parquet 的 video_id 求交。
产出 90_报告/youtube_commons_match.json
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

KB = Path(r"C:/项目/知识库")
OUT = KB / "90_报告"
CACHE = Path(r"C:/项目/work/research2/yt_commons")
URL = "https://hf-mirror.com/datasets/PleIAs/YouTube-Commons/resolve/main/cctube_0.parquet"


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / "cctube_0.parquet"
    if not f.exists() or f.stat().st_size < 100_000_000:
        print("downloading", URL, flush=True)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=600) as r, f.open("wb") as out:
            while True:
                chunk = r.read(8 << 20)
                if not chunk:
                    break
                out.write(chunk)
    print("downloaded:", f.stat().st_size / 1e9, "GB", flush=True)
    schema = pq.read_schema(f)
    print("columns:", schema.names, flush=True)
    # 找 ID 列
    id_col = None
    for cand in ("video_id", "youtube_id", "id", "video"):
        if cand in schema.names:
            id_col = cand
            break
    if id_col is None:
        # 打印首行找线索
        t = pq.read_table(f, columns=schema.names[:8]).slice(0, 2).to_pandas()
        print(t.to_string()[:1000], flush=True)
        return 2
    tb = pq.read_table(f, columns=[id_col])
    ids = set(tb.column(id_col).to_pylist())
    ours = pd.read_parquet(KB / "10_规范化" / "videos.parquet", columns=["video_id"])
    our_ids = set(ours["video_id"].astype(str))
    hit = our_ids & ids
    rep = {"dataset_shard": "cctube_0.parquet", "id_column": id_col,
           "commons_rows": len(ids), "ours": len(our_ids),
           "matched": len(hit), "match_pct": round(len(hit) * 100.0 / max(1, len(our_ids)), 2),
           "sample": sorted(hit)[:10]}
    (OUT / "youtube_commons_match.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:600], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
