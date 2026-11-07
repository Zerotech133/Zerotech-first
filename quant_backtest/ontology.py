# -*- coding: utf-8 -*-
"""L1 本体注册表（Ontology Registry，2026-10-09）。

Palantir OMS 思想的本地落地：图谱里有什么（名词）、从哪来（writer/source）、
多新鲜（fresh）、多可信（trust）——一份声明，处处消费：
  · kg_portal stats()/api("ontology")：计数与保鲜读注册表（全量 30 标签/41 边）
  · validate()：注册表 ↔ 图内实况双向对账（替代 graph_schema 事后 diff 的
    「先声明后入图」正向约束；K264 教训：DOC_EVENT 前身情报线 10 边族
    曾零注册，检查器全盲）
  · export()：reports/ontology_snapshot_YYYYMMDD.json 滚动快照（每日链
    graph_check 后一步，保留 8 份，同 graph_schema 惯例）
  · L3 kg-mcp 的 ontology_schema 工具将直接喂本表给 agent（类型系统自描述）

字段口径：
  domain    业务域（市场数据/公司人物/资金行为/市场事件/情报推理/情报本体/实验域）
  key       唯一约束属性（无约束=实体链接靠写入器自持，标注 None 并说明）
  date_field 观测时间属性（防前视锚点：freshness=max(date_field)；静态=None）
  source    数据源族名（对应 kg_source_rating.SOURCES 键）
  trust     Admiralty 档（A 完全可靠 ~ F 无法判断，档随证据走，依据见
            kg_source_rating.py；非 SOURCES 族以「族名+档」就地标注）
  writer    产出管线（图是谁写的——OMS 的 pipeline 血缘）

设计纪律：注册表只描述、不连接——快照/对账才碰 Neo4j（Neo4j 不可达时
export 降级 SKIP 仍退出 0，与每日链 warn-only 语义一致）。

用法：
  python ontology.py                # 打印注册表摘要
  python ontology.py --export       # 快照+对账落盘 reports/
  python ontology.py --validate     # 注册表↔图内实况对账（差异明细）
"""

import argparse
import glob
import json
import os
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VERSION = "1.0"            # 注册表内容版本（类型清单语义）
REGISTRY_TS = "2026-10-09"

# ── 受治理 TBox 元信息（C1，2026-11-06）──────────────────────
# 把"本体注册表"升级为"受治理 TBox"：每个节点/边类型声明 归属(steward)
# + 状态机 + 版本 + RFC。治理字段由 _apply_governance() 在加载时按域兜底
# 补全（显式声明优先），registry_json() 暴露、validate()/gate() 审计。
# 版本号遵循语义化（MAJOR.MINOR.PATCH）。
TBOX_VERSION = "2.1.0"          # 2.0 治理面；2.1 引入双时态面（C4，2026-11-06）
TBOX_TS = "2026-11-06"          # 治理元信息登记日
GOV_DEFAULT_RFC = "RFC-KG-000"  # 初始注册批次（后续结构/语义变更须升 RFC 号）

# 业务域 → 治理归属（steward 席位/团队）。对应 C5：情报域归 KGMAP1 席位。
DOMAIN_STEWARD = {
    "市场数据": "KGMARKET1",
    "公司人物": "KGMARKET1",
    "资金行为": "KGCAPITAL1",
    "市场事件": "KGEVENT1",
    "情报推理": "KGMAP1",        # 情报线转正域（C5 steward）
    "情报本体": "KGMAP1",        # 人工情报录入（K201）
    "实验域":   "KGSHADOW1",     # EXP_ 影子推理（zerobound-kg 团队）
}

# 边类型分类 -> 治理归属（边的 domain 是"边分类"，与节点业务域不同轴）
EDGE_STEWARD = {
    "结构": "KGMARKET1",
    "资金": "KGCAPITAL1",
    "事件": "KGEVENT1",
    "时间": "KGEVENT1",
    "相似": "KGMARKET1",
    "分类": "KGMARKET1",
    "情报": "KGMAP1",
    "推理": "KGMAP1",
    "情报本体": "KGMAP1",
    "实验域": "KGSHADOW1",
    "文档线": "KGMAP1",
}
# 类型状态机：active=生产可用 / experimental=实验候选 / deprecated=已弃用
STATUS_VALUES = ("active", "experimental", "deprecated")

GOVERNANCE = {
    "policy": "每个节点/边类型须声明 owner(归属席位)+status+version+rfc；"
              "active/deprecated 缺 owner/rfc 判治理缺口；deprecated 须 replaced_by。",
    "versioning": "语义化版本；破坏性 schema 变更须 MAJOR+；新增类型 MINOR+。",
    "rfc": "每次结构/语义变更登记 RFC-KG-###；初始注册批次 RFC-KG-000。",
    "steward_duty": "steward 对所属类型的语义、保鲜、漂移负责，接收 gate 告警。",
    # C5（2026-11-06）：消除名实脱节——TBox 结构本身（增删类型/改域归属/
    # 状态机/版本/RFC 登记）由 KGMAP1 席位任 steward：TBox 级变更须提
    # RFC-KG-### 并由 KGMAP1 审批归档；gate 的 TBox 级告警（治理缺口/
    # 双时态索引缺失）直达 KGMAP1，域级告警按 DOMAIN_STEWARD/EDGE_STEWARD 分发。
    "tbox_steward": "KGMAP1",
}

# TBox 结构 steward 席位（与 GOVERNANCE["tbox_steward"] 同源单点）
TBOX_STEWARD = "KGMAP1"

TRUST_SCALE = {
    "A": "完全可靠（人工审核/官方日历）",
    "B": "通常可靠（官方接口/确定性引擎，有小口径瑕疵实证）",
    "C": "相当可靠（LLM 本地受控/共现统计，可闭环修复）",
    "D": "不常可靠（远端 feed，无 SLA，断流教训）",
    "F": "无法判断",
}

