# -*- coding: utf-8 -*-
"""YouTube-Commons 多 shard 命中率抽样（判断 Phase3 解阻可行性）。

扫描指定 shard 列表（默认 1,2,3,4,5,50,200,440），逐 shard 下载→ID 交集，
命中≥20 或扫完即停。产出 90_报告/youtube_commons_scan.json
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

KB = Path(r"C:/项目/知识库")
CACHE = Path(r"C:/项目/work/research2/yt_commons")
REP = KB / "90_报告"
BASE = "https://hf-mirror.com/datasets/PleIAs/YouTube-Commons/resolve/main/cctube_{}.parquet"
SHARDS = [1, 2, 3, 4, 5, 50, 200, 440]


def download(n: int) -> Path:
    f = CACHE / f"cctube_{n}.parquet"
    if f.exists() and f.stat().st_size > 100_000_000:
        return f
    req = urllib.request.Request(BASE.format(n), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, f.open("wb") as out:
        while True:
            chunk = r.read(8 << 20)
            if not chunk:
                break
            out.write(chunk)
    return f


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    ours = set(pd.read_parquet(KB / "10_规范化" / "videos.parquet", columns=["video_id"])["video_id"].astype(str))
    found = set()
    rows = []
    for n in SHARDS:
        try:
            f = download(n)
            tb = pq.read_table(f, columns=["video_id"])
            ids = set(tb.column("video_id").to_pylist())
            hit = ours & ids
            found |= hit
            rows.append({"shard": n, "rows": len(ids), "matched": len(hit)})
            print(f"shard {n}: rows={len(ids)} matched={len(hit)} total_found={len(found)}", flush=True)
            if len(found) >= 20:
                break
        except Exception as e:  # noqa: BLE001
            rows.append({"shard": n, "error": f"{type(e).__name__}: {str(e)[:100]}"})
            print(f"shard {n}: ERROR {e}", flush=True)
    rep = {"shards_scanned": len(rows), "found": len(found), "sample": sorted(found)[:10], "rows": rows}
    (REP / "youtube_commons_scan.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"shards_scanned": len(rows), "found": len(found)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
