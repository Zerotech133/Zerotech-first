#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_unit_blogger_map.py — 为「全部文案」批次的 units 重建博主归属映射。

背景（2026-11-08）:
    build_units_batch -> units_v2.build_units 在落行时丢弃了 blogger 字段，
    且 video_key（Excel 无发布时间的日期占位）跨博主大量撞 key
    （34,466 units / 3,356 个 key，重复 2,853）。分诊分层（top10 先行、
    按博主抽检）需要 unit -> blogger 归属，本脚本用 claim 文本锚从
    learning.db fact_video_copy（12,160 视频，含 blogger_name + copy_text）
    重建映射。

方法:
    规范化（去空白）后，视频 copy_text 滑窗抽 50-gram 指纹建倒排；
    unit claim 取前 50 字查候选视频，再以完整子串验证唯一归属；
    多视频同文 → ambiguous（转发/洗稿），保留全部候选。

输出:
    30_知识单元/<pid>_unit_blogger_map.jsonl  每行 {unit_id, blogger_name,
    blogger_rank, video_id, match: exact|multi|no_match, candidates}

只读：不改 draft jsonl、不写 DB。映射文件供 learning_status_flow --filter-ids
的名单预生成使用。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

KB = Path(r"C:/项目/知识库")
UNIT_DIR = KB / "30_知识单元"
DB = KB / "00_数据仓" / "learning.db"
FP_LEN = 30
FP_STEP = 5      # 视频侧滑窗步长
PROBE_N = 5      # claim 侧探针：起点 0,1,2,3,4（连续）→ (p+pi) mod 5 必有一次对齐


def norm(s: str) -> str:
    return "".join(s.split())


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else "视频号学习_全部文案_20261108"
    draft = UNIT_DIR / f"{pid}_units_draft.jsonl"
    if not draft.exists():
        print(f"[错误] draft 不存在: {draft}", file=sys.stderr)
        return 2

    t0 = time.time()
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    # 只取有文案的视频
    rows = cur.execute(
        "select video_id, blogger_name, blogger_rank, copy_text "
        "from fact_video_copy where has_copy = 1 and copy_text is not null"
    ).fetchall()
    con.close()
    print(f"[1/3] 载入视频语料: {len(rows)} 视频 [{time.time()-t0:.1f}s]")

    fp_index: dict[str, set] = defaultdict(set)
    vid_text: dict[str, str] = {}
    vid_meta: dict[str, tuple] = {}
    for video_id, bname, brank, ctext in rows:
        t = norm(ctext or "")
        if len(t) < 10:
            continue
        vid_text[video_id] = t
        vid_meta[video_id] = (bname, brank)
        for i in range(0, max(1, len(t) - FP_LEN + 1), FP_STEP):
            fp_index[t[i:i + FP_LEN]].add(video_id)
    print(f"[2/3] 指纹倒排: {len(fp_index)} 指纹 [{time.time()-t0:.1f}s]")

    out = UNIT_DIR / f"{pid}_unit_blogger_map.jsonl"
    stat = {"exact": 0, "multi": 0, "no_match": 0}
    by_blogger: dict[str, int] = defaultdict(int)
    with draft.open(encoding="utf-8") as fin, \
         out.open("w", encoding="utf-8", newline="\n") as fout:
        for ln in fin:
            r = json.loads(ln)
            c = norm(r.get("claim") or "")
            # 多探针：claim 内起点 0..4（连续）各取 30-gram，
            # 视频侧采样位置均为 5 的倍数 → (p+pi) mod 5 必有一次 == 0
            hits: list = []
            for pi in range(PROBE_N):
                probe = c[pi:pi + FP_LEN]
                if len(probe) < FP_LEN:
                    break
                cands = fp_index.get(probe)
                if not cands:
                    continue
                hits = [v for v in cands if c in vid_text[v]]
                if hits:
                    break
            if not hits and len(c) >= FP_LEN:
                # fallback：claim 尾段探针（防切分边界噪声集中在头部）
                probe = c[-FP_LEN:]
                cands = fp_index.get(probe, set())
                hits = [v for v in cands if c in vid_text[v]]
            if len(hits) == 1:
                v = hits[0]
                bname, brank = vid_meta[v]
                rec = {"unit_id": r["unit_id"], "blogger_name": bname,
                       "blogger_rank": brank, "video_id": v, "match": "exact",
                       "candidates": 1}
                stat["exact"] += 1
                by_blogger[bname] += 1
            elif len(hits) > 1:
                # 多归属：取 rank 最小（视频数最多博主）为主归属，保留候选
                hits.sort(key=lambda v: vid_meta[v][1] or 999)
                bname, brank = vid_meta[hits[0]]
                rec = {"unit_id": r["unit_id"], "blogger_name": bname,
                       "blogger_rank": brank, "video_id": hits[0],
                       "match": "multi", "candidates": len(hits)}
                stat["multi"] += 1
                by_blogger[bname] += 1
            else:
                rec = {"unit_id": r["unit_id"], "blogger_name": None,
                       "blogger_rank": None, "video_id": None,
                       "match": "no_match", "candidates": 0}
                stat["no_match"] += 1
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total = sum(stat.values())
    resolved = stat["exact"] + stat["multi"]
    print(f"[3/3] 完成 [{time.time()-t0:.1f}s] total={total} {stat}")
    print(f"归属率: {resolved/total:.2%}  -> {out}")
    # 快速对照 top10（rank<=10）
    t10 = sum(v for k, v in by_blogger.items()
              if any(vid_meta[v][1] and vid_meta[v][1] <= 10
                     for v in [next((vv for vv, m in vid_meta.items()
                                     if m[0] == k), None)] if v))
    print(f"top10 博主归属 units（按 rank<=10 博主计）: 约 {t10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