# ── 节点类型注册表（30 标签，2026-10-09 图内实况口径）─────────
# 数量不写死（快照时现查）；desc 保持一句话可讲清。
NODE_TYPES = {
    # 市场数据域
    "Security": dict(
        domain="市场数据",
        key="ts_code",
        date_field=None,
        source="xtcloud_local+tushare_pro",
        trust="B",
        writer="kg_build*.py（tdx∪tushare 双源 merge，退市覆盖+4字段补齐）",
        desc="证券主实体（A股/ETF），ts_code 全站主键锚",
    ),
    "Company": dict(
        domain="公司人物",
        key="com_id",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_build.py（stock_company 18 列全保）",
        desc="上市公司主体（ts_code 多对一，退市复用 com_id）",
    ),
    "Industry": dict(
        domain="市场数据",
        key="name",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_build.py",
        desc="细分行业（stock_basic.industry）",
    ),
    "SwL1Industry": dict(
        domain="市场数据",
        key="index_code",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_build.py",
        desc="申万一级行业（sw_classify_l1）",
    ),
    "Region": dict(
        domain="市场数据",
        key="name",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_build.py",
        desc="省份区域（stock_basic.area）",
    ),
    "MarketIndex": dict(
        domain="市场数据",
        key="code",
        date_field=None,
        source="tushare_pro+jev_feed",
        trust="B",
        writer="pull_global_idx.py + kg_build_p3.py",
        desc="市场指数（A股宽基+全球）",
    ),
    "Concept": dict(
        domain="市场数据",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="人工词表（concept_dict/m4_alias，M4）",
        desc="概念板块（人工维护，不依赖 Product 碎片层）",
    ),
    "Technique": dict(
        domain="市场数据",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="kg_candle.py 内置定义（K174 15 形态引擎扩容）",
        desc="技术形态/手法对象（含监管映射 reg_tag）",
    ),
    "TradeDate": dict(
        domain="市场数据",
        key="date",
        date_field="date",
        source="human_curation",
        trust="A",
        writer="kg_tradedate.py",
        desc="交易日一等节点（date=yyyymmdd 唯一，NEXT_TRADE_DAY 链）",
    ),
    # 公司人物域
    "Person": dict(
        domain="公司人物",
        key="name",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_build.py（stk_managers 高管快照）",
        desc="高管/自然人股东（重名共节点为已知口径）",
    ),
    "Product": dict(
        domain="公司人物",
        key="name",
        date_field=None,
        source="rule_keyword",
        trust="B",
        writer="kg_build.py（business_scope 抽取，Phase 2-C）",
        desc="产品/业务（经营范围关键词抽取）",
    ),
    # 资金行为域
    "Organization": dict(
        domain="资金行为",
        key="name",
        date_field=None,
        source="tushare_pro",
        trust="B",
        writer="kg_bclass.py（top10_floatholders 自然人过滤后）",
        desc="机构/股东实体（十大流通股东，按报告期快照）",
    ),
    "HotMoney": dict(
        domain="资金行为",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="youzi_registry 人工词表",
        desc="游资/资金主体（含散户集群如拉萨天团）",
    ),
    "Seat": dict(
        domain="资金行为",
        key="name",
        date_field=None,
        source="xtcloud_local",
        trust="B",
        writer="kg_build_cl1.py（ts_top_inst，特殊通道行子串排除 G4b）",
        desc="券商营业部席位",
    ),
    # 市场事件域
    "MarketEvent": dict(
        domain="市场事件",
        key="evt_id",
        date_field="date",
        source="多引擎（按 kind）",
        trust="B",
        writer="kg_build_cl1/kg_candle/kg_bclass/kg_dt_gap/"
        "K190-P1 pattern（evt_id=code:kind:date）",
        desc="盘面博弈事件：candle/limit/topgap/volume/chanlun/pattern",
    ),
    # 情报推理域（转正域，写入器在 C:\\项目\\zerobound-kg）
    "Event": dict(
        domain="情报推理",
        key="id",
        date_field="date",
        source="rule_keyword+llm_local",
        trust="B",
        writer="event_graph.py（RULES 14 组词表+qwen 互检）",
        desc="公告/新闻事件（bias 判偏，semantica_events）",
    ),
    "IntelItem": dict(
        domain="情报推理",
        key="item_id",
        date_field="published",
        source="jev_feed+llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线（转正域）",
        desc="情报条目（事实层，LLM 抽取后人审转正）",
    ),
    "Source": dict(
        domain="情报推理",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="C:\\项目\\zerobound-kg 情报线（转正域）",
        desc="情报来源（事实层）",
    ),
    "Hypothesis": dict(
        domain="情报推理",
        key="hid",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 假设线（转正域）",
        desc="假设（事实层，SUPPORTS/CONTRADICTS 论证终点）",
    ),
    # 情报本体域（K201 人工情报录入）
    "Theme": dict(
        domain="情报本体",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-C 人工录入（产业主题传导链）",
        desc="产业主题（人形机器人/AI 算力等，传导链起点）",
    ),
    "Segment": dict(
        domain="情报本体",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-C 人工录入（两级环节）",
        desc="产业链环节（一级/二级，SUB_OF 归属）",
    ),
    "Commentator": dict(
        domain="情报本体",
        key="name",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-D 人工录入（四派分析师）",
        desc="信息源/分析主体（ASSESSED 派系评估起点）",
    ),
    # 实验域（EXP_ 前缀=候选/影子，转正走去前缀双域桥）
    "EXP_IntelItem": dict(
        domain="实验域",
        key="item_id",
        date_field="published",
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg\\kg_ann_history_link.py",
        desc="LLM 抽取情报条目（影子层，最大节点）",
    ),
    "EXP_Security": dict(
        domain="实验域",
        key="ts_code",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="证券镜像（推理层，ts_code 同锚）",
    ),
    "EXP_Event": dict(
        domain="实验域",
        key="id",
        date_field="date",
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="事件镜像（推理层）",
    ),
    "EXP_Hypothesis": dict(
        domain="实验域",
        key="hid",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 线",
        desc="假设（推理层）",
    ),
    "EXP_Assessment": dict(
        domain="实验域",
        key="aid",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 线",
        desc="评估（推理层，EXP_ASSESSES 挂假设）",
    ),
    "EXP_Source": dict(
        domain="实验域",
        key="name",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="情报来源（推理层）",
    ),
    "EXP_Community": dict(
        domain="实验域",
        key="cid",
        date_field=None,
        source="cooccur_stat",
        trust="C",
        writer="kg_communities.py（Louvain 快照，json 为真相源）",
        desc="席位社区/游资集团（K190-P2）",
    ),
    "EXP_AgentMemo": dict(
        domain="实验域",
        key="mid",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="agent 会话备忘（画布问答上下文）",
        desc="Agent 备忘（实验）",
    ),
}

# ── 边类型注册表（41 边型）────────────────────────────────────
# temporal=True → 带 validStart/validEnd（铁律：在市 validEnd=9999-12-31）；
# date_field → freshness 观测锚（多数边用 validStart；静态定义边 None）。
_EDGE = dict(
    domain="",
    desc="",
    frm="",
    to="",
    temporal=False,
    date_field=None,
    source="",
    trust="B",
    writer="",
)


