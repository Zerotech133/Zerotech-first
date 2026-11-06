# -*- coding: utf-8 -*-
"""Competency Questions（能力问句）注册表 — C3 治理能力回归。

本体（TBox）不是写完就不动的。任何一次删类型 / 断边 / 改 schema，都可能让
图谱"答不出"原本能答的业务问题。CQ 把"图谱必须能回答的问题"固化成 Cypher
断言，纳入 CI：本体改动若破坏某条 CQ 的引用类型或实例，CI 立即红灯。

设计（与 C2 gate 同哲学，但职责不同）：
- 静态守卫（始终运行，无需 Neo4j）：每条 CQ 的 Cypher 引用的节点标签 /
  关系类型必须仍存在于 ontology.NODE_TYPES / EDGE_TYPES。删类型即回归。
- 实时守卫（仅连上 Neo4j 时运行）：`live_non_empty=True` 的 CQ 还要求该能力
  在真实图里确有实例（捕捉"类型在、但建图断流"的隐性退化）。
- 无 DB / 无 driver 时实时部分 SKIP（不 FAIL）——可用性由 gate() 负责，
  CQ 只管"能力完整性"，二者正交。

每条 CQ 带治理元数据（owner / rfc），归属与 ontology 治理面一致。
"""

CQ_REGISTRY_VERSION = "1.0.0"
CQ_DEFAULT_RFC = "RFC-KG-CQ"

# owner 按 CQ 主域 steward（与 ontology.DOMAIN_STEWARD / EDGE_STEWARD 对齐）
# 市场数据/公司人物 -> KGMARKET1；资金 -> KGCAPITAL1；事件 -> KGEVENT1；
# 情报推理/情报本体 -> KGMAP1；实验域 -> KGSHADOW1。

