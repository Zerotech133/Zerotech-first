import os as _os_kgschema

_KG_NEO4J_URI = _os_kgschema.environ.get("KG_NEO4J_URI", "bolt://localhost:7687")

import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_neo4j_password() -> str:
    """Neo4j 口令解析（2026-10-27 EXEC-R46：禁止硬编码兜底）。

    旧实现兜底 `KgNeo4j2026!`（10-16 换机凭据）；2026-10-26 R5 轮换后旧口令失效，
    6 个 KG 计划任务（KgLeiden/KgFastRP/KgExtractShadow/KgEventShadow/KgDigest/
    KgLeidenSummary）因未注入 env 而落到旧兜底 → Neo4j Unauthorized → 全部 rc=1。
    顺序：进程 env KG_NEO4J_PASSWORD → 同目录 .env.local → 空串（由 KgClient 报明确错）。
    """
    pw = _os_kgschema.environ.get("KG_NEO4J_PASSWORD", "").strip()
    if pw:
        return pw
    env_local = os.path.join(HERE, ".env.local")
    try:
        with open(env_local, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("KG_NEO4J_PASSWORD="):
                    return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


# ── Neo4j 连接 ────────────────────────────────────────────────
NEO4J_URI = _KG_NEO4J_URI
# 凭据：env → .env.local（见 _load_neo4j_password；不再有硬编码旧值兜底）
NEO4J_USER = _os_kgschema.environ.get("KG_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _load_neo4j_password()
NEO4J_DB = "neo4j"  # community 版只有默认 db

# ── 节点标签 ──────────────────────────────────────────────────
LBL_SECURITY = "Security"  # 标的（A股/ETF/可转债；Phase 1 只建 A 股）
LBL_COMPANY = "Company"  # 上市公司实体
LBL_INDUSTRY = "Industry"  # 细分行业（来自 stock_basic.industry）
LBL_SW_L1 = "SwL1Industry"  # 申万一级行业（来自 sw_classify_l1）
LBL_REGION = "Region"  # 省份区域（来自 stock_basic.area）
LBL_PERSON = "Person"  # 人物（实控人/高管/董秘，Phase 2-A）
LBL_PRODUCT = "Product"  # 产品/业务（经营范围抽取，Phase 2-C）
LBL_MARKET_INDEX = "MarketIndex"  # 市场指数（A股宽基+全球，Phase 3-A）
LBL_HOTMONEY = "HotMoney"  # 游资/资金主体（CL-1，含散户集群如拉萨天团）
LBL_SEAT = "Seat"  # 券商营业部席位（CL-1）
LBL_MARKET_EVENT = "MarketEvent"  # 盘面博弈事件：缠论结构/量能事件（CL-1）
LBL_CONCEPT = "Concept"  # 概念层（M4，人工词表，不依赖 Product 碎片层）
# ── K-SPATIAL-01 时空关系打底（2026-10-19）───────────────────
LBL_TRADE_ACTION = (
    "TradeAction"  # 龙虎榜交易原子事件（ts_top_inst 原始行粒度，含 side 方向）
)
LBL_LOCATION = (
    "Location"  # 地理锚点（province/city/seat/virtual 四档，Seat→Location 时空映射）
)

# ── 关系类型 ──────────────────────────────────────────────────
REL_OF_COMPANY = "OF_COMPANY"  # Security -[OF_COMPANY]-> Company
REL_INDUSTRY_OF = "INDUSTRY_OF"  # Security -[INDUSTRY_OF]-> Industry
REL_AREA_OF = "AREA_OF"  # Security -[AREA_OF]-> Region
REL_SERVES_AS = "SERVES_AS"  # Person -[SERVES_AS {role}]-> Company (Phase 2-A)
REL_SW_L1_OF = "SW_L1_OF"  # Security -[SW_L1_OF]-> SwL1Industry (Phase 2-B)
REL_PRODUCES = "PRODUCES"  # Company -[PRODUCES]-> Product (Phase 2-C)
REL_CORRELATES = "CORRELATES_WITH"  # MarketIndex -[CORRELATES_WITH {corr,beta}]-> MarketIndex (Phase 3-A)
REL_BENCHMARK_OF = "BENCHMARK_OF"  # MarketIndex -[BENCHMARK_OF]-> SwL1Industry/Security 基准映射 (Phase 3-A)
REL_OCCUPIES = "OCCUPIES"  # HotMoney -[OCCUPIES]-> Seat 游资占用席位 (CL-1)
REL_TRADED_ON = "TRADED_ON"  # Seat -[TRADED_ON {net_buy/buy/sell}]-> Security 龙虎榜净买额（元）(CL-1)
REL_HAS_STRUCTURE = (
    "HAS_STRUCTURE"  # Security -[HAS_STRUCTURE]-> MarketEvent 缠论结构事件 (CL-1)
)
REL_HAS_VOL_EVENT = (
    "HAS_VOL_EVENT"  # Security -[HAS_VOL_EVENT]-> MarketEvent 量能事件 (CL-1)
)
REL_DRIVE = "DRIVE"  # Event -[DRIVE {p,lag_days}]-> MarketEvent 外部事件伴随盘面事件 (M2-W1，只算共现不判因果)
REL_BELONGS_TO = "BELONGS_TO"  # Concept -[BELONGS_TO]-> Concept 概念层级（子→父，M4）
REL_REQUIRES = "REQUIRES"  # Concept -[REQUIRES]-> Concept 依赖（人工边，M4）
REL_MAPS_TO = "MAPS_TO"  # Security -[MAPS_TO {via}]-> Concept 标的挂概念（M4，via=alias/industry）
REL_HAS_CANDLE = "HAS_CANDLE"  # Security -[HAS_CANDLE]-> MarketEvent 分时盘口事件（Batch 4，stk_mins 1 分钟线）
LBL_ORGANIZATION = "Organization"  # 机构/股东实体（B 类，top10_floatholders）
LBL_TECHNIQUE = "Technique"  # 手法节点（P3-b：candle type 对象化+监管映射）
LBL_TRADE_DATE = "TradeDate"  # 交易日一等节点（P3-d：date=yyyymmdd 唯一）
REL_HOLDS = "HOLDS"  # Organization -[HOLDS {ratio,shares}]-> Security 十大流通股东（B 类，按报告期快照）
REL_HAS_LIMIT = "HAS_LIMIT"  # Security -[HAS_LIMIT]-> MarketEvent 涨跌停名单事件（B 类 limit_list_d）
REL_HAS_TOPGAP = "HAS_TOPGAP"  # Security -[HAS_TOPGAP]-> MarketEvent 榜后差额事件（P2-① ts_top_list）
REL_SIMILAR_K = "SIMILAR_K"  # Security -[SIMILAR_K {dist,corr}]-> Security K线形态相似（P2-② 60日DTW，无向语义字典序定向）
REL_USES_TECH = "USES_TECH"  # MarketEvent -[USES_TECH]-> Technique 手法对象化（P3-b，kind=candle 事件挂手法）
REL_ON_DATE = "ON_DATE"  # MarketEvent/Event -[ON_DATE]-> TradeDate 事件挂交易日（P3-d）
REL_NEXT_TD = (
    "NEXT_TRADE_DAY"  # TradeDate -[NEXT_TRADE_DAY]-> TradeDate 交易日历链（P3-d）
)
LBL_EXP_COMMUNITY = (
    "EXP_Community"  # 席位社区/游资集团（K190-P2，Louvain 快照，json 为真相源）
)
REL_IN_GROUP = (
    "IN_GROUP"  # Seat -[IN_GROUP {role,source}]-> EXP_Community 集团成员（K190-P2）
)
REL_HAS_PATTERN = "HAS_PATTERN"  # Security -[HAS_PATTERN]-> MarketEvent(kind=pattern) 15 K线形态事件（K190-P1）
LBL_THEME = "Theme"  # 产业主题（K201-C：人形机器人/AI算力，情报本体传导链）
LBL_SEGMENT = "Segment"  # 产业链环节（K201-C，两级：一级环节/二级子环节）
REL_CONDUCTION = "CONDUCTION"  # Theme -[CONDUCTION {type}]-> Segment 传导边（K201-C，type=载体/物理约束/供应链）
REL_SUB_OF = "SUB_OF"  # Segment -[SUB_OF]-> Segment 二级子环节归属（K201-C）
REL_SUPPLIES_TO = "SUPPLIES_TO"  # Company -[SUPPLIES_TO {evidence,chain}]-> Segment 供货关系（K201-C，证据溯源）
LBL_COMMENTATOR = (
    "Commentator"  # 信息源/分析主体（K201-D：四派分析师/评论员，情报本体评估层）
)
REL_ASSESSED = "ASSESSED"  # Commentator -[ASSESSED {verdict,stars,theme,date}]-> Security 派系评估（K201-D）
REL_SUPPLIES = "SUPPLIES"  # Concept -[SUPPLIES {conf,reason,overlap,batch}]-> Concept 概念级供应链方向（K265-P1，source=concept_llm_v1 整体回滚键；src=上游卖方，品牌概念禁作 src）
REL_DRIVES = "DRIVES"  # Concept -[DRIVES {conf,reason,overlap,batch}]-> Concept 需求带动（K265-P1，source=concept_llm_v1；qwen 判向+buyer 自检+人审抽样，conf≥0.95 入图）
# ── K-SPATIAL-01 新边族 ───────────────────────────────────────
REL_EXECUTED_BY = "EXECUTED_BY"  # TradeAction -[EXECUTED_BY]-> Seat
REL_TARGETS = "TARGETS"  # TradeAction -[TARGETS]-> Security
REL_OCCUPIED_BY = (
    "OCCUPIED_BY"  # TradeAction -[OCCUPIED_BY]-> HotMoney（经 Seat→OCCUPIES 投影）
)
REL_LOCATED_AT = "LOCATED_AT"  # TradeAction -[LOCATED_AT]-> Location
REL_SEAT_LOCATED_AT = (
    "SEAT_LOCATED_AT"  # Seat -[SEAT_LOCATED_AT {validStart}]-> Location
)
REL_HAS_TRADED = "HAS_TRADED"  # TradeDate -[HAS_TRADED]-> TradeAction

# ── K264 跨项目共图收录（2026-10-08）────────────────────────
# 情报线边族（写入器在 C:\项目\zerobound-kg：kg_ann_history_link/kg_corroborate_edges/
# kg_annotation 等）——图内实核 ≈100.5 万条，本侧此前零注册（检查器/统计不可见）。
# 双版本为设计：EXP_ 前缀=实验/候选域，无前缀=转正域（kg_annotation.py 的
# rel[4:] 去前缀映射即两域桥）。保鲜检查按域分治：情报线 lag 由 KGStockAnalysis
# 侧 kg_check 负责，本侧 COVERAGE_EDGES 不纳入（节奏不同避免误报）。
# KGStockAnalysis 侧 schema 同样缺这 10 个（同病），其补注册挂对方项目线。
REL_INVOLVES = "INVOLVES"  # Security -[INVOLVES]-> Event 公告事件（自家缺口补注册！kg_build_p3_evt/kg_build_m2 写入，validStart=事件日，方向勿写反；COVERAGE_EDGES 消费方）
REL_MENTIONS = "MENTIONS"  # IntelItem -[MENTIONS]-> Security 情报提及标的（无前缀转正版，4 source）
REL_REPORTED_BY = (
    "REPORTED_BY"  # IntelItem -[REPORTED_BY]-> Source 情报溯源（无前缀版，2 source）
)
REL_SUPPORTS = (
    "SUPPORTS"  # IntelItem/Event -[SUPPORTS]-> Hypothesis 印证（Z2 印证表 d=True 分支）
)
REL_CONTRADICTS = (
    "CONTRADICTS"  # IntelItem/Event -[CONTRADICTS]-> Hypothesis 反证（d=False 分支）
)
REL_EXP_MENTIONS = "EXP_MENTIONS"  # EXP_IntelItem -[EXP_MENTIONS]-> EXP_Security 情报提及（实验域，46.9 万条，4 source）
REL_EXP_REPORTED_BY = "EXP_REPORTED_BY"  # EXP_IntelItem -[EXP_REPORTED_BY]-> EXP_Source 情报溯源（实验域，48.6 万条）
REL_EXP_SUPPORTS = (
    "EXP_SUPPORTS"  # EXP_IntelItem -[EXP_SUPPORTS]-> EXP_Hypothesis 印证候选（实验域）
)
REL_EXP_CONTRADICTS = "EXP_CONTRADICTS"  # EXP_IntelItem -[EXP_CONTRADICTS]-> EXP_Hypothesis 反证候选（实验域）
REL_EXP_ASSESSES = "EXP_ASSESSES"  # EXP_Assessment -[EXP_ASSESSES]-> EXP_Hypothesis 评估挂假设（实验域；注意与 REL_ASSESSED 派系评估不同语义）

# ── 时序默认值 ────────────────────────────────────────────────
VALID_END_OPEN = "9999-12-31"  # 在市/有效 → 开区间末端
SOURCE_SEED = "stock_basic+stock_company+sw_classify_l1"

# ── Cypher DDL：约束 + 索引 ───────────────────────────────────
CONSTRAINTS = [
    # 唯一性约束（自动建索引 + 唯一保证）
    "CREATE CONSTRAINT sec_ts_code IF NOT EXISTS "
    "FOR (n:Security) REQUIRE n.ts_code IS UNIQUE",
    "CREATE CONSTRAINT com_com_id IF NOT EXISTS "
    "FOR (n:Company) REQUIRE n.com_id IS UNIQUE",
    "CREATE CONSTRAINT ind_name IF NOT EXISTS "
    "FOR (n:Industry) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT swl1_idx IF NOT EXISTS "
    "FOR (n:SwL1Industry) REQUIRE n.index_code IS UNIQUE",
    "CREATE CONSTRAINT reg_name IF NOT EXISTS FOR (n:Region) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT person_name IF NOT EXISTS "
    "FOR (n:Person) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT mkt_idx_code IF NOT EXISTS "
    "FOR (n:MarketIndex) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT evt_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT prod_name IF NOT EXISTS "
    "FOR (n:Product) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT hm_name IF NOT EXISTS FOR (n:HotMoney) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT seat_name IF NOT EXISTS FOR (n:Seat) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT mevt_id IF NOT EXISTS "
    "FOR (n:MarketEvent) REQUIRE n.evt_id IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS "
    "FOR (n:Concept) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT org_name IF NOT EXISTS "
    "FOR (n:Organization) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT tech_name IF NOT EXISTS "
    "FOR (n:Technique) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT td_date IF NOT EXISTS "
    "FOR (n:TradeDate) REQUIRE n.date IS UNIQUE",
    # ── K-SPATIAL-01 ───────────────────────────────────────────
    "CREATE CONSTRAINT ta_id IF NOT EXISTS FOR (n:TradeAction) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT loc_key IF NOT EXISTS FOR (n:Location) REQUIRE n.key IS UNIQUE",
]

INDEXES = [
    # 二级索引（高频查询字段）
    "CREATE INDEX sec_name IF NOT EXISTS FOR (n:Security) ON (n.name)",
    "CREATE INDEX sec_market IF NOT EXISTS FOR (n:Security) ON (n.market)",
    "CREATE INDEX com_name IF NOT EXISTS FOR (n:Company) ON (n.com_name)",
    "CREATE INDEX sec_list_date IF NOT EXISTS FOR (n:Security) ON (n.list_date)",
]

# ── 节点属性映射（源列 → 节点属性） ───────────────────────────
# 完整保留 stock_company 的 18 列（business_scope/main_business 给 Phase 2 产业链抽取用）
SECURITY_PROPS = {  # stock_basic → Security
    "ts_code": "ts_code",
    "name": "name",
    "symbol": "symbol",
    "market": "market",
    "area": "area",
    "industry": "industry",
    "cnspell": "cnspell",
    "list_date": "list_date",
    "act_name": "act_name",
    "act_ent_type": "act_ent_type",
}
COMPANY_PROPS = {  # stock_company → Company
    "ts_code": "ts_code",  # 关联键（不唯一，多对一退市复用）
    "com_id": "com_id",
    "com_name": "com_name",
    "chairman": "chairman",
    "manager": "manager",
    "secretary": "secretary",
    "reg_capital": "reg_capital",
    "setup_date": "setup_date",
    "province": "province",
    "city": "city",
    "introduction": "introduction",
    "website": "website",
    "email": "email",
    "office": "office",
    "business_scope": "business_scope",
    "employees": "employees",
    "main_business": "main_business",
    "exchange": "exchange",
}

# ── 关系边属性 ────────────────────────────────────────────────
EDGE_PROPS = ["validStart", "validEnd", "source", "updated_at"]


def all_ddl():
    """返回完整 DDL 列表（约束 + 索引），供 kg_client.ensure_schema() 顺序执行。"""
    return CONSTRAINTS + INDEXES


if __name__ == "__main__":
    # 自检：打印 Schema
    print("=== 知识图谱 Phase 1 Schema ===")
    print(
        "节点标签: %s"
        % " / ".join([LBL_SECURITY, LBL_COMPANY, LBL_INDUSTRY, LBL_SW_L1, LBL_REGION])
    )
    print("关系类型: %s" % " / ".join([REL_OF_COMPANY, REL_INDUSTRY_OF, REL_AREA_OF]))
    print("时序铁律: 所有边带 validStart/validEnd（在市 validEnd=%s）" % VALID_END_OPEN)
    print("数据源: %s" % SOURCE_SEED)
    print("约束: %d 条 / 索引: %d 条" % (len(CONSTRAINTS), len(INDEXES)))
    print("--- DDL ---")
    for cy in all_ddl():
        print("  %s;" % cy)