def _e(**kw):
    d = dict(_EDGE)
    d.update(kw)
    return d


EDGE_TYPES = {
    # ── 结构/分类（Phase 1 族，时序铁律）──
    "OF_COMPANY": _e(
        domain="结构",
        frm="Security",
        to="Company",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build.py",
        desc="标的→上市公司",
    ),
    "INDUSTRY_OF": _e(
        domain="结构",
        frm="Security",
        to="Industry",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build.py",
        desc="行业归属",
    ),
    "AREA_OF": _e(
        domain="结构",
        frm="Security",
        to="Region",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build.py",
        desc="地域归属",
    ),
    "SW_L1_OF": _e(
        domain="结构",
        frm="Security",
        to="SwL1Industry",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build.py",
        desc="申万一级成分",
    ),
    "SERVES_AS": _e(
        domain="结构",
        frm="Person",
        to="Company",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build.py",
        desc="高管任职（最大边；G5 实证 47 条时序倒挂为已知）",
    ),
    "PRODUCES": _e(
        domain="结构",
        frm="Company",
        to="Product",
        temporal=True,
        date_field="validStart",
        source="rule_keyword",
        writer="kg_build.py（Phase 2-C）",
        desc="公司产出产品",
    ),
    # ── 资金行为（CL-1/B 类）──
    "OCCUPIES": _e(
        domain="资金",
        frm="HotMoney",
        to="Seat",
        temporal=False,
        source="human_curation",
        trust="A",
        writer="kg_build_cl1.py",
        desc="游资占用席位",
    ),
    "TRADED_ON": _e(
        domain="资金",
        frm="Seat",
        to="Security",
        temporal=True,
        date_field="validStart",
        source="xtcloud_local",
        writer="kg_build_cl1.py（龙虎榜净买额，winrate 回填 kg_seat_winrate.py）",
        desc="席位交易标的（net_buy 按日）",
    ),
    "HOLDS": _e(
        domain="资金",
        frm="Organization",
        to="Security",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_bclass.py",
        desc="十大流通股东持股（按报告期快照）",
    ),
    # ── 市场事件族 ──
    "HAS_STRUCTURE": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="chanlun_engine",
        trust="B",
        writer="kg_build_cl1.py（kind=chanlun）",
        desc="缠论结构事件（confidence/hm_tag 标注）",
    ),
    "HAS_VOL_EVENT": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="xtcloud_local",
        writer="kg_build_cl1.py（kind=volume）",
        desc="量异动事件",
    ),
    "HAS_CANDLE": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="candle_local",
        writer="kg_candle.py（kind=candle，stk_mins 分钟线）",
        desc="分时盘口事件",
    ),
    "HAS_LIMIT": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="tushare_pro",
        writer="kg_bclass.py（kind=limit，连板/封单/炸板）",
        desc="涨跌停名单事件",
    ),
    "HAS_TOPGAP": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="xtcloud_local",
        writer="kg_dt_gap.py（kind=topgap 榜后差额）",
        desc="榜后差额事件（gap/席位性质/次日穿透）",
    ),
    "HAS_PATTERN": _e(
        domain="事件",
        frm="Security",
        to="MarketEvent",
        date_field=None,
        source="chanlun_engine",
        writer="K190-P1（kind=pattern 15 K 线形态）",
        desc="K 线形态事件（USES_TECH 关联手法）",
    ),
    "USES_TECH": _e(
        domain="事件",
        frm="MarketEvent",
        to="Technique",
        date_field=None,
        source="candle_local",
        writer="kg_candle.py（手法对象化 P3-b）",
        desc="事件用手法（含监管映射）",
    ),
    "ON_DATE": _e(
        domain="时间",
        frm="MarketEvent/Event",
        to="TradeDate",
        date_field=None,
        source="多引擎",
        writer="kg_tradedate.py（P3-d）",
        desc="事件挂交易日（双时间线：发生时点）",
    ),
    "NEXT_TRADE_DAY": _e(
        domain="时间",
        frm="TradeDate",
        to="TradeDate",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="kg_tradedate.py",
        desc="交易日历链（时序遍历免重算）",
    ),
    "SIMILAR_K": _e(
        domain="相似",
        frm="Security",
        to="Security",
        temporal=True,
        date_field="validStart",
        source="cooccur_stat",
        trust="C",
        writer="kg_sim_k.py（60 日 DTW，无向语义字典序定向）",
        desc="K 线形态相似（dist/corr）",
    ),
    "CORRELATES_WITH": _e(
        domain="相似",
        frm="MarketIndex",
        to="MarketIndex",
        temporal=True,
        date_field="validStart",
        source="cooccur_stat",
        trust="C",
        writer="kg_build_p3.py（corr/beta）",
        desc="指数相关性",
    ),
    "BENCHMARK_OF": _e(
        domain="相似",
        frm="MarketIndex",
        to="SwL1Industry/Security",
        temporal=True,
        date_field="validStart",
        source="tushare_pro",
        writer="kg_build_p3.py（Phase 3-A，尚未实建——"
        "注册声明先于建图，validate 以 INFO 报空）",
        desc="指数基准映射",
    ),
    "DRIVE": _e(
        domain="事件",
        frm="Event",
        to="MarketEvent",
        temporal=True,
        date_field="validStart",
        source="cooccur_stat",
        trust="C",
        writer="kg_build_m2.py（M2 共现，只算共现不判因果）",
        desc="外部事件伴随盘面事件（p/lag_days）",
    ),
    # ── 概念层（M4）──
    "MAPS_TO": _e(
        domain="分类",
        frm="Security",
        to="Concept",
        temporal=True,
        date_field="validStart",
        source="human_curation",
        trust="A",
        writer="M4（via=alias/industry）",
        desc="标的挂概念",
    ),
    "BELONGS_TO": _e(
        domain="分类",
        frm="Concept",
        to="Concept",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="M4",
        desc="概念层级（子→父）",
    ),
    "REQUIRES": _e(
        domain="分类",
        frm="Concept",
        to="Concept",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="M4（人工边）",
        desc="概念依赖",
    ),
    # ── 情报推理（转正域）──
    "INVOLVES": _e(
        domain="情报",
        frm="Event",
        to="Security",
        temporal=True,
        date_field="validStart",
        source="rule_keyword",
        writer="kg_build_p3_evt.py",
        desc="公告事件涉入标的（validStart=事件日）",
    ),
    "MENTIONS": _e(
        domain="情报",
        frm="IntelItem",
        to="Security",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg\\kg_mentions_rebuild.py",
        desc="情报提及标的（事实层）",
    ),
    "REPORTED_BY": _e(
        domain="情报",
        frm="IntelItem",
        to="Source",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="情报溯源（事实层）",
    ),
    "SUPPORTS": _e(
        domain="推理",
        frm="IntelItem/Event",
        to="Hypothesis",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 印证表 d=True",
        desc="印证（事实层）",
    ),
    "CONTRADICTS": _e(
        domain="推理",
        frm="IntelItem/Event",
        to="Hypothesis",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 印证表 d=False",
        desc="反证（事实层）",
    ),
    # ── 情报本体（K201-C/D + K265）──
    "CONDUCTION": _e(
        domain="情报本体",
        frm="Theme",
        to="Segment",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-C（type=载体/物理约束/供应链）",
        desc="主题→环节传导",
    ),
    "SUB_OF": _e(
        domain="情报本体",
        frm="Segment",
        to="Segment",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-C",
        desc="二级环节归属",
    ),
    "SUPPLIES_TO": _e(
        domain="情报本体",
        frm="Company",
        to="Segment",
        date_field=None,
        source="human_curation",
        trust="A",
        writer="K201-C（evidence 证据溯源）",
        desc="公司供货环节",
    ),
    "ASSESSED": _e(
        domain="情报本体",
        frm="Commentator",
        to="Security",
        date_field="date",
        source="human_curation",
        trust="A",
        writer="K201-D（verdict/stars 派系评估）",
        desc="派系评估",
    ),
    "SUPPLIES": _e(
        domain="情报本体",
        frm="Concept",
        to="Concept",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="K265-P1（concept_llm_v1，qwen 判向+自检+人审，conf≥0.95 入图）",
        desc="概念级供应链方向",
    ),
    "DRIVES": _e(
        domain="情报本体",
        frm="Concept",
        to="Concept",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="K265-P1（同上，品牌概念禁作 src）",
        desc="概念级需求带动",
    ),
    # ── 实验域（EXP_ 影子推理）──
    "EXP_MENTIONS": _e(
        domain="实验域",
        frm="EXP_IntelItem",
        to="EXP_Security",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="提及（影子）",
    ),
    "EXP_REPORTED_BY": _e(
        domain="实验域",
        frm="EXP_IntelItem",
        to="EXP_Source",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg 情报线",
        desc="溯源（影子）",
    ),
    "EXP_SUPPORTS": _e(
        domain="实验域",
        frm="EXP_IntelItem",
        to="EXP_Hypothesis",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 线",
        desc="印证（影子）",
    ),
    "EXP_CONTRADICTS": _e(
        domain="实验域",
        frm="EXP_IntelItem",
        to="EXP_Hypothesis",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 线",
        desc="反证（影子）",
    ),
    "EXP_ASSESSES": _e(
        domain="实验域",
        frm="EXP_Assessment",
        to="EXP_Hypothesis",
        date_field=None,
        source="llm_local",
        trust="C",
        writer="C:\\项目\\zerobound-kg Z2 线",
        desc="评估挂假设（影子；≠ASSESSED 派系评估）",
    ),
    "IN_GROUP": _e(
        domain="实验域",
        frm="Seat",
        to="EXP_Community",
        date_field=None,
        source="cooccur_stat",
        trust="C",
        writer="kg_communities.py（K190-P2，role/source）",
        desc="席位入集团",
    ),
    # ── 文档线（K266，5 分钟节奏）──
    "DOC_EVENT": _e(
        domain="文档线",
        frm="Security",
        to="Security",
        date_field="ts_last",
        source="llm_local+human_curation",
        trust="B",
        writer="k266_m3a_events.py（人审放行，rel/dir/n/date/amount/src_doc）",
        desc="文档抽取事件边（公告/研报跨标的关系）",
    ),
}

