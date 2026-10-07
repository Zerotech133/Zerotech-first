# -*- coding: utf-8 -*-
"""KG 时序属性审计（图谱的时序关系不明显·专项研究）
输出：①关系类型 × 时间属性覆盖矩阵 ②节点类型时间键
③时序潜力抽样：HOLDS 多报告期 / TRADED_ON 按日序列 / 共现边 s
"""
import sys
sys.path.insert(0, r"E:\老田的AI助手\quant_backtest")
import kg_portal as KP


def q(cy, **kw):
    try:
        return KP._q(cy, **kw)
    except Exception as e:
        return [{"ERR": str(e)[:120]}]  # 豁免（C2批2）：一次性审计脚本，错误以 ERR 字段并入结果打印


print("== ① 关系类型 × 时间属性覆盖 ==")
types = [r["relationshipType"] for r in
         q("CALL db.relationshipTypes() YIELD relationshipType "
           "RETURN relationshipType")]
for t in sorted(types):
    cov = q("MATCH ()-[r:%s]->() RETURN count(r) AS n, "
            "count(r.validStart) AS vs" % t)[0]
    if "ERR" in cov:
        print("%-18s ERR %s" % (t, cov["ERR"]))
        continue
    ks = q("MATCH ()-[r:%s]->() RETURN keys(r) AS k LIMIT 3" % t)
    keyset = sorted({k for row in ks for k in row.get("k", [])})
    pct = (100.0 * cov["vs"] / cov["n"]) if cov["n"] else 0
    print("%-18s n=%-8d validStart=%-8d(%5.1f%%) keys=%s"
          % (t, cov["n"], cov["vs"], pct, keyset))

print()
print("== ② 节点类型时间键抽样 ==")
labels = [r["label"] for r in q("CALL db.labels() YIELD label RETURN label")]
for lb in sorted(labels):
    ks = q("MATCH (n:%s) RETURN keys(n) AS k LIMIT 3" % lb)
    keyset = sorted({k for row in ks for k in row.get("k", [])})
    tkeys = [k for k in keyset if any(s in k.lower()
             for s in ("date", "valid", "ts", "time", "期"))]
    if tkeys:
        print("%-14s 时间键: %s" % (lb, tkeys))

print()
print("== ③ 时序潜力抽样 ==")
r = q("MATCH ()-[h:HOLDS]->() RETURN count(DISTINCT h.validStart) "
      "AS periods, count(h) AS edges")[0]
print("HOLDS 报告期数=%s 边数=%s（多期股东变动潜力）" %
      (r.get("periods"), r.get("edges")))
r = q("MATCH ()-[t:TRADED_ON]->() RETURN min(t.validStart) AS a, "
      "max(t.validStart) AS b, count(t) AS n")[0]
print("TRADED_ON 覆盖 %s → %s 共 %s 笔（按日龙虎榜序列）" %
      (r.get("a"), r.get("b"), r.get("n")))
rows = q("MATCH (h:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->"
         "(:Security {ts_code:'002743.SZ'}) RETURN t.validStart AS d, "
         "h.name AS hm ORDER BY d DESC LIMIT 8")
print("002743.SZ 游资边时间线样本:")
for x in rows:
    print("  %s %s" % (x.get("d"), x.get("hm")))
r = q("MATCH (s:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent) "
      "RETURN count(r) AS n, count(r.confidence) AS conf")[0]
print("HAS_STRUCTURE n=%s conf=%s" % (r.get("n"), r.get("conf")))
