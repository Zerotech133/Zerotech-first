# -*- coding: utf-8 -*-
"""知识图谱 Phase 1 Schema：实体节点 + 时序关系边 + Cypher DDL。

依据《股票量化知识图谱设计文档》Phase 1 范围：
  · 实体：标的(Security) + 公司(Company) + 行业(Industry) + 申万一级(SwL1Industry) + 区域(Region)
  · 关系：OF_COMPANY / INDUSTRY_OF / AREA_OF（均带 validStart/validEnd 时序区间）
  · 数据源：stock_basic + stock_company + sw_classify_l1（已落库 parquet）
  · 不依赖未拉完的 cashflow/fina_indicator（Phase 2 才接质量因子）

铁律：所有关系边必须带 validStart/validEnd；当前在市 → validEnd=9999-12-31。
Phase 2 后续接入：人物/产品/事件/产业链/跨市场实体。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Neo4j 连接 ────────────────────────────────────────────────
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "kg_pwd_2026"      # 与 docker run -e NEO4J_AUTH 一致（11 位符合 5.x 要求）
NEO4J_DB = "neo4j"                  # community 版只有默认 db

# ── 节点标签 ──────────────────────────────────────────────────
LBL_SECURITY = "Security"           # 标的（A股/ETF/可转债；Phase 1 只建 A 股）
LBL_COMPANY = "Company"             # 上市公司实体
LBL_INDUSTRY = "Industry"           # 细分行业（来自 stock_basic.industry）
LBL_SW_L1 = "SwL1Industry"          # 申万一级行业（来自 sw_classify_l1）
LBL_REGION = "Region"               # 省份区域（来自 stock_basic.area）
LBL_PERSON = "Person"              # 人物（实控人/高管/董秘，Phase 2-A）
LBL_PRODUCT = "Product"            # 产品/业务（经营范围抽取，Phase 2-C）
LBL_MARKET_INDEX = "MarketIndex"   # 市场指数（A股宽基+全球，Phase 3-A）
LBL_HOTMONEY = "HotMoney"          # 游资/资金主体（CL-1，含散户集群如拉萨天团）
LBL_SEAT = "Seat"                  # 券商营业部席位（CL-1）
LBL_MARKET_EVENT = "MarketEvent"   # 盘面博弈事件：缠论结构/量能事件（CL-1）
LBL_CONCEPT = "Concept"            # 概念层（M4，人工词表，不依赖 Product 碎片层）

# ── 关系类型 ──────────────────────────────────────────────────
REL_OF_COMPANY = "OF_COMPANY"       # Security -[OF_COMPANY]-> Company
REL_INDUSTRY_OF = "INDUSTRY_OF"     # Security -[INDUSTRY_OF]-> Industry
REL_AREA_OF = "AREA_OF"             # Security -[AREA_OF]-> Region
REL_SERVES_AS = "SERVES_AS"         # Person -[SERVES_AS {role}]-> Company (Phase 2-A)
REL_SW_L1_OF = "SW_L1_OF"           # Security -[SW_L1_OF]-> SwL1Industry (Phase 2-B)
REL_PRODUCES = "PRODUCES"           # Company -[PRODUCES]-> Product (Phase 2-C)
REL_CORRELATES = "CORRELATES_WITH"  # MarketIndex -[CORRELATES_WITH {corr,beta}]-> MarketIndex (Phase 3-A)
REL_BENCHMARK_OF = "BENCHMARK_OF"   # MarketIndex -[BENCHMARK_OF]-> SwL1Industry/Security 基准映射 (Phase 3-A)
REL_OCCUPIES = "OCCUPIES"           # HotMoney -[OCCUPIES]-> Seat 游资占用席位 (CL-1)
REL_TRADED_ON = "TRADED_ON"         # Seat -[TRADED_ON {net_buy/buy/sell}]-> Security 龙虎榜净买额（元）(CL-1)
REL_HAS_STRUCTURE = "HAS_STRUCTURE" # Security -[HAS_STRUCTURE]-> MarketEvent 缠论结构事件 (CL-1)
REL_HAS_VOL_EVENT = "HAS_VOL_EVENT" # Security -[HAS_VOL_EVENT]-> MarketEvent 量能事件 (CL-1)
REL_DRIVE = "DRIVE"                 # Event -[DRIVE {p,lag_days}]-> MarketEvent 外部事件伴随盘面事件 (M2-W1，只算共现不判因果)
REL_BELONGS_TO = "BELONGS_TO"       # Concept -[BELONGS_TO]-> Concept 概念层级（子→父，M4）
REL_REQUIRES = "REQUIRES"           # Concept -[REQUIRES]-> Concept 依赖（人工边，M4）
REL_MAPS_TO = "MAPS_TO"             # Security -[MAPS_TO {via}]-> Concept 标的挂概念（M4，via=alias/industry）
REL_HAS_CANDLE = "HAS_CANDLE"       # Security -[HAS_CANDLE]-> MarketEvent 分时盘口事件（Batch 4，stk_mins 1 分钟线）
LBL_ORGANIZATION = "Organization"   # 机构/股东实体（B 类，top10_floatholders）
LBL_TECHNIQUE = "Technique"         # 手法节点（P3-b：candle type 对象化+监管映射）
LBL_TRADE_DATE = "TradeDate"        # 交易日一等节点（P3-d：date=yyyymmdd 唯一）
REL_HOLDS = "HOLDS"                 # Organization -[HOLDS {ratio,shares}]-> Security 十大流通股东（B 类，按报告期快照）
REL_HAS_LIMIT = "HAS_LIMIT"         # Security -[HAS_LIMIT]-> MarketEvent 涨跌停名单事件（B 类 limit_list_d）
REL_HAS_TOPGAP = "HAS_TOPGAP"       # Security -[HAS_TOPGAP]-> MarketEvent 榜后差额事件（P2-① ts_top_list）
REL_SIMILAR_K = "SIMILAR_K"         # Security -[SIMILAR_K {dist,corr}]-> Security K线形态相似（P2-② 60日DTW，无向语义字典序定向）
REL_USES_TECH = "USES_TECH"         # MarketEvent -[USES_TECH]-> Technique 手法对象化（P3-b，kind=candle 事件挂手法）
REL_ON_DATE = "ON_DATE"             # MarketEvent/Event -[ON_DATE]-> TradeDate 事件挂交易日（P3-d）
REL_NEXT_TD = "NEXT_TRADE_DAY"      # TradeDate -[NEXT_TRADE_DAY]-> TradeDate 交易日历链（P3-d）
LBL_EXP_COMMUNITY = "EXP_Community"  # 席位社区/游资集团（K190-P2，Louvain 快照，json 为真相源）
REL_IN_GROUP = "IN_GROUP"           # Seat -[IN_GROUP {role,source}]-> EXP_Community 集团成员（K190-P2）
REL_HAS_PATTERN = "HAS_PATTERN"     # Security -[HAS_PATTERN]-> MarketEvent(kind=pattern) 15 K线形态事件（K190-P1）
LBL_THEME = "Theme"                 # 产业主题（K201-C：人形机器人/AI算力，情报本体传导链）
LBL_SEGMENT = "Segment"             # 产业链环节（K201-C，两级：一级环节/二级子环节）
REL_CONDUCTION = "CONDUCTION"       # Theme -[CONDUCTION {type}]-> Segment 传导边（K201-C，type=载体/物理约束/供应链）
REL_SUB_OF = "SUB_OF"               # Segment -[SUB_OF]-> Segment 二级子环节归属（K201-C）
REL_SUPPLIES_TO = "SUPPLIES_TO"     # Company -[SUPPLIES_TO {evidence,chain}]-> Segment 供货关系（K201-C，证据溯源）
LBL_COMMENTATOR = "Commentator"     # 信息源/分析主体（K201-D：四派分析师/评论员，情报本体评估层）
REL_ASSESSED = "ASSESSED"           # Commentator -[ASSESSED {verdict,stars,theme,date}]-> Security 派系评估（K201-D）

# ── 时序默认值 ────────────────────────────────────────────────
VALID_END_OPEN = "9999-12-31"       # 在市/有效 → 开区间末端
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
    "CREATE CONSTRAINT reg_name IF NOT EXISTS "
    "FOR (n:Region) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT person_name IF NOT EXISTS "
    "FOR (n:Person) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT mkt_idx_code IF NOT EXISTS "
    "FOR (n:MarketIndex) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT evt_id IF NOT EXISTS "
    "FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT prod_name IF NOT EXISTS "
    "FOR (n:Product) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT hm_name IF NOT EXISTS "
    "FOR (n:HotMoney) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT seat_name IF NOT EXISTS "
    "FOR (n:Seat) REQUIRE n.name IS UNIQUE",
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
SECURITY_PROPS = {                  # stock_basic → Security
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
COMPANY_PROPS = {                   # stock_company → Company
    "ts_code": "ts_code",           # 关联键（不唯一，多对一退市复用）
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
    print("节点标签: %s" % " / ".join([
        LBL_SECURITY, LBL_COMPANY, LBL_INDUSTRY, LBL_SW_L1, LBL_REGION]))
    print("关系类型: %s" % " / ".join([
        REL_OF_COMPANY, REL_INDUSTRY_OF, REL_AREA_OF]))
    print("时序铁律: 所有边带 validStart/validEnd（在市 validEnd=%s）"
          % VALID_END_OPEN)
    print("数据源: %s" % SOURCE_SEED)
    print("约束: %d 条 / 索引: %d 条" % (len(CONSTRAINTS), len(INDEXES)))
    print("--- DDL ---")
    for cy in all_ddl():
        print("  %s;" % cy)