# ── 属性级元数据（防前视锚点：observed_at 声明「数据代表的时点」）──
# 全量属性映射复用 kg_schema.SECURITY_PROPS/COMPANY_PROPS（唯一权威源），
# 此处只声明分析关键属性；observed_at=None → 属性为静态名录属性。
PROP_META = {
    "Security": {
        "ts_code": {
            "type": "str",
            "source": "tdx∪tushare",
            "observed_at": None,
            "note": "主键锚（三写法归一）",
        },
        "name": {
            "type": "str",
            "source": "stock_basic",
            "observed_at": None,
            "note": "现用名（历史名不回溯）",
        },
        "industry": {
            "type": "str",
            "source": "stock_basic",
            "observed_at": None,
            "note": "静态口径，变更不追踪",
        },
        "list_date": {
            "type": "str",
            "source": "stock_basic",
            "observed_at": None,
            "note": "上市日",
        },
    },
    "MarketEvent": {
        "evt_id": {
            "type": "str",
            "source": "引擎生成",
            "observed_at": None,
            "note": "code:kind:date 复合键",
        },
        "kind": {
            "type": "str",
            "source": "引擎枚举",
            "observed_at": None,
            "note": "candle/limit/topgap/volume/chanlun/pattern",
        },
        "date": {
            "type": "str",
            "source": "行情/榜单日",
            "observed_at": "date 本身",
            "note": "事件观测时点（防前视下界）",
        },
        "pct": {
            "type": "float",
            "source": "分钟线/日线",
            "observed_at": "date",
            "note": "涨跌幅",
        },
    },
    "Company": {},  # 18 列静态（COMPANY_PROPS 全集，季频源）
    "_edge_common": {  # 所有时序边共有（kg_schema.EDGE_PROPS）
        "validStart": {
            "type": "str",
            "observed_at": "关系生效日",
            "note": "防前视：早于此日用该关系=穿越",
        },
        "validEnd": {
            "type": "str",
            "observed_at": None,
            "note": "9999-12-31=在市开区间",
        },
        "source": {"type": "str", "observed_at": None, "note": "写入来源标签（血缘）"},
        "updated_at": {
            "type": "str",
            "observed_at": None,
            "note": "写入时间（非数据时点！）",
        },
    },
}

# ── 受治理 TBox：加载时治理字段补全（C1）─────────────────────
def _default_status(key):
    """实验域 EXP_ 前缀 → experimental，其余 active。"""
    return "experimental" if str(key).startswith("EXP_") else "active"


