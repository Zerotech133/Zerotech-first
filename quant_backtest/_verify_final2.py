# -*- coding: utf-8 -*-
"""K-SPATIAL-01 最终验收（所有方向修正）"""

from neo4j import GraphDatabase

d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "KgNeo4j2026!"))


def run(title, cy, limit=12):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    with d.session() as s:
        rows = list(s.run(cy))
        print(f"  → {len(rows)} 行")
        for r in rows[:limit]:
            rec = dict(r)
            # 截长字符串
            for k, v in list(rec.items()):
                if isinstance(v, str) and len(v) > 55:
                    rec[k] = v[:52] + "..."
                if isinstance(v, list) and len(v) > 6:
                    rec[k] = v[:6] + ["..."]
            print(f"    {rec}")
    return rows


# ── 存量校验（基础） ──────────────────────────────────────
run(
    "【存量】TradeAction 总数（唯一约束保证）",
    "MATCH (n:TradeAction) RETURN count(n) AS total, count(DISTINCT n.id) AS uniq",
)
run(
    "【存量】TRADED_ON 旧边未动（双写兼容）",
    "MATCH ()-[r:TRADED_ON]->() RETURN count(r) AS n",
)

# ── Q1 完整正确方向 ──────────────────────────────────────
run(
    "【Q1】上海游资 20240207 净买 + 江苏注册地（完整时空链）",
    """
    MATCH (td:TradeDate {date:'20240207'})-[:HAS_TRADED]->(ta:TradeAction {side:0})
    MATCH (ta)-[:EXECUTED_BY]->(st:Seat)-[:SEAT_LOCATED_AT]->(loc:Location {province:'上海'})
    MATCH (ta)-[:TARGETS]->(sec:Security)-[:AREA_OF]->(rgn:Region {name:'江苏省'})
    RETURN st.name AS seat, sec.ts_code AS code, sec.name AS name, ta.amount AS amt
    ORDER BY ta.amount DESC LIMIT 15
    """,
)

# Q1 不带江苏过滤的对照
run(
    "【Q1 对照】上海游资 20240207 净买 TOP10（不加江苏过滤）",
    """
    MATCH (td:TradeDate {date:'20240207'})-[:HAS_TRADED]->(ta:TradeAction {side:0})
    MATCH (ta)-[:EXECUTED_BY]->(st:Seat)-[:SEAT_LOCATED_AT]->(loc:Location {province:'上海'})
    MATCH (ta)-[:TARGETS]->(sec:Security)
    RETURN st.name AS seat, sec.ts_code AS code, sec.name AS name, ta.amount AS amt
    ORDER BY ta.amount DESC LIMIT 10
    """,
)

# ── Q2 完整正确方向 ──────────────────────────────────────
run(
    "【Q2】章建平 side 偏好（全周期）",
    """
    MATCH (ta:TradeAction)-[:OCCUPIED_BY]->(hm:HotMoney {name:'章建平(章盟主)'})
    RETURN ta.side AS side, count(ta) AS n, sum(ta.amount) AS total
    ORDER BY side
    """,
)

run(
    "【Q2-b】章建平 20240207 当日 side 偏好 + 标的样例",
    """
    MATCH (td:TradeDate {date:'20240207'})-[:HAS_TRADED]->(ta:TradeAction)
    MATCH (ta)-[:OCCUPIED_BY]->(hm:HotMoney {name:'章建平(章盟主)'})
    MATCH (ta)-[:TARGETS]->(sec:Security)
    RETURN ta.side AS side, count(ta) AS n, sum(ta.amount) AS total,
           collect(DISTINCT sec.ts_code) AS codes
    ORDER BY side
    """,
)

# ── Q3 完整正确方向 ──────────────────────────────────────
run(
    "【Q3】20240207 被 3+ 省席位盯上的标的 TOP10",
    """
    MATCH (td:TradeDate {date:'20240207'})-[:HAS_TRADED]->(ta:TradeAction)
    MATCH (ta)-[:EXECUTED_BY]->(st:Seat)-[:SEAT_LOCATED_AT]->(loc:Location)
    MATCH (ta)-[:TARGETS]->(sec:Security)
    WITH sec, count(DISTINCT loc.province) AS n_prov, count(DISTINCT ta) AS n_ta,
         collect(DISTINCT loc.province) AS provs
    WHERE n_prov >= 3 AND n_ta >= 5
    RETURN sec.ts_code AS code, sec.name AS name, n_prov, n_ta, provs
    ORDER BY n_prov DESC LIMIT 10
    """,
)

# ── Bonus：双时态 & 地理锚点验证 ─────────────────────────
run(
    "【Bonus】knowledge_time 双时态字段存在？",
    "MATCH (n:TradeAction) RETURN n.knowledge_time AS kt LIMIT 1",
)
run(
    "【Bonus】Seat→Location 样例（地理锚点）",
    "MATCH (st:Seat)-[:SEAT_LOCATED_AT]->(loc:Location) RETURN st.name AS seat, loc.province AS p, loc.city AS c LIMIT 5",
)
run(
    "【Bonus】Location kind 分布",
    "MATCH (loc:Location) RETURN loc.kind AS kind, count(loc) AS n ORDER BY n DESC",
)

d.close()
print("\n" + "=" * 60)
print("  ✅ K-SPATIAL-01 验收完成")
print("=" * 60)
