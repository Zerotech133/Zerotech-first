# -*- coding: utf-8 -*-
"""Neo4j 客户端封装：连接 + DDL + 批量 upsert 节点/边 + 计数。

设计原则：
· 懒加载 neo4j python driver（kg_schema 自检不需要装 driver）
· 用 UNWIND + MERGE 批量 upsert（避免逐条往返）
· 关系边 MERGE 必须带 validStart/validEnd（时序图谱铁律）
· 全程事务原子（失败回滚，不污染图谱）

用法：
  from kg_client import KgClient
  cli = KgClient()
  cli.ensure_schema()
  cli.upsert_nodes("Security", ["ts_code","name"], rows)
  cli.upsert_edge("Security","ts_code","Industry","name","INDUSTRY_OF",
                  edge_rows)
  cli.stats()
"""
import sys
from datetime import datetime

import kg_schema as S


class KgClient:
    """Neo4j 客户端薄封装。"""

    def __init__(self, uri=None, user=None, password=None, db=None,
                 retry=6, retry_wait=15):
        try:
            from neo4j import GraphDatabase
        except ImportError:
            raise SystemExit(
                "neo4j python driver 未装。请执行：\n"
                "  pip install neo4j")
        self._drv = GraphDatabase.driver(
            uri or S.NEO4J_URI,
            auth=(user or S.NEO4J_USER, password or S.NEO4J_PASSWORD))
        self._db = db or S.NEO4J_DB
        # 连通性自检（带重试：工作站开机后容器可能还在启动，最长等 75s）
        import sys as _sys
        import time as _time
        last_err = None
        for attempt in range(retry):
            try:
                with self._session() as s:
                    r = s.run("RETURN 1 AS v").single()
                    if r["v"] != 1:
                        raise RuntimeError("Neo4j 连通自检失败")
                return
            except Exception as e:
                last_err = e
                if attempt < retry - 1:
                    print("Neo4j 未就绪(%.0fs 后重试 %d/%d): %s"
                          % (retry_wait, attempt + 1, retry - 1, e),
                          file=_sys.stderr)
                    _time.sleep(retry_wait)
        raise RuntimeError("Neo4j 连接失败（已重试 %d 次）: %s"
                           % (retry, last_err))

    def _session(self):
        return self._drv.session(database=self._db)

    def close(self):
        self._drv.close()

    # ── DDL ──────────────────────────────────────────────────
    def ensure_schema(self):
        """顺序执行约束 + 索引 DDL（IF NOT EXISTS 幂等）。"""
        with self._session() as s:
            for cy in S.all_ddl():
                s.run(cy)
        print("DDL 完成: %d 约束 + %d 索引"
              % (len(S.CONSTRAINTS), len(S.INDEXES)))

    # ── 节点 upsert ──────────────────────────────────────────
    def upsert_nodes(self, label, key_cols, rows, batch=2000):
        """批量 upsert 节点。
        label: 节点标签
        key_cols: 用于 MERGE 的唯一键列名列表（如 ["ts_code"]）
        rows: dict 列表（含 key_cols + 其他属性；None 值会删除属性）

        用 SET n += row（map 风格）：不同行 keys 不一致也能正确写入，
        None 值会删除该属性（Neo4j 5.x 语义），避免 rows[0] 决定 SET 子句。
        """
        if not rows:
            return 0
        merge_clause = ", ".join("%s: row.%s" % (c, c) for c in key_cols)
        cy = ("UNWIND $rows AS row "
              "MERGE (n:%s {%s}) "
              "SET n += row" % (label, merge_clause))
        n = 0
        with self._session() as s:
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                s.run(cy, rows=chunk)
                n += len(chunk)
        return n

    # ── 关系边 upsert ────────────────────────────────────────
    def upsert_edge(self, src_lbl, src_key, dst_lbl, dst_key,
                    rel_type, edge_rows, batch=2000):
        """批量 upsert 关系边（带 validStart/validEnd 时序区间）。

        edge_rows: [{src:.., dst:.., validStart:.., validEnd:.., source:..,
                     updated_at:.., <其他属性>}]
        MERGE 键：(src, dst, validStart)——允许同一对节点在不同时间段
                  有多条有效关系（如退市后重新上市）。
        """
        if not edge_rows:
            return 0
        # 动态构造 SET 子句（除 src/dst 外的所有属性）
        sample = edge_rows[0]
        extra_cols = [c for c in sample if c not in ("src", "dst")]
        set_clause = ", ".join("r.%s = row.%s" % (c, c) for c in extra_cols)
        cy = (
            "UNWIND $rows AS row "
            "MATCH (s:%s {%s: row.src}) "
            "MATCH (d:%s {%s: row.dst}) "
            "MERGE (s)-[r:%s {validStart: row.validStart}]->(d) "
            "SET %s" % (src_lbl, src_key, dst_lbl, dst_key,
                        rel_type, set_clause)
        )
        n = 0
        with self._session() as s:
            for i in range(0, len(edge_rows), batch):
                chunk = edge_rows[i:i + batch]
                s.run(cy, rows=chunk)
                n += len(chunk)
        return n

    # ── 统计 ─────────────────────────────────────────────────
    def stats(self):
        """打印节点/边计数概览。"""
        with self._session() as s:
            print("=== 知识图谱统计 ===")
            for lbl in [S.LBL_SECURITY, S.LBL_COMPANY, S.LBL_INDUSTRY,
                        S.LBL_SW_L1, S.LBL_REGION, S.LBL_PERSON,
                        S.LBL_PRODUCT, S.LBL_MARKET_INDEX,
                        S.LBL_HOTMONEY, S.LBL_SEAT, S.LBL_MARKET_EVENT,
                        S.LBL_CONCEPT,
                        # 情报层实体（K218 仪表扩容：引擎三层全量可见）
                        S.LBL_TECHNIQUE, S.LBL_TRADE_DATE,
                        S.LBL_THEME, S.LBL_SEGMENT,
                        S.LBL_COMMENTATOR, S.LBL_EXP_COMMUNITY]:
                r = s.run("MATCH (n:%s) RETURN count(n) AS c" % lbl).single()
                print("  节点 %-15s %d" % (lbl, r["c"]))
            for lbl in ["Event"]:
                r = s.run("MATCH (n:%s) RETURN count(n) AS c" % lbl).single()
                print("  节点 %-15s %d" % (lbl, r["c"]))
            for rel in [S.REL_OF_COMPANY, S.REL_INDUSTRY_OF, S.REL_AREA_OF,
                        S.REL_SERVES_AS, S.REL_SW_L1_OF, S.REL_PRODUCES,
                        S.REL_CORRELATES, S.REL_BENCHMARK_OF,
                        S.REL_INVOLVES,
                        S.REL_OCCUPIES, S.REL_TRADED_ON,
                        S.REL_HAS_STRUCTURE, S.REL_HAS_VOL_EVENT,
                        S.REL_HAS_CANDLE,
                        # K270 补齐（测试网抓漏）：B 类名单/相似/持股五边型
                        S.REL_HAS_LIMIT, S.REL_HAS_TOPGAP,
                        S.REL_HOLDS, S.REL_SIMILAR_K,
                        S.REL_DRIVE, S.REL_BELONGS_TO, S.REL_REQUIRES,
                        S.REL_MAPS_TO,
                        # 情报层边（K218 仪表扩容：形态/传导/集团/共识）
                        S.REL_HAS_PATTERN, S.REL_USES_TECH, S.REL_ON_DATE,
                        S.REL_CONDUCTION, S.REL_SUB_OF, S.REL_SUPPLIES_TO,
                        S.REL_IN_GROUP, S.REL_ASSESSED,
                        # 概念级语义边（K265-P1：LLM 供应链/需求带动）
                        S.REL_SUPPLIES, S.REL_DRIVES,
                        # 情报线边族（K264 跨项目收录：写入器在 KGStockAnalysis，
                        # 双版本设计 EXP_=实验域/无前缀=转正域，≈100.5 万条）
                        S.REL_MENTIONS, S.REL_REPORTED_BY,
                        S.REL_SUPPORTS, S.REL_CONTRADICTS,
                        S.REL_EXP_MENTIONS, S.REL_EXP_REPORTED_BY,
                        S.REL_EXP_SUPPORTS, S.REL_EXP_CONTRADICTS,
                        S.REL_EXP_ASSESSES]:
                r = s.run(
                    "MATCH ()-[r:%s]->() RETURN count(r) AS c" % rel).single()
                print("  边   %-15s %d" % (rel, r["c"]))

    def sample_subgraph(self, ts_code, depth=1):
        """抽样某个标的的 1 跳子图（验证建图正确性）。

        用扁平 Cypher 返回（不用 path），避免 neo4j python driver 6.x
        Node.labels 私有 API 问题。depth>1 时退化为 1 跳（保守起见）。
        """
        cy = (
            "MATCH (n:Security {ts_code: $code}) "
            "OPTIONAL MATCH (n)-[r]-(m) "
            "RETURN n.name AS name, n.industry AS industry, n.area AS area, "
            "n.list_date AS ld, "
            "type(r) AS rt, labels(m) AS ml, "
            "coalesce(m.name, m.com_name, m.ts_code, m.index_code, "
            "coalesce(m.kind,'') + coalesce(m.date,'')) AS dst, "
            "properties(r) AS rp "
            "ORDER BY rt LIMIT 50")
        with self._session() as s:
            rows = list(s.run(cy, code=ts_code))
        if not rows:
            print("=== %s 未找到 ===" % ts_code)
            return
        head = rows[0]
        print("=== %s 子图（1 跳, %d 条边）===" % (ts_code, len(
            [r for r in rows if r["rt"]])))
        print("  节点 name=%s industry=%s area=%s list_date=%s"
              % (head["name"], head["industry"], head["area"], head["ld"]))
        seen = set()
        for r in rows:
            if not r["rt"]:
                continue
            key = (r["rt"], r["dst"])
            if key in seen:
                continue
            seen.add(key)
            vs = r["rp"]
            vs_str = " ".join("%s=%s" % (k, vs[k])
                              for k in ["validStart", "validEnd"]
                              if k in vs)
            print("  -[%s %s]-> %s %s"
                  % (r["rt"], vs_str, r["ml"], r["dst"]))

        # Phase 2-A：附加 Person 二跳查询（Security→Company←Person）
        cy_p = (
            "MATCH (n:Security {ts_code: $code})"
            "-[:OF_COMPANY]->(c:Company)<-[s:SERVES_AS]-(p:Person) "
            "RETURN p.name AS pname, s.role AS role, "
            "s.validStart AS vs, s.validEnd AS ve "
            "ORDER BY role LIMIT 20")
        with self._session() as s:
            prows = list(s.run(cy_p, code=ts_code))
        if prows:
            print("  人物 (Person -[SERVES_AS]-> Company):")
            for r in prows:
                print("    %s  role=%s  [%s, %s]"
                      % (r["pname"], r["role"], r["vs"], r["ve"]))

        # Phase 2-C：附加 Product 二跳查询（Security→Company-[PRODUCES]->Product）
        cy_prod = (
            "MATCH (n:Security {ts_code: $code})"
            "-[:OF_COMPANY]->(c:Company)-[p:PRODUCES]->(prod:Product) "
            "RETURN prod.name AS pname, p.source AS psrc "
            "ORDER BY pname LIMIT 30")
        with self._session() as s:
            prodrows = list(s.run(cy_prod, code=ts_code))
        if prodrows:
            print("  产品 (Company -[PRODUCES]-> Product):")
            for r in prodrows:
                print("    %s  src=%s" % (r["pname"], r["psrc"]))

    # ── 产业链邻近查询（暂缓 SUPPLIES 显式边的替代能力）────────
    def supply_chain_peers(self, ts_code, top_n=15, min_shared=2):
        """产业链邻近：共享产品最多的公司（Product 二跳聚合）。

        替代显式 SUPPLIES 边（公告文本密度不足，暂缓 LLM 抽取）。
        Returns: [(ts_code, name, industry, shared_products, [产品名…])]
        """
        cy = (
            "MATCH (s:Security {ts_code: $c})"
            "-[:OF_COMPANY]->(c1:Company)-[:PRODUCES]->(p:Product)"
            "<-[:PRODUCES]-(c2:Company)<-[:OF_COMPANY]-(s2:Security) "
            "WHERE c1 <> c2 AND s2.name IS NOT NULL "
            "WITH s2, collect(DISTINCT p.name) AS prods "
            "WHERE size(prods) >= $min "
            "RETURN s2.ts_code AS tc, s2.name AS nm, s2.industry AS ind, "
            "size(prods) AS n, prods "
            "ORDER BY n DESC LIMIT $top")
        with self._session() as s:
            rows = list(s.run(cy, c=ts_code, min=min_shared, top=top_n))
        print("=== %s 产业链邻近 Top%d（共享产品数, min=%d）==="
              % (ts_code, len(rows), min_shared))
        for r in rows:
            prods_show = "/".join(sorted(r["prods"])[:5])
            print("  %-11s %-10s %-8s 共享%3d: %s"
                  % (r["tc"], r["nm"], r["ind"] or "-", r["n"], prods_show))
        return [(r["tc"], r["nm"], r["ind"], r["n"], r["prods"])
                for r in rows]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--peers", help="产业链邻近查询（ts_code）")
    ap.add_argument("--sample", help="个股 1 跳子图（ts_code）")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    cli = KgClient()
    if args.peers:
        cli.supply_chain_peers(args.peers, top_n=args.top)
    elif args.sample:
        cli.sample_subgraph(args.sample)
    else:
        cli.ensure_schema()
        cli.stats()
    cli.close()
