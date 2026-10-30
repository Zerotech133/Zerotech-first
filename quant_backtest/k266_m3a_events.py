# -*- coding: utf-8 -*-
"""K266 M3-A 篇级事件层（预研见 INTEL_RD_PLAN 补记六）：
影子 events → Security 三级映射 → event_shadow.jsonl（影子层）。
口径：事件方向 = 所属记录（文档/单条新闻）的 direction——B 案单条化后即事件级精度。
映射三级（复用 M3-B）：精确 name → aliases → CONTAINS 唯一；主体/客体双命中 Security
→ both_sec=true 为入图候选；单边命中降级保留（挂属性候选）。
去重：同(主体,客体,rel,方向) 1 小时聚窗链式合并（多源重复报道计 n）。
影子纪律：默认只写 event_shadow.jsonl；--dry 预览不落盘；--commit 入图（人审后）。
用法: python -X utf8 k266_m3a_events.py [--dry] [--commit]"""

import json
import os
import sys
import hashlib
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kg_client import KgClient

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW = os.path.join(BASE, "data", "k266_docs", "structure_shadow.jsonl")
OUT = os.path.join(BASE, "data", "k266_docs", "event_shadow.jsonl")
DRY = "--dry" in sys.argv
COMMIT = "--commit" in sys.argv

# 1) 影子 events 展开（按 ts 排序备聚窗）
events, n_docs, n_ev = [], 0, 0
for line in open(SHADOW, encoding="utf-8"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if not r.get("raw_ok"):
        continue
    n_docs += 1
    try:
        ts = datetime.strptime(r.get("ts", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        ts = datetime(2000, 1, 1)
    for e in r.get("events", []):
        if not ((e.get("subject") or "").strip() or (e.get("object") or "").strip()):
            continue
        n_ev += 1
        events.append((ts, r, e))
events.sort(key=lambda x: x[0])
print(f"[1] 影子记录 raw_ok={n_docs} events={n_ev}")

# 2) Security 三级映射表
c = KgClient()
with c._session() as s:
    secs = s.run("MATCH (s:Security) RETURN s.name AS n, s.aliases AS a").data()
names = {r["n"] for r in secs}
alias_map = {}
for r in secs:
    a = r.get("a")
    if isinstance(a, list):
        for x in a:
            if x:
                alias_map[x] = r["n"]
    elif isinstance(a, str) and a:
        alias_map[a] = r["n"]


# contains 级品类词证券黑名单（K266-W7 入图人审发现：证券名本身是品类词，
# 描述短语包含它即误配——"人形机器人零组件"≠机器人(新松)）。exact/alias 不受限。
GENERIC_SEC = {"机器人", "航天电子"}


def to_sec(name):
    """raw 名称 → (Security 名或 None, 命中方式)"""
    name = (name or "").strip()
    if not name:
        return None, "empty"
    if name in names:
        return name, "exact"
    if name in alias_map:
        return alias_map[name], "alias"
    hits = [n for n in names if name in n or n in name]
    if len(hits) == 1:
        n = hits[0]
        # contains 三重闸：品类词证券 + 短词(<4字，如"火电"⊂烽火电子) + 并列清单
        # （如"陕西电器研究所、…集团"整串只该配其一，宁缺毋滥）
        if n in GENERIC_SEC:
            return None, "generic"
        if len(name) < 4 or any(ch in name for ch in "、；;"):
            return None, "fuzzy"
        return n, "contains"
    return None, ("ambig" if hits else "miss")


# 3) 映射 + 1h 聚窗去重 → 影子事件
st = {
    "exact": 0,
    "alias": 0,
    "contains": 0,
    "ambig": 0,
    "miss": 0,
    "empty": 0,
    "generic": 0,
    "fuzzy": 0,
}
buckets = {}
for ts, r, e in events:
    s_raw, o_raw = (e.get("subject") or "").strip(), (e.get("object") or "").strip()
    s_sec, s_how = to_sec(s_raw)
    o_sec, o_how = to_sec(o_raw)
    st[s_how] += 1
    st[o_how] += 1
    key = (s_sec or s_raw, o_sec or o_raw, e.get("rel") or "", r.get("direction") or "")
    eid = hashlib.md5(("|".join(map(str, key))).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    b = buckets.get(key)
    if b and ts - b["ts_last"] <= timedelta(hours=1):
        b["n"] += 1
        b["ts_last"] = ts
        if len(b["docs"]) < 5:
            b["docs"].append(r.get("doc_id"))
    else:
        buckets[key] = {
            "eid": eid,
            "subj": s_raw,
            "subj_sec": s_sec,
            "obj": o_raw,
            "obj_sec": o_sec,
            "rel": e.get("rel", ""),
            "dir": r.get("direction", "中性"),
            "date": e.get("date", ""),
            "amount": e.get("amount", ""),
            "n": 1,
            "src": r.get("src", ""),
            "ts_first": ts,
            "ts_last": ts,
            "docs": [r.get("doc_id")],
        }
rows = list(buckets.values())
for b in rows:
    b["ts_first"] = b["ts_first"].strftime("%Y-%m-%d %H:%M:%S")
    b["ts_last"] = b["ts_last"].strftime("%Y-%m-%d %H:%M:%S")
    b["both_sec"] = bool(b["subj_sec"] and b["obj_sec"])
n_both = sum(1 for b in rows if b["both_sec"])
print(f"[2] 端点映射统计（主体+客体两侧）: {st}")
print(f"[3] 聚窗后影子事件 {len(rows)} 条，双端命中 Security（入图候选）= {n_both}")

if DRY:
    print("[4] DRY 不落盘，Top10（按 n）预览：")
    for b in sorted(rows, key=lambda x: -x["n"])[:10]:
        print(
            "   n=%-3d %-8s %s(%s) -[%s]-> %s(%s) %s %s"
            % (
                b["n"],
                b["dir"],
                b["subj"],
                b["subj_sec"] or "-",
                b["rel"],
                b["obj"],
                b["obj_sec"] or "-",
                b["date"],
                b["amount"],
            )
        )
else:
    with open(OUT, "w", encoding="utf-8") as f:
        for b in rows:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    print(f"[4] 影子落盘 {OUT} {len(rows)} 条")

if COMMIT:
    edges = [b for b in rows if b["both_sec"]]
    with c._session() as s:
        s.run(
            "UNWIND $rows AS e MATCH (a:Security {name: e.subj_sec}), "
            "(b:Security {name: e.obj_sec}) "
            "MERGE (a)-[r:DOC_EVENT {rel: e.rel, dir: e.dir}]->(b) "
            "SET r.n = e.n, r.ts_last = e.ts_last, r.src_doc = e.docs[0], "
            "    r.amount = e.amount, r.date = e.date",
            rows=edges,
        )
    print(f"[5] 入图 DOC_EVENT {len(edges)} 条（人审放行）")
else:
    print("[5] 未入图（--commit 需人审后执行）")
