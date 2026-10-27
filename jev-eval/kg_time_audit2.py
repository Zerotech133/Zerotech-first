# -*- coding: utf-8 -*-
"""KG 时序研究补充抽样：SERVES_AS 任职期 / HOLDS 报告期序列 / gnet 边 s 覆盖"""

import sys

sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
import kg_portal as KP

print("== SERVES_AS 任职期样本（600519.SH） ==")
for r in KP._q(
    "MATCH (p:Person)-[x:SERVES_AS]->(:Company)<-[:OF_COMPANY]-"
    "(:Security {ts_code:'600519.SH'}) "
    "RETURN p.name AS nm, x.role AS role, x.validStart AS vs, "
    "x.validEnd AS ve LIMIT 8"
):
    print("  %s | %s | %s → %s" % (r["nm"], r["role"], r["vs"], r["ve"]))

print("== HOLDS 8 报告期 ==")
print(
    " ",
    [
        r["p"]
        for r in KP._q(
            "MATCH ()-[h:HOLDS]->() RETURN DISTINCT h.validStart AS p ORDER BY p DESC"
        )
    ],
)

print("== map_graph 边 s 序列规模（w>=2） ==")
mg = KP.api("map_graph") if hasattr(KP, "api") else None
mg = KP.map_graph(min_w=2)
es = mg.get("edges", [])
with_s = [e for e in es if e.get("s")]
lens = sorted(len(e["s"]) for e in with_s)
print(
    "  边总数=%d 带 s=%d  s长度中位=%s 最长=%s"
    % (
        len(es),
        len(with_s),
        lens[len(lens) // 2] if lens else 0,
        lens[-1] if lens else 0,
    )
)
print("  样本边:", [(e["a"], e["b"], len(e["s"])) for e in with_s[:3]])

print("== TRADED_ON 时间分桶（月度笔数近 12 月） ==")
for r in KP._q(
    "MATCH ()-[t:TRADED_ON]->() WHERE t.validStart >= '20250901' "
    "RETURN substring(t.validStart,0,6) AS m, count(*) AS n "
    "ORDER BY m"
):
    print("  %s %d" % (r["m"], r["n"]))