CQS = [
    # ── 结构骨架（live_non_empty：建图必有的主干）──
    dict(
        id="CQ-001",
        question="给定证券，能否解析出其上市公司主体（OF_COMPANY 主干）？",
        domain="结构",
        cypher="MATCH (s:Security)-[:OF_COMPANY]->(c:Company) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGMARKET1",
        rfc="RFC-KG-001",
    ),
    dict(
        id="CQ-002",
        question="标的能否同时解析行业、地域、申万一级（三条结构边齐备）？",
        domain="结构",
        cypher=("MATCH (s:Security)-[:INDUSTRY_OF]->(:Industry), "
                "(s)-[:AREA_OF]->(:Region), "
                "(s)-[:SW_L1_OF]->(:SwL1Industry) "
                "RETURN count(DISTINCT s) AS c"),
        live_non_empty=True,
        owner="KGMARKET1",
        rfc="RFC-KG-002",
    ),
    dict(
        id="CQ-003",
        question="能否回答『某席位交易了哪些标的』（TRADED_ON 龙虎榜）？",
        domain="资金",
        cypher="MATCH (seat:Seat)-[:TRADED_ON]->(s:Security) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGCAPITAL1",
        rfc="RFC-KG-003",
    ),
    dict(
        id="CQ-004",
        question="能否回答『某公司被哪些机构/股东持有』（HOLDS 十大流通股东）？",
        domain="资金",
        cypher="MATCH (o:Organization)-[:HOLDS]->(s:Security) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGCAPITAL1",
        rfc="RFC-KG-004",
    ),
    dict(
        id="CQ-005",
        question="能否解析游资主体与其占用席位（OCCUPIES 游资词表）？",
        domain="资金",
        cypher="MATCH (h:HotMoney)-[:OCCUPIES]->(seat:Seat) RETURN count(*) AS c",
        live_non_empty=False,
        owner="KGCAPITAL1",
        rfc="RFC-KG-005",
    ),
    # ── 市场事件（窗口依赖，仅静态守卫，避免 CI 随行情波动误红）──
    dict(
        id="CQ-006",
        question="能否聚合某标的的涨跌停/量异/缠论/形态/榜后多类盘口事件？",
        domain="事件",
        cypher=("MATCH (s:Security)-[:HAS_LIMIT|HAS_VOL_EVENT|HAS_CANDLE|"
                "HAS_STRUCTURE|HAS_PATTERN|HAS_TOPGAP]->(e:MarketEvent) "
                "RETURN count(*) AS c"),
        live_non_empty=False,
        owner="KGEVENT1",
        rfc="RFC-KG-006",
    ),
    dict(
        id="CQ-007",
        question="盘口事件能否关联到技术手法（含监管映射 reg_tag）？",
        domain="事件",
        cypher="MATCH (e:MarketEvent)-[:USES_TECH]->(t:Technique) RETURN count(*) AS c",
        live_non_empty=False,
        owner="KGEVENT1",
        rfc="RFC-KG-007",
    ),
    # ── 时间骨架 ──
    dict(
        id="CQ-008",
        question="交易日日历链是否连续可达（NEXT_TRADE_DAY 时序遍历）？",
        domain="时间",
        cypher="MATCH (d:TradeDate)-[:NEXT_TRADE_DAY]->(n:TradeDate) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGMARKET1",
        rfc="RFC-KG-008",
    ),
    # ── 相似骨架 ──
    dict(
        id="CQ-009",
        question="能否回答『与某标的 K 线形态最相似的标的』（SIMILAR_K）？",
        domain="相似",
        cypher="MATCH (s:Security)-[:SIMILAR_K]->(t:Security) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGMARKET1",
        rfc="RFC-KG-009",
    ),
    # ── 分类骨架 ──
    dict(
        id="CQ-010",
        question="标的→概念→概念层级（子→父）能否贯通（MAPS_TO/BELONGS_TO）？",
        domain="分类",
        cypher=("MATCH (s:Security)-[:MAPS_TO]->(c:Concept)-[:BELONGS_TO*1..3]->(p:Concept) "
                "RETURN count(*) AS c"),
        live_non_empty=True,
        owner="KGMARKET1",
        rfc="RFC-KG-010",
    ),
    # ── 情报推理（稀疏域，仅静态守卫）──
    dict(
        id="CQ-011",
        question="公告事件能否解析其涉入标的（INVOLVES）？",
        domain="情报",
        cypher="MATCH (ev:Event)-[:INVOLVES]->(s:Security) RETURN count(*) AS c",
        live_non_empty=False,
        owner="KGMAP1",
        rfc="RFC-KG-011",
    ),
    dict(
        id="CQ-012",
        question="情报条目能否解析提及标的及其来源（MENTIONS/REPORTED_BY）？",
        domain="情报",
        cypher=("MATCH (it:IntelItem)-[:MENTIONS]->(s:Security), "
                "(it)-[:REPORTED_BY]->(src:Source) "
                "RETURN count(*) AS c"),
        live_non_empty=False,
        owner="KGMAP1",
        rfc="RFC-KG-012",
    ),
    dict(
        id="CQ-013",
        question="哪些情报/事件印证或反证了假设（SUPPORTS/CONTRADICTS 论证终点）？",
        domain="推理",
        cypher="MATCH (x)-[:SUPPORTS|CONTRADICTS]->(h:Hypothesis) RETURN count(*) AS c",
        live_non_empty=False,
        owner="KGMAP1",
        rfc="RFC-KG-013",
    ),
    # ── 情报本体（人工维护，稀疏，仅静态守卫）──
    dict(
        id="CQ-014",
        question="产业主题→环节→供货公司的传导链能否贯通（CONDUCTION/SUB_OF/SUPPLIES_TO）？",
        domain="情报本体",
        cypher=("MATCH (th:Theme)-[:CONDUCTION]->(sg:Segment)-[:SUB_OF*0..3]->(sg2:Segment)"
                "<-[:SUPPLIES_TO]-(co:Company) RETURN count(*) AS c"),
        live_non_empty=False,
        owner="KGMAP1",
        rfc="RFC-KG-014",
    ),
    dict(
        id="CQ-015",
        question="评论主体对标的的派系评估能否解析（ASSESSED 派系评估）？",
        domain="情报本体",
        cypher="MATCH (cm:Commentator)-[:ASSESSED]->(s:Security) RETURN count(*) AS c",
        live_non_empty=False,
        owner="KGMAP1",
        rfc="RFC-KG-015",
    ),
    # ── 文档线（K266 5 分钟节奏，建图必建）──
    dict(
        id="CQ-016",
        question="文档抽取出的跨标的关系边能否检索（DOC_EVENT 跨标的关系）？",
        domain="文档线",
        cypher="MATCH (a:Security)-[:DOC_EVENT]->(b:Security) RETURN count(*) AS c",
        live_non_empty=True,
        owner="KGMAP1",
        rfc="RFC-KG-016",
    ),
]


def all_cqs():
    """返回 CQ 列表（调用方可按需筛选，如按 owner/domain）。"""
    return CQS