def _apply_governance():
    """为每个节点/边类型补全治理字段（owner/status/version/rfc/replaced_by）。

    规则：条目若已显式声明治理字段则不覆盖，否则按域 steward 兜底。
    不修改 NODE_TYPES/EDGE_TYPES 字面量，保证向后兼容所有消费方。
    """
    for key, m in NODE_TYPES.items():
        m.setdefault("owner", DOMAIN_STEWARD.get(m.get("domain", ""), "KGADMIN1"))
        m.setdefault("status", _default_status(key))
        m.setdefault("version", TBOX_VERSION)
        m.setdefault("rfc", GOV_DEFAULT_RFC)
        m.setdefault("replaced_by", None)
    for key, m in EDGE_TYPES.items():
        m.setdefault("owner", EDGE_STEWARD.get(m.get("domain", ""), "KGADMIN1"))
        m.setdefault("status", _default_status(key))
        m.setdefault("version", TBOX_VERSION)
        m.setdefault("rfc", GOV_DEFAULT_RFC)
        m.setdefault("replaced_by", None)


def _governance_gap():
    """治理缺口：未显式细化 steward（落到 KGADMIN1 兜底）/ 非法状态 /
    缺 replaced_by 的条目。供 snapshot/validate/gate 复用。"""
    missing_owner, missing_rfc, bad_status, missing_replaced = [], [], [], []
    for key, m in NODE_TYPES.items():
        if not m.get("owner") or str(m["owner"]).startswith("KGADMIN1"):
            missing_owner.append(key)
        if not m.get("rfc"):
            missing_rfc.append(key)
        if m.get("status") not in STATUS_VALUES:
            bad_status.append(key)
        if m.get("status") == "deprecated" and not m.get("replaced_by"):
            missing_replaced.append(key)
    for key, m in EDGE_TYPES.items():
        if not m.get("owner") or str(m["owner"]).startswith("KGADMIN1"):
            missing_owner.append(key)
        if not m.get("rfc"):
            missing_rfc.append(key)
        if m.get("status") not in STATUS_VALUES:
            bad_status.append(key)
        if m.get("status") == "deprecated" and not m.get("replaced_by"):
            missing_replaced.append(key)
    return {
        "missing_owner": sorted(set(missing_owner)),
        "missing_rfc": sorted(set(missing_rfc)),
        "bad_status": sorted(set(bad_status)),
        "missing_replaced_by": sorted(set(missing_replaced)),
        "count": len(set(missing_owner)) + len(set(missing_rfc))
        + len(set(bad_status)) + len(set(missing_replaced)),
    }


_apply_governance()


# ── C4 双时态面（2026-11-06）─────────────────────────────────
# 目标：漂移可追责到"哪次写入"（事务时间 tx_created）+ 事实随时间演化
# 可回溯（有效时间窗 valid_from/valid_to，开区间无界=缺省空）。
# 写入约定（对所有建图脚本生效）：
#   - 一切节点/边写入须带 tx_created（datetime）；未带 = 迁移前存量，回填基准。
#   - 时变边（BITEMPORAL_EDGE_DOMAINS 覆盖的类型）另带 valid_from/valid_to。
TX_FIELD = "tx_created"     # 事务时间字段（追责维度）
VALID_FROM = "valid_from"   # 有效时间起点
VALID_TO = "valid_to"       # 有效时间终点（None/缺省 = 至今有效）

# 需要有效时间窗的边域：资金流/事件状态/时间序列随时间变化，须可回溯；
# 结构类（BELONGS_TO 等名义稳定）只记 tx_created，不加窗。
BITEMPORAL_EDGE_DOMAINS = ("资金", "事件", "时间")


def bitemporal_edge_types():
    """需要 (valid_from, valid_to) 复合索引的边类型清单（按边域筛）。"""
    return [
        r for r, m in EDGE_TYPES.items()
        if m.get("domain") in BITEMPORAL_EDGE_DOMAINS
    ]


def bitemporal_ddl():
    """生成双时态索引 DDL（Neo4j 4.4+/5.x 语法，属性带变量前缀——5.x 必需；
    幂等 IF NOT EXISTS）。

    - 全部节点标签：tx_created 单列索引（写入追责主查询）。
    - 时变边类型：(valid_from, valid_to) 复合索引（时点回溯主查询）
      + tx_created 单列索引（写入追责）。
    索引命名：tx_<label 小写> / vf_<rel 小写> / txr_<rel 小写>。
    """
    stmts = []
    for lbl in NODE_TYPES:
        stmts.append(
            "CREATE INDEX tx_%s IF NOT EXISTS FOR (n:`%s`) ON (n.`%s`);"
            % (lbl.lower(), lbl, TX_FIELD)
        )
    for rel in bitemporal_edge_types():
        stmts.append(
            "CREATE INDEX vf_%s IF NOT EXISTS FOR ()-[r:`%s`]-()"
            " ON (r.`%s`, r.`%s`);"
            % (rel.lower(), rel, VALID_FROM, VALID_TO)
        )
        stmts.append(
            "CREATE INDEX txr_%s IF NOT EXISTS FOR ()-[r:`%s`]-() ON (r.`%s`);"
            % (rel.lower(), rel, TX_FIELD)
        )
    return stmts


def bitemporal_backfill_cypher():
    """存量回填 Cypher（幂等，只补缺失字段，不覆盖已有值）。

    - 节点：tx_created 缺失 -> 回填 migration 基准（date()，日粒度足够追责）。
    - 时变边：valid_from 缺失 -> 用自身 date_field（若该类型声明了），
      否则 date()；valid_to 缺省不写（= 至今有效，开区间语义）。
    返回 [(描述, cypher), ...]；执行方逐条 run，失败不中断（逐条留痕）。
    """
    stmts = []
    for lbl, m in NODE_TYPES.items():
        stmts.append((
            "node %s: backfill tx_created" % lbl,
            "MATCH (n:`%s`) WHERE n.%s IS NULL SET n.%s = date();"
            % (lbl, TX_FIELD, TX_FIELD),
        ))
    for rel in bitemporal_edge_types():
        df = (EDGE_TYPES.get(rel) or {}).get("date_field")
        fill = ("coalesce(r.`%s`, date())" % df) if df else "date()"
        stmts.append((
            "rel %s: backfill valid_from%s" % (rel, " (from %s)" % df if df else ""),
            "MATCH ()-[r:`%s`]->() WHERE r.%s IS NULL SET r.%s = %s;"
            % (rel, VALID_FROM, VALID_FROM, fill),
        ))
    return stmts


