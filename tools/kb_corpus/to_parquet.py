# -*- coding: utf-8 -*-
"""P1b · JSONL → Parquet（videos / comments / map）。

- videos.jsonl → videos.parquet（单文件）
- comments.jsonl → comments/part-*.parquet（分块，避免内存爆）
- video_channel_map.jsonl → video_channel_map.parquet
用法：python tools/kb_corpus/to_parquet.py [--chunk 200000]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

KB = Path(r"C:\项目\知识库")
D = KB / "10_规范化"
REP = KB / "90_报告"


def read_jsonl(path: Path, chunk: int):
    buf = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                buf.append(json.loads(line))
            except ValueError:
                continue
            if len(buf) >= chunk:
                yield buf
                buf = []
    if buf:
        yield buf


def write_parquet(rows: list[dict], path: Path) -> None:
    # 统一列集合（缺列补 None）
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    table = pa.table({c: [r.get(c) for r in rows] for c in cols})
    pq.write_table(table, path, compression="zstd")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=200000)
    args = ap.parse_args(argv)
    out = {"chunks": 0, "rows": 0}
    # videos 单文件
    rows = [r for r in read_jsonl(D / "videos.jsonl", args.chunk)]
    all_v = [x for chunk in rows for x in chunk]
    write_parquet(all_v, D / "videos.parquet")
    print("videos.parquet rows:", len(all_v), flush=True)
    # map
    m_rows = [x for chunk in read_jsonl(D / "video_channel_map.jsonl", args.chunk) for x in chunk]
    write_parquet(m_rows, D / "video_channel_map.parquet")
    print("map rows:", len(m_rows), flush=True)
    # comments 分块
    cdir = D / "comments"
    cdir.mkdir(exist_ok=True)
    for i, chunk in enumerate(read_jsonl(D / "comments.jsonl", args.chunk)):
        write_parquet(chunk, cdir / ("part-%04d.parquet" % i))
        out["chunks"] += 1
        out["rows"] += len(chunk)
        if i % 10 == 0:
            print(f"comments chunk {i} rows={len(chunk)} total={out['rows']}", flush=True)
    print("comments rows:", out["rows"], "chunks:", out["chunks"], flush=True)
    rep = {"videos": len(all_v), "map": len(m_rows), "comments": out["rows"], "chunks": out["chunks"]}
    (REP / "p1b_parquet_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
