# -*- coding: utf-8 -*-
"""tier3 T 类第一轮 dump 生成器（3,000 条，博主轮转取样，2026-11-08）

老田批准的三线策略线 1：T 类先行（tier2 抽检通过率 92%，教程价值最高）。
- 源：draft 池 cls=T & status=draft（7,652 条）
- 取样：按博主轮转（round-robin），避免单博主垄断批次；seed 固定 20261108
- 输出格式同 tier2：unit_id/cls/blogger/video/claim
"""
import json
import random
from collections import defaultdict
from pathlib import Path

DRAFT = Path(r"C:/项目/知识库/30_知识单元/视频号学习_全部文案_20261108_units_draft.jsonl")
BMAP = Path(r"C:/项目/知识库/30_知识单元/视频号学习_全部文案_20261108_unit_blogger_map.jsonl")
OUT = Path(r"C:/项目/_tmp_ci9/tier3_T1_review_dump.jsonl")

N_TARGET = 3000
SEED = 20261108

def main():
    bmap = {}
    for ln in BMAP.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            bmap[r["unit_id"]] = (r.get("blogger_name") or "_no_match_", r.get("video_id") or "")

    pool = defaultdict(list)  # blogger -> [row]
    total_T_draft = 0
    with DRAFT.open(encoding="utf-8") as f:
        for ln in f:
            r = json.loads(ln)
            if r.get("status") != "draft" or r.get("unit_class") != "T":
                continue
            total_T_draft += 1
            bl, vid = bmap.get(r["unit_id"], ("_no_match_", ""))
            pool[bl].append({
                "unit_id": r["unit_id"],
                "cls": "T",
                "blogger": bl,
                "video": vid,
                "claim": (r.get("claim") or "").strip(),
            })

    bloggers = sorted(pool)  # 排序保证可复现
    rng = random.Random(SEED)
    for bl in bloggers:
        rng.shuffle(pool[bl])

    # 轮转：每轮从每个博主取 1 条，直到 3000
    picked = []
    cursors = {bl: 0 for bl in bloggers}
    while len(picked) < N_TARGET:
        progressed = False
        for bl in bloggers:
            if len(picked) >= N_TARGET:
                break
            if cursors[bl] < len(pool[bl]):
                picked.append(pool[bl][cursors[bl]])
                cursors[bl] += 1
                progressed = True
        if not progressed:
            break

    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    bl_cnt = defaultdict(int)
    for r in picked:
        bl_cnt[r["blogger"]] += 1
    print(f"T 类 draft 池: {total_T_draft}  取样: {len(picked)}  博主数: {len(bloggers)}")
    top = sorted(bl_cnt.items(), key=lambda x: -x[1])[:8]
    print("配额 top8:", top)
    print("->", OUT)

if __name__ == "__main__":
    main()
