# -*- coding: utf-8 -*-
"""K266 金标集抽样（拍板 2026-10-08：建 30 篇方向判定校准集）。
分层：研报 20 + 公告剪报（非研报 doc_type）10；direction 配额 利好13/中性16/利空1（利空全量仅1篇保入选）。
产出：data/k266_docs/gold_labels.jsonl（gold 字段留空待人审）+ 控制台人审清单。
用法: python -X utf8 k266_gold_sample.py"""
import json
import os
import random

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW = os.path.join(BASE, "data", "k266_docs", "structure_shadow.jsonl")
GOLD = os.path.join(BASE, "data", "k266_docs", "gold_labels.jsonl")

QUOTA = {"利好": 13, "中性": 16, "利空": 1}
N_RESEARCH = 20

rows = []
with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("raw_ok"):
            rows.append(r)
random.seed(20261008)

picked = []
# 第一轮：按方向配额抽，研报与非研报池分开保证 N_RESEARCH=20
pools = {}
for r in rows:
    is_res = (r.get("doc_type") == "研报")
    pools.setdefault((r.get("direction"), is_res), []).append(r)
for d, quota in QUOTA.items():
    res = pools.get((d, True), [])
    oth = pools.get((d, False), [])
    random.shuffle(res); random.shuffle(oth)
    # 研报目标 = round(quota * 20/30)，余量用非研报补
    n_res = min(len(res), round(quota * N_RESEARCH / 30))
    picked += res[:n_res]
    need = quota - n_res
    if need > 0:  # 非研报不够则从研报余量补
        extra = oth[:need]
        if len(extra) < need:
            extra += res[n_res:n_res + (need - len(extra))]
        picked += extra

# 收尾：数量不足 30 从剩余随机补足
rest = [r for r in rows if r not in picked]
random.shuffle(rest)
picked += rest[:30 - len(picked)]

out = []
for r in sorted(picked, key=lambda x: x["doc_id"]):
    out.append({
        "doc_id": r["doc_id"], "name": r.get("name"), "kind": r.get("kind"),
        "doc_type": r.get("doc_type"), "title_guess": r.get("title_guess"),
        "shadow_direction": r.get("direction"), "shadow_why": r.get("direction_why"),
        "shadow_companies": r.get("companies", []),
        "gold_direction": "", "gold_comment": "",   # ← 人审填写：利好|利空|中性 + 备注
    })
with open(GOLD, "w", encoding="utf-8") as f:
    for o in out:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
print(f"金标集 {len(out)} 篇 -> {GOLD}")
from collections import Counter
print("direction 分布 =", dict(Counter(o['shadow_direction'] for o in out)))
print("doc_type 分布 =", dict(Counter(o['doc_type'] for o in out)))
print("--- 人审清单 ---")
for i, o in enumerate(out, 1):
    print(f"{i:2d}. {o['doc_id'][:20]:22s} {o['doc_type']:<6s} {o['shadow_direction']:<4s} "
          f"{(o['title_guess'] or '')[:26]}")
