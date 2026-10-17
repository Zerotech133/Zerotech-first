# -*- coding: utf-8 -*-
"""kg_analysis.py — 知识图谱每日 GDS 分析任务（2026-10-16 上线）

流程：GDS 投影核心子图 → Louvain 社区检测 + PageRank 影响力 + Degree 活跃度
     → 写回图属性（下游 Cypher 直接可查）→ 导出 parquet 快照（进特征湖，供
     XGBoost/LightGBM 等模型消费）。

用法：
    python kg_analysis.py            # 全流程
    python kg_analysis.py --dry-run  # 只验证连接与投影规模，不写不导

调度：计划任务 KGAnalysis（每日 07:00，KgDailySync 06:20 之后）。
"""
import io
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_pw():
    for p in (os.path.join(HERE, ".env.local"),):
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                for ln in f:
                    if ln.startswith("KG_NEO4J_PASSWORD="):
                        return ln.split("=", 1)[1].strip()
    return os.environ.get("KG_NEO4J_PASSWORD", "")


PROJ = "kg_core"
# 核心分析子图：主体（公司/证券/人/机构/产品/主题），跳过海量事件明细节点
# K-STOCK-426 教训与定稿：Concept 曾一次性并入投影——430 个概念枢纽把
# Louvain 吸成巨社区（Security.community_id 全域漂移），kg_boost 冒烟
# 5596 条 buy（权威账 57 条），社区分位区分度塌方，已回滚。定稿方案：
# 主投影维持 6 标签（社区结构稳定）；Concept pagerank 走独立子投影
# （Security+Concept+MAPS_TO 有向图 stream 写回 Concept.pagerank），
# parquet 导出后拼装 Concept 行（community_id=-1 占位）——kg_boost 概念
# 热度从 MAPS_TO 入度升级为 pagerank 分位，社区打分零行为变化。
NODE_LABELS = ["Company", "Security", "Person", "Organization", "Product", "Theme"]
REL_TYPES = ["HOLDS", "SERVES_AS", "USES_TECH", "PRODUCES", "INVOLVES",
             "TRADED_ON", "MAPS_TO", "SUBSIDIARY_OF", "SUPPLIES_TO", "COMPETES_WITH"]
OUT_DIR = os.path.join(HERE, "data", "kg_analysis")


def _concept_pagerank(s):
    """Concept pagerank 独立子投影（K-STOCK-426 定稿）。

    Security+Concept+MAPS_TO 有向图 → pageRank.stream → 仅写回
    Concept.pagerank。主投影社区结构零扰动；概念热度升级为图谱
    重要性分位（kg_boost 消费），旧 MAPS_TO 入度保留为降级路径。
    """
    cproj = "kg_concept"
    s.run(f"CALL gds.graph.exists('{cproj}') YIELD exists "
          "WITH exists WHERE exists CALL gds.graph.drop($p) "
          "YIELD graphName RETURN graphName", p=cproj).consume()
    proj = s.run(
        f"CALL gds.graph.project('{cproj}', ['Security','Concept'], ['MAPS_TO']) "
        "YIELD nodeCount, relationshipCount "
        "RETURN nodeCount, relationshipCount").single()
    print(f"[concept-proj] {cproj}: {proj['nodeCount']} nodes / "
          f"{proj['relationshipCount']} rels")
    out = s.run(
        f"CALL gds.pageRank.stream('{cproj}') "
        "YIELD nodeId, score "
        "WITH gds.util.asNode(nodeId) AS n, score "
        "WHERE n:Concept SET n.pagerank = score "
        "RETURN count(n) AS written").single()
    s.run(f"CALL gds.graph.drop('{cproj}')").consume()
    print(f"[concept] Concept.pagerank 写回 {out['written']} 个（独立子投影，社区零扰动）")


