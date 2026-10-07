# -*- coding: utf-8 -*-
"""K266 M3-B 入图（拍板 2026-10-08「先B后A」）：影子 companies×direction 聚合挂 Security 属性。
口径：篇级 direction（利好=pos/利空=neg/其余含中性偏多=neu）平摊给该篇全部 companies
（已知噪声：非主主体公司被带方向；M3-A 篇级事件层再精化）。
映射三级：精确 name → aliases → CONTAINS 唯一命中；歧义/未命中跳过并计数（多为非 A 股实体）。
幂等：每次从影子全量重算后覆盖写（doc_* 属性），可随影子续跑重刷。
用法: python -X utf8 k266_m3b_ingest.py [--dry]"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kg_client import KgClient

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW = os.path.join(BASE, "data", "k266_docs", "structure_shadow.jsonl")
DRY = "--dry" in sys.argv

# 1) 影子全量 → 公司×方向计数
agg = {}  # security_name -> {"pos":n,"neg":n,"neu":n,"docs":[...]}
n_docs = n_err = 0
with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("raw_ok"):
            n_err += 1
            continue
        n_docs += 1
        d = r.get("direction")
        key = "pos" if d == "利好" else ("neg" if d == "利空" else "neu")
        for c in r.get("companies", []):
            c = (c or "").strip()
            if not c:
                continue
            a = agg.setdefault(c, {"pos": 0, "neg": 0, "neu": 0, "docs": []})
            a[key] += 1
            if len(a["docs"]) < 3:
                a["docs"].append(r.get("doc_id"))
print(f"[1] 影子 raw_ok={n_docs} err={n_err} 去重公司名={len(agg)}")

# 2) 图侧 Security name/aliases → 三级映射
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

mapped = {}   # security_name -> agg（多公司名映射到同一 Security 合并）
st = {"exact": 0, "alias": 0, "contains": 0, "ambig": 0, "miss": 0}
miss_list = []
for cname, a in agg.items():
    tgt = None
    if cname in names:
        tgt, st["exact"] = cname, st["exact"] + 1
    elif cname in alias_map:
        tgt, st["alias"] = alias_map[cname], st["alias"] + 1
    else:
        hits = [n for n in names if cname in n or n in cname]
        if len(hits) == 1:
            tgt, st["contains"] = hits[0], st["contains"] + 1
        elif len(hits) > 1:
            st["ambig"] += 1
            miss_list.append((cname, "歧义:" + ",".join(hits[:3])))
            continue
        else:
            st["miss"] += 1
            if len(miss_list) < 20:
                miss_list.append((cname, "未命中"))
            continue
    m = mapped.setdefault(tgt, {"pos": 0, "neg": 0, "neu": 0, "docs": []})
    m["pos"] += a["pos"]; m["neg"] += a["neg"]; m["neu"] += a["neu"]
    m["docs"].extend(a["docs"])
print(f"[2] 映射 {st} -> Security 节点 {len(mapped)}")

# 3) 写图（UNWIND 批量，doc_* 属性覆盖）
rows = [{"n": n, "pos": m["pos"], "neg": m["neg"], "neu": m["neu"],
         "cnt": m["pos"] + m["neg"] + m["neu"], "last": time.strftime("%Y-%m-%d")}
        for n, m in mapped.items()]
if DRY:
    print("[3] DRY 不写图，top10 预览：")
    for r in sorted(rows, key=lambda x: -x["cnt"])[:10]:
        print("   %-12s pos=%d neg=%d neu=%d" % (r["n"], r["pos"], r["neg"], r["neu"]))
else:
    with c._session() as s:
        s.run(
            "UNWIND $rows AS r MATCH (sec:Security {name: r.n}) "
            "SET sec.doc_pos = r.pos, sec.doc_neg = r.neg, sec.doc_neu = r.neu, "
            "    sec.doc_n = r.cnt, sec.doc_last = r.last", rows=rows)
    print(f"[3] 写图完成 {len(rows)} 节点")
    chk = c._session().__enter__()
    try:
        got = chk.run("MATCH (s:Security) WHERE s.doc_n IS NOT NULL "
                      "RETURN count(s) AS n, sum(s.doc_pos) AS p, sum(s.doc_neg) AS g, "
                      "sum(s.doc_neu) AS e").data()[0]
        print(f"[4] 验证 doc_n 节点={got['n']} pos={got['p']} neg={got['g']} neu={got['e']}")
    finally:
        chk.close()
print("--- 未映射样例（歧义+未命中 前 20） ---")
for cname, why in miss_list[:20]:
    print(f"   {cname} -> {why}")
