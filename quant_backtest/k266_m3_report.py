# -*- coding: utf-8 -*-
"""K266 M3 影子验收统计：类型/方向分布 + tags 榜 + ERR 清单 + 人审抽样。
用法: python k266_m3_report.py [--sample 10]"""
import json
import os
import random
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW = os.path.join(BASE, "data", "k266_docs", "structure_shadow.jsonl")

rows = []
with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass

ok = [r for r in rows if r.get("raw_ok")]
err = [r for r in rows if not r.get("raw_ok")]
print(f"=== M3 影子统计：总 {len(rows)} | ok {len(ok)} | err {len(err)}"
      f"（err率 {len(err)/max(len(rows),1)*100:.0f}%）===")

print("\n[类型分布]", dict(Counter(r.get("doc_type") for r in ok)))
print("[方向分布]", dict(Counter(r.get("direction") for r in ok)))

tags = Counter()
for r in ok:
    tags.update(r.get("tags", []))
print(f"\n[tags 榜 Top20]（词种 {len(tags)}）")
for w, n in tags.most_common(20):
    print(f"  {n:>3}  {w}")

ev_n = sum(len(r.get("events", [])) for r in ok)
co = Counter()
for r in ok:
    co.update(r.get("companies", []))
print(f"\n[事件五元组] 共 {ev_n} 条，均值 {ev_n/max(len(ok),1):.1f}/篇")
print("[公司提及 Top10]")
for c, n in co.most_common(10):
    print(f"  {n:>3}  {c}")

if err:
    print(f"\n[ERR 清单 {len(err)} 条]")
    for r in err[:8]:
        print(f"  {r.get('doc_id','')} {r.get('name','')[:34]}  {r.get('err','')[:60]}")

n = int(sys.argv[sys.argv.index("--sample") + 1]) if "--sample" in sys.argv else 10
random.seed(7)
print(f"\n[人审抽样 {n} 篇]")
for r in random.sample(ok, min(n, len(ok))):
    evs = "; ".join(f"{e.get('subject','')}-{e.get('rel','')}-{e.get('object','')}"
                    for e in r.get("events", [])[:2])
    print(f"  {r['doc_id']} {r.get('name','')[:30]}")
    print(f"    类型={r.get('doc_type')} 方向={r.get('direction')} "
          f"理由={r.get('direction_why','')[:30]}")
    print(f"    tags={','.join(r.get('tags', [])[:6])}")
    if evs:
        print(f"    事件: {evs[:90]}")