def main(dry_run=False):
    pw = _load_pw()
    if not pw:
        print("[error] KG_NEO4J_PASSWORD 未配置")
        return 3
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", pw))

    with drv.session() as s:
        # 0) 清理旧投影
        s.run(f"CALL gds.graph.exists('{PROJ}') YIELD exists "
              "WITH exists WHERE exists CALL gds.graph.drop($p) YIELD graphName RETURN graphName",
              p=PROJ).consume()

        # 0.5) schema 交集：GDS 投影要求所有声明的标签/关系类型必须存在
        have_labels = {d["l"] for d in s.run("CALL db.labels() YIELD label AS l RETURN l").data()}
        have_rels = {d["t"] for d in s.run("CALL db.relationshipTypes() YIELD relationshipType AS t RETURN t").data()}
        labels = [x for x in NODE_LABELS if x in have_labels]
        rels = [x for x in REL_TYPES if x in have_rels]
        skipped_l = [x for x in NODE_LABELS if x not in labels]
        skipped_r = [x for x in REL_TYPES if x not in rels]
        if skipped_l or skipped_r:
            print(f"[proj] 跳过不存在的标签 {skipped_l} / 关系 {skipped_r}")
        if not labels or not rels:
            print("[error] 标签/关系交集为空")
            return 2

        # 1) 投影（仅存在的标签/关系类型会被收录）
        proj = s.run(
            f"""
            CALL gds.graph.project('{PROJ}',
              [{','.join(repr(x) for x in labels)}],
              [{','.join(repr(x) for x in rels)}])
            YIELD graphName, nodeCount, relationshipCount
            RETURN graphName, nodeCount, relationshipCount
            """).single()
        n, r = proj["nodeCount"], proj["relationshipCount"]
        print(f"[proj] {PROJ}: {n} nodes / {r} rels")
        if n == 0:
            print("[error] 投影为空：检查标签/关系类型拼写")
            return 2
        if dry_run:
            s.run(f"CALL gds.graph.drop('{PROJ}')").consume()
            print("[dry-run] 投影已丢弃，流程结束")
            return 0

        # 2) 三算法写回（社区/影响力/活跃度）
        lou = s.run(f"CALL gds.louvain.write('{PROJ}', {{writeProperty:'community_id'}}) "
                    "YIELD communityCount, modularity RETURN communityCount, modularity").single()
        pr = s.run(f"CALL gds.pageRank.write('{PROJ}', {{writeProperty:'pagerank'}}) "
                   "YIELD nodePropertiesWritten RETURN nodePropertiesWritten").single()
        dg = s.run(f"CALL gds.degree.write('{PROJ}', {{writeProperty:'degree_kg'}}) "
                   "YIELD nodePropertiesWritten RETURN nodePropertiesWritten").single()
        print(f"[algo] louvain: {lou['communityCount']} 社区 (modularity={lou['modularity']:.4f}) | "
              f"pagerank 写回 {pr['nodePropertiesWritten']} | degree 写回 {dg['nodePropertiesWritten']}")

        # 2.5) Concept pagerank 独立子投影（K-STOCK-426 定稿）
        _concept_pagerank(s)

        # 3) 导出 top 快照 → parquet 湖
        os.makedirs(OUT_DIR, exist_ok=True)
        rows = s.run(
            f"""
            MATCH (x) WHERE x.pagerank IS NOT NULL
            RETURN coalesce(labels(x)[0],'?') AS label,
                   coalesce(x.name, x.symbol, x.code, toString(id(x))) AS name,
                   x.pagerank AS pagerank, x.community_id AS community_id,
                   x.degree_kg AS degree_kg
            """).data()
    drv.close()

    import pandas as pd
    df = pd.DataFrame(rows).sort_values("pagerank", ascending=False)
    out = os.path.join(OUT_DIR, f"kg_scores_{date.today().isoformat()}.parquet")
    df.to_parquet(out, index=False)
    top3 = df.head(3)[["label", "name", "pagerank"]].values.tolist()
    print(f"[export] {len(df)} 行 → {out}")
    print("[top3]", top3)
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