def _expected_indexes():
    """bitemporal_ddl() 期望的索引集合，规格化为
    (entityType, labelsOrTypes 元组, properties 元组)，与 SHOW INDEXES
    输出同构，供 gate() 比对图内实况。"""
    exp = set()
    for lbl in NODE_TYPES:
        exp.add(("NODE", (lbl,), (TX_FIELD,)))
    for rel in bitemporal_edge_types():
        exp.add(("RELATIONSHIP", (rel,), (VALID_FROM, VALID_TO)))
        exp.add(("RELATIONSHIP", (rel,), (TX_FIELD,)))
    return exp


# ── 基础访问面 ────────────────────────────────────────────────
def labels():
    """注册表标签清单（排序稳定，供 stats 等消费方遍历）。"""
    return sorted(NODE_TYPES)


def rels():
    """注册表边型清单。"""
    return sorted(EDGE_TYPES)


def trust_grade(family):
    """源族→Admiralty 档：优先 kg_source_rating 实证档（单一权威），
    注册表就地标注为兜底（族改名/新增时对账会报 drift）。"""
    try:
        import kg_source_rating as g

        if family in g.SOURCES:
            return g.SOURCES[family]["grade"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/ontology.py", exc)
    for fam in str(family).split("+"):
        try:
            import kg_source_rating as g

            if fam in g.SOURCES:
                return g.SOURCES[fam]["grade"]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/ontology.py", exc)
    return None


def registry_json():
    """纯注册表视图（不连图）：类型+属性+元数据，L3 agent 自描述用。"""
    nodes = {}
    for lbl, m in NODE_TYPES.items():
        g = trust_grade(m["source"]) or m["trust"]
        nodes[lbl] = {
            **m,
            "trust": g,
            "props": PROP_META.get(lbl, {}),
            "governance": {
                "owner": m.get("owner"),
                "status": m.get("status"),
                "version": m.get("version"),
                "rfc": m.get("rfc"),
                "replaced_by": m.get("replaced_by"),
            },
        }
    edges = {}
    for r, m in EDGE_TYPES.items():
        g = trust_grade(m["source"]) or m["trust"]
        edges[r] = {
            **m,
            "trust": g,
            "governance": {
                "owner": m.get("owner"),
                "status": m.get("status"),
                "version": m.get("version"),
                "rfc": m.get("rfc"),
                "replaced_by": m.get("replaced_by"),
            },
        }
    return {
        "version": VERSION,
        "tbox_version": TBOX_VERSION,
        "tbox_ts": TBOX_TS,
        "governance_policy": GOVERNANCE,
        "domain_steward": DOMAIN_STEWARD,
        "tbox_steward": TBOX_STEWARD,
        "registry_ts": REGISTRY_TS,
        "trust_scale": TRUST_SCALE,
        "bitemporal": {
            "tx_field": TX_FIELD,
            "valid_from": VALID_FROM,
            "valid_to": VALID_TO,
            "edge_domains": list(BITEMPORAL_EDGE_DOMAINS),
            "edge_types": bitemporal_edge_types(),
            "ddl_statement_count": len(bitemporal_ddl()),
        },
        "counts": {"node_types": len(nodes), "edge_types": len(edges)},
        "node_types": nodes,
        "edge_types": edges,
        "edge_common_props": PROP_META["_edge_common"],
    }


# ── 快照 / 对账（碰图区）─────────────────────────────────────
def _connect():
    from kg_client import KgClient

    return KgClient(retry=1, retry_wait=1)


def snapshot(cli=None):
    """注册表 × 图内实况：全量计数 + 按声明观测锚的保鲜 + 元数据。

    2 条全图扫描（label/reltype 计数）+ 每个声明了 date_field 的类型
    1 条 max 查询；Neo4j 不可达时返回 ok=False（不抛，warn-only 语义）。
    """
    out = registry_json()
    out["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out["ok"] = False
    try:
        cli = cli or _connect()
        with cli._session() as s:
            n_cnt = {
                r["lbl"]: r["c"]
                for r in s.run(
                    "MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c"
                ).data()
                if r.get("lbl")
            }
            e_cnt = {
                r["t"]: r["c"]
                for r in s.run(
                    "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c"
                ).data()
            }
            fresh = {}
            for lbl, m in NODE_TYPES.items():
                if m.get("date_field"):
                    q = "MATCH (n:`%s`) RETURN max(n.%s) AS mx" % (lbl, m["date_field"])
                    fresh[lbl] = s.run(q).data()[0].get("mx")
            for r, m in EDGE_TYPES.items():
                if m.get("date_field"):
                    q = "MATCH ()-[x:`%s`]->() RETURN max(x.%s) AS mx" % (
                        r,
                        m["date_field"],
                    )
                    fresh["rel:" + r] = s.run(q).data()[0].get("mx")
            # C4：图内索引实况（gate R6 比对双时态期望集）。
            # 旧版 Neo4j 无 SHOW INDEXES 某些字段时降级为空表（不致快照失败）。
            try:
                out["indexes"] = s.run(
                    "SHOW INDEXES YIELD name, type, entityType, "
                    "labelsOrTypes, properties, state "
                    "WHERE type <> 'LOOKUP' "
                    "RETURN entityType, labelsOrTypes, properties, state, name"
                ).data()
            except Exception:
                out["indexes"] = []
    except Exception as ex:
        out["error"] = "Neo4j 不可达：%s" % str(ex)[:80]
        return out
    # 计数与保鲜并回注册表视图（注册的排前面，未注册的归 drift 桶）
    for lbl in out["node_types"]:
        out["node_types"][lbl]["count"] = n_cnt.get(lbl, 0)
    for r in out["edge_types"]:
        out["edge_types"][r]["count"] = e_cnt.get(r, 0)
    out["freshness"] = fresh
    out["graph_counts"] = {
        "labels": len(n_cnt),
        "rel_types": len(e_cnt),
        "nodes_total": sum(n_cnt.values()),
        "edges_total": sum(e_cnt.values()),
    }
    out["drift"] = {
        "unregistered_labels": sorted(set(n_cnt) - set(NODE_TYPES)),
        "unregistered_rels": sorted(set(e_cnt) - set(EDGE_TYPES)),
        "registered_absent_labels": sorted(l for l in NODE_TYPES if l not in n_cnt),
        "registered_absent_rels": sorted(r for r in EDGE_TYPES if r not in e_cnt),
        "governance_gap": _governance_gap(),
    }
    out["ok"] = True
    return out


def validate(cli=None, snap=None):
    """对账（WARN 语义）：图内未注册=旁路写入嫌疑；注册未在图=清库/
    改名待查；trust 档与 kg_source_rating 不一致=双处维护漂移。"""
    issues = []
    snap = snap or snapshot(cli)
    if not snap.get("ok"):
        return [("SKIP", "ontology", "graph", snap.get("error", "?"))]
    d = snap["drift"]
    for lbl in d["unregistered_labels"]:
        issues.append(("WARN", "ontology", "node", "图内未注册标签 %s" % lbl))
    for r in d["unregistered_rels"]:
        issues.append(("WARN", "ontology", "edge", "图内未注册边型 %s" % r))
    for lbl in d["registered_absent_labels"]:
        issues.append(("INFO", "ontology", "node", "注册标签图内为空 %s" % lbl))
    for r in d["registered_absent_rels"]:
        issues.append(("INFO", "ontology", "edge", "注册边型图内为空 %s" % r))
    # ── 治理一致性（C1，WARN-only：观测/提示，阻断在 gate）──
    gg = d.get("governance_gap", _governance_gap())
    for k in gg["bad_status"]:
        issues.append(("WARN", "ontology", "gov", "类型 %s 状态非法（须 %s）"
                       % (k, "/".join(STATUS_VALUES))))
    for k in gg["missing_replaced_by"]:
        issues.append(("WARN", "ontology", "gov", "deprecated 类型 %s 缺 replaced_by" % k))
    for k in gg["missing_owner"]:
        issues.append(("INFO", "ontology", "gov",
                       "类型 %s 未细化 steward（落 KGADMIN1 兜底）" % k))
    if gg["count"]:
        issues.append(("INFO", "ontology", "gov",
                       "治理缺口合计 %d（bad_status=%d, missing_replaced=%d）"
                       % (gg["count"], len(gg["bad_status"]), len(gg["missing_replaced_by"]))))
    return issues


def export(cli=None):
    """快照落盘 reports/ontology_snapshot_YYYYMMDD.json（滚动保留 8 份）。

    Neo4j 不可达 → 落纯注册表快照（无计数）并打印 SKIP，仍返回 issues
    （warn-only：每日链该步退出码恒 0，断图不阻断 brief）。
    """
    reports = os.path.join(HERE, "reports")
    os.makedirs(reports, exist_ok=True)
    out_path = os.path.join(
        reports, "ontology_snapshot_%s.json" % time.strftime("%Y%m%d")
    )
    snap = snapshot(cli)
    issues = validate(cli=None, snap=snap)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    olds = sorted(glob.glob(os.path.join(reports, "ontology_snapshot_*.json")))
    for old in olds[:-7]:
        try:
            os.remove(old)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/ontology.py", exc)
    for lv, kind, sub, msg in issues:
        print("[%s] %s %s/%s %s" % (lv, kind, "snapshot", sub, msg))
    print(
        "ontology-snapshot: node_types=%d edge_types=%d ok=%s 快照=%s"
        % (
            len(snap["node_types"]),
            len(snap["edge_types"]),
            snap.get("ok"),
            os.path.basename(out_path),
        )
    )
    return issues


# ── CI 门禁裁决（C2）────────────────────────────────────────
# 门禁阈值：FAIL=危害图谱健康的硬信号（阻断 CI）；WARN=治理成熟度/已知挂账（提示）。
# 设计：fail 阈值偏宽松（避免 D5 等已知挂账长期让 CI 全红致团队麻木），
# 但漂移失控（>fail 阈值）仍坚决阻断。
GATE_THRESHOLDS = {
    "unregistered_labels_warn": 10,    # 图内未注册标签 > 10 -> WARN
    "unregistered_labels_fail": 40,    # > 40 -> FAIL（语义漂移失控）
    "unregistered_rels_warn": 10,
    "unregistered_rels_fail": 40,
    "freshness_market_warn_days": 7,   # market_max_list_date 陈旧 > 7 天 -> WARN
    "freshness_market_fail_days": 30,  # > 30 天 -> FAIL（如 D5 停库副作用）
    "governance_bad_status_fail": 0,   # 非法状态 > 0 -> FAIL（schema 错误）
    "governance_missing_replaced_warn": 0,
    # C4 双时态索引：缺 1 条起 WARN（上线跟踪）；缺超 fail 值才 FAIL。
    # 默认 fail=999（关闭阻断）——首次部署全缺不该红；存量索引被误删时
    # 由运维把此值调小（如 5）启用阻断。
    "bitemporal_index_warn_missing": 1,
    "bitemporal_index_fail_missing": 999,
}


def _days_stale(date_val):
    """市场层保鲜日期（yyyymmdd 或 yyyy-mm-dd）-> 距今天数；无法解析->None。"""
    import datetime as _dt
    ss = str(date_val).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            dd = _dt.datetime.strptime(ss, fmt).date()
            return (_dt.date.today() - dd).days
        except ValueError:
            continue
    return None


def gate(snap=None, cli=None):
    """CI 门禁裁决（C2）：治理元数据 + 图实况 -> 结构化规则裁决。

    返回 dict：
      passed    bool  全 FAIL 规则通过=True
      exit_code int   0=PASS / 1=FAIL（CI 约定）
      rules     list  每条 {id, level(PASS/WARN/FAIL), passed, actual,
                          threshold, msg}
    依赖 snapshot() 的图实况；Neo4j 不可达按 FAIL 处理（门禁不得静默通过）。
    """
    snap = snap or snapshot(cli)
    rules = []
    if not snap.get("ok"):
        rules.append({"id": "graph_reachable", "level": "FAIL", "passed": False,
                      "actual": str(snap.get("error", ""))[:80],
                      "threshold": "Neo4j 可达",
                      "msg": "Neo4j 不可达，门禁无法裁决（按 FAIL 处理）"})
        return {"rules": rules, "passed": False, "exit_code": 1,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "tbox_version": TBOX_VERSION,
                "tbox_steward": TBOX_STEWARD}
    d = snap["drift"]
    gg = d.get("governance_gap", _governance_gap())
    T = GATE_THRESHOLDS

    # R1 未注册标签
    n = len(d["unregistered_labels"])
    lvl = "FAIL" if n > T["unregistered_labels_fail"] else (
        "WARN" if n > T["unregistered_labels_warn"] else "PASS")
    rules.append({"id": "drift_labels", "level": lvl, "passed": lvl != "FAIL",
                  "actual": n,
                  "threshold": "WARN>%d / FAIL>%d" % (
                      T["unregistered_labels_warn"], T["unregistered_labels_fail"]),
                  "msg": "图内未注册标签（K264 旁路写入嫌疑）"})

    # R2 未注册关系
    n = len(d["unregistered_rels"])
    lvl = "FAIL" if n > T["unregistered_rels_fail"] else (
        "WARN" if n > T["unregistered_rels_warn"] else "PASS")
    rules.append({"id": "drift_rels", "level": lvl, "passed": lvl != "FAIL",
                  "actual": n,
                  "threshold": "WARN>%d / FAIL>%d" % (
                      T["unregistered_rels_warn"], T["unregistered_rels_fail"]),
                  "msg": "图内未注册关系（K264 旁路写入嫌疑）"})

    # R3 保鲜探针（市场层停滞，D5 挂账副作用）
    md = (snap.get("freshness") or {}).get("market_max_list_date")
    days = _days_stale(md) if md else None
    if days is None:
        lvl, actual = ("WARN" if md is None else "PASS"), "无数据"
    elif days > T["freshness_market_fail_days"]:
        lvl, actual = "FAIL", "%d 天" % days
    elif days > T["freshness_market_warn_days"]:
        lvl, actual = "WARN", "%d 天" % days
    else:
        lvl, actual = "PASS", "%d 天" % days
    rules.append({"id": "freshness_market", "level": lvl, "passed": lvl != "FAIL",
                  "actual": actual,
                  "threshold": "WARN>%d天 / FAIL>%d天" % (
                      T["freshness_market_warn_days"], T["freshness_market_fail_days"]),
                  "msg": "市场层保鲜 market_max_list_date 陈旧度"})

    # R4 治理非法状态
    n = len(gg["bad_status"])
    lvl = "FAIL" if n > T["governance_bad_status_fail"] else "PASS"
    rules.append({"id": "gov_status", "level": lvl, "passed": lvl != "FAIL",
                  "actual": n, "threshold": "FAIL>0",
                  "msg": "非法的类型状态机值"})

    # R5 治理 deprecated 缺 replaced_by（WARN，不阻断）
    n = len(gg["missing_replaced_by"])
    lvl = "WARN" if n > 0 else "PASS"
    rules.append({"id": "gov_replaced", "level": lvl, "passed": True,
                  "actual": n, "threshold": "WARN>0",
                  "msg": "deprecated 类型缺 replaced_by"})

    # R6 双时态索引覆盖（C4）：期望集 vs SHOW INDEXES 实况。
    # 索引信息不可得（旧版本降级空表）按 SKIP 记 WARN=PASS 语义，不阻断。
    expected = _expected_indexes()
    live = set()
    for ix in snap.get("indexes") or []:
        lot = tuple(ix.get("labelsOrTypes") or ())
        props = tuple(ix.get("properties") or ())
        if lot and props:
            live.add((str(ix.get("entityType") or "").upper(), lot, props))
    if not live and not snap.get("indexes"):
        rules.append({"id": "bitemporal_index", "level": "PASS", "passed": True,
                      "actual": "SHOW INDEXES 不可得",
                      "threshold": "期望 %d 条" % len(expected),
                      "msg": "索引实况不可得（旧版本降级），跳过比对"})
    else:
        missing = sorted(
            "%s:%s(%s)" % (e, "|".join(lot), ",".join(p))
            for e, lot, p in expected - live
        )
        n = len(missing)
        lvl = ("FAIL" if n > T["bitemporal_index_fail_missing"]
               else "WARN" if n >= T["bitemporal_index_warn_missing"]
               else "PASS")
        rules.append({"id": "bitemporal_index", "level": lvl,
                      "passed": lvl != "FAIL",
                      "actual": "缺 %d/%d" % (n, len(expected)),
                      "threshold": "WARN>=%d / FAIL>%d" % (
                          T["bitemporal_index_warn_missing"],
                          T["bitemporal_index_fail_missing"]),
                      "msg": "双时态索引缺失（迁移未跑或被误删）: "
                             + "; ".join(missing[:5])
                             + (" ..." if n > 5 else "")})

    passed = all(r["passed"] for r in rules)
    return {"rules": rules, "passed": passed, "exit_code": 0 if passed else 1,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tbox_version": TBOX_VERSION,
            "tbox_steward": TBOX_STEWARD}


if __name__ == "__main__":
    from linreg import run_with_lineage

    def _cli():
        ap = argparse.ArgumentParser(description="L1 本体注册表")
        ap.add_argument("--export", action="store_true", help="快照+对账落盘")
        ap.add_argument("--validate", action="store_true", help="仅对账打印")
        ap.add_argument("--json", action="store_true", help="打印注册表 JSON")
        a = ap.parse_args()
        try:
            if a.export:
                export()
            elif a.validate:
                for lv, kind, sub, msg in validate():
                    print("[%s] %s/%s %s" % (lv, kind, sub, msg))
            elif a.json:
                print(json.dumps(registry_json(), ensure_ascii=False, indent=1))
            else:
                r = registry_json()
                print("=== L1 本体注册表 v%s（%s）===" % (VERSION, REGISTRY_TS))
                print(
                    "节点类型 %d / 边类型 %d"
                    % (r["counts"]["node_types"], r["counts"]["edge_types"])
                )
                by_dom = {}
                for lbl, m in r["node_types"].items():
                    by_dom.setdefault(m["domain"], []).append(lbl)
                for dom in sorted(by_dom):
                    print("[%s] %s" % (dom, " ".join(by_dom[dom])))
                print(
                    "trust 档位：A%d B%d C%d D%d"
                    % tuple(
                        sum(
                            1
                            for m in list(r["node_types"].values())
                            + list(r["edge_types"].values())
                            if m["trust"] == g
                        )
                        for g in "ABCD"
                    )
                )

                # ---- 治理面（C1）----
                gg = _governance_gap()
                print(
                    "治理（C1）：TBox v%s | 域 steward=%d | 治理缺口=%d"
                    % (TBOX_VERSION, len(DOMAIN_STEWARD), gg["count"])
                )
                if gg["bad_status"]:
                    print("  [!] 非法状态：%s" % ", ".join(gg["bad_status"]))
                if gg["missing_replaced_by"]:
                    print("  [!] deprecated 缺 replaced_by：%s"
                          % ", ".join(gg["missing_replaced_by"]))
                stew = {}
                for m in list(r["node_types"].values()) + list(r["edge_types"].values()):
                    o = m.get("governance", {}).get("owner", "?")
                    stew[o] = stew.get(o, 0) + 1
                print("  steward 覆盖：%s"
                      % ", ".join("%sx%d" % (k, v) for k, v in sorted(stew.items())))
        except Exception as ex:
            # warn-only：每日链本体快照步任何异常不得阻断后续 brief
            print("[SKIP] ontology %s" % str(ex)[:120])

    run_with_lineage("kg_daily_sync", "ontology_snap", _cli)
