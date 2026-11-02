# -*- coding: utf-8 -*-
"""数据中台目录构建器：扫描 data/ 下各仓（tdx/tushare/xueqiu/cnopendata）
→ data/catalog.json。

按《数据中台架构设计.md》§5：
- tables：物理表登记（source/store/layout/schema/date_col/code_col/freq/
  priority/manifest/freshness/rows/files/done/desc）
- aliases：逻辑别名 → 真源优先级候选（§5.3 真源矩阵）

自动探测（零迁移）：
- layout 按文件名推断：part_NNNN→part、d_YYYYMMDD→by_date、
  p_YYYYMMDD→by_period、<code>_<YYYYMM>→by_code_month、其余→by_code
- schema 取首个 parquet 的列；done 取仓库 _manifest.json；新鲜度按布局算
- priority/desc/date_col/code_col 特例用 TABLE_DEFS 声明，其余给默认

原子写（tmp+replace）；只读仓库数据文件，不改任何存量。

用法：
  python build_catalog.py            # 重建 data/catalog.json 并打印摘要
"""

import glob
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import logging

# 生命周期覆盖（planned/deprecated/archived）：真源 agent_platform/catalogs/data_lifecycle.yaml
_LIFECYCLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "datawarehouse")


def _lifecycle_module():
    try:
        import sys
        if _LIFECYCLE_DIR not in sys.path:
            sys.path.insert(0, _LIFECYCLE_DIR)
        import lifecycle  # noqa: WPS433
        return lifecycle
    except Exception:  # noqa: BLE001
        return None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "catalog.json")

# ── CnOpenData 静态历史包（WO-QT-20261021-088）──
# 免费外部源，一次落地 → 不再按日更新；每表一目录 + part_000N.parquet 分片，
# 表级溯源（源链接/许可/抓取与解析时间/行数/逐文件 sha256）在包清单
# _manifest.json 的内层 "tables"（双层壳，见 _load_manifest）。
CNOD_SOURCE = "cnopendata"
CNOD_DIR = os.path.join(HERE, "data", "cnopendata")
CNOD_MANIFEST = os.path.join(CNOD_DIR, "_manifest.json")
# 本批接入的 6 表（表名 = 目录名 = 包清单内层键；合计 151,421 行已回读核验）
CNOD_TABLES = (
    "chinese-dictionary-of-financial-sentiment",
    "firm-level-climate-change-exposure",
    "fepu",
    "czyx",
    "yspl",
    "gjzfssfc",
)

# freq 枚举（catalog 的节奏词表，唯一定义处）：
#   D=日 / W=周 / M=月 / Q=季 / Y=年 / 1min=分钟
# WO-QT-20261021-088 扩 Y：CnOpenData 6 表为 5 张省级年度面板 + 1 张年度
# 主表（气候暴露另含季度 part）。扩 Y 的依据：
#   ① 本文件此前无 freq 校验（_scan_dir 直接透传 TABLE_DEFS 值），扩枚举
#      只是把既有隐式词表显式化，不改任何既有表取值 → 238 表零波及；
#   ② 唯一按 freq 分档的下游 kg_source_rating.py L100 STALE_DAYS 已含
#      {"Y": 400}（年度容忍档），未知值回落 Q，故 Y 无需改下游即可生效；
#   ③ 全仓无「catalog freq ∈ 固定集合」的断言（test_kg_objectview 的
#      D/M/Q/E/static 是 objectview 扇区静态配置，非 catalog 字段）。
FREQ_ENUM = ("D", "W", "M", "Q", "Y", "1min")

WAREHOUSES = [  # (source, 数据根, manifest 路径)
    (
        "xtcloud",
        os.path.join(HERE, "data", "tdx"),
        os.path.join(HERE, "data", "tdx", "_manifest.json"),
    ),
    (
        "tushare",
        os.path.join(HERE, "data", "tushare"),
        os.path.join(HERE, "data", "tushare", "_manifest.json"),
    ),
    # 雪球仓（xueqiu-collector，设计 xueqiu-collector-design-2026-10-16 §3.3）
    (
        "xueqiu",
        os.path.join(HERE, "data", "xueqiu"),
        os.path.join(HERE, "data", "xueqiu", "_manifest.json"),
    ),
    # CnOpenData 静态历史包（WO-QT-20261021-088）：6 张表各一目录
    # （part_000N.parquet），表级溯源在包清单 _manifest.json 内层 "tables"。
    (CNOD_SOURCE, CNOD_DIR, CNOD_MANIFEST),
]
LEGACY_US = os.path.join(HERE, "data", "us")  # 旧美股逐股（deprecated）
CRYPTO_DIR = os.path.join(HERE, "data", "crypto")  # 加密货币日线
INFO_DB = os.path.join(HERE, "data", "tdx", "tdx_info.db")
XQ_INFO_DB = os.path.join(HERE, "data", "xueqiu", "xueqiu_info.db")  # 雪球舆情库
GUBA_DIR = os.path.join(
    HERE, "data", "guba"
)  # guba 150 码 jsonl（kg-graph-storage-design §3.1）
# 设计 §3.2：三个新扫描分支对应的雪球 parquet 表（采集器首跑前目录不存在 → 占位注册）
XQ_PARQUET_TABLES = [
    "xq_quote_snap",
    "xq_kline_d",
    "xq_capital_flow",
    "xq_northbound",
    "xq_earningforecast",
    "xq_hot_deal",
    "xq_inner_trade",
]

# 数据地基模块章程 §3（2026-10-16）：外部库表登记（代码即契约）。
# PG/Neo4j 等物理独立存储不搬数据，只在 catalog 登记元数据让读层可路由；
# 每次重建自动并入，不依赖手工编辑 JSON。v2_bar_daily 读路径=datamart
# layout "pg" → data_loader.load_table（直连 5432 zhice-db，失败 docker 兜底）。
EXTERNAL_TABLES = {
    "v2_bar_daily": {
        "source": "pg",
        "store": "pg://zhice-db:5446/zhice_os_v2/v2_bar_daily",
        "layout": "pg",
        "schema": [
            "symbol",
            "market",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "pre_close",
        ],
        "date_col": "trade_date",
        "code_col": "symbol",
        "primary_key": ["symbol", "trade_date"],
        "freq": "D",
        "priority": 3,
        "manifest": None,
        "freshness": None,
        "rows": None,
        "files": 0,
        "done": True,
        "deprecated": False,
        "desc": "PG zhice-db 日线（zhice-os v2，多市场；symbol 为库内形态）",
        "readiness_basis": "external_db",
        "verification_status": "unknown",
        "manifest_status": "not_applicable",
        "status": "registered",
    },
}

# tdx_read 同款口径：财务类日期列 end_date
END_DATE_TABLES = {
    "ts_income",
    "ts_balancesheet",
    "ts_cashflow",
    "ts_fina_indicator",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
}
# 特例声明（未列出的表给默认值）。
TABLE_DEFS = {
    # ── tushare 仓 ──
    "anns_d": {
        "date_col": "ann_date",
        "freq": "D",
        "desc": "公告（按日分片，tushare 兜底源）",
    },
    "trade_cal": {
        "date_col": "cal_date",
        "code_col": None,
        "primary_key": ["exchange", "cal_date"],
        "desc": "交易日历（SSE 全量）",
    },
    "stock_basic": {"primary_key": ["ts_code"], "desc": "A股基础信息（tushare）"},
    "stock_company": {
        "code_col": "ts_code",
        "freq": "Q",
        "desc": "A股上市公司详情（经营范围/高管/员工，图谱公司实体源）",
    },
    "income": {
        "code_col": "ts_code",
        "date_col": "end_date",
        "freq": "Q",
        "desc": "A股利润表（全字段85列，逐码，图谱质量因子源）",
    },
    "balancesheet": {
        "code_col": "ts_code",
        "date_col": "end_date",
        "freq": "Q",
        "desc": "A股资产负债表（全字段152列，逐码）",
    },
    "cashflow": {
        "code_col": "ts_code",
        "date_col": "end_date",
        "freq": "Q",
        "desc": "A股现金流量表（全字段97列，逐码，FCFF/FCFE）",
    },
    "fina_indicator": {
        "code_col": "ts_code",
        "date_col": "end_date",
        "freq": "Q",
        "desc": "A股财务指标（全字段108列，逐码，ROE/ROA/ROIC）",
    },
    "shibor_lpr": {
        "date_col": "date",
        "freq": "M",
        "desc": "LPR 利率 1y/5y（monetary_policy 收紧后的替代）",
    },
    "sf_month": {
        "date_col": "month",
        "freq": "M",
        "desc": "社融月度（增量/累计/存量，月份 6 位）",
    },
    "hk_basic": {"desc": "港股基础信息"},
    "us_basic": {"desc": "美股基础信息"},
    "us_daily": {"freq": "D", "desc": "美股日线（逐码，建设中）"},
    "daily": {
        "date_col": "trade_date",
        "code_col": "ts_code",
        "freq": "D",
        "desc": "A股日线深历史（tushare 按日，1990→2023-07）",
    },
    "adj_factor": {
        "date_col": "trade_date",
        "code_col": "ts_code",
        "freq": "D",
        "desc": "A股复权因子（tushare 按日，回测前复权源）",
    },
    "cb_basic": {"desc": "可转债基础"},
    "cb_daily": {"freq": "D", "desc": "可转债日线（逐码）"},
    "bak_basic": {"freq": "D", "desc": "备选基础信息（逐码）"},
    "stk_mins": {
        "date_col": "trade_time",
        "freq": "1min",
        "desc": "A股 1 分钟线（中证800成分，逐码月度）",
    },
    "idx_mins": {
        "date_col": "trade_time",
        "freq": "1min",
        "desc": "指数 1 分钟线（逐码月度）",
    },
    "sw_mins": {
        "date_col": "trade_time",
        "freq": "1min",
        "desc": "申万指数 1 分钟线（逐码月度）",
    },
    "etf_mins": {
        "date_col": "trade_time",
        "freq": "1min",
        "desc": "ETF 1 分钟线（逐码月度）",
    },
    "sw_classify_l1": {"code_col": None, "desc": "申万一级分类"},
    "crypto_daily": {
        "date_col": "trade_date",
        "code_col": "ts_code",
        "freq": "D",
        "manifest": None,
        "desc": "加密货币日线（37 交易对，BTC/ETH 等，逐对单文件）",
    },
    "index_global": {
        "date_col": "trade_date",
        "code_col": "ts_code",
        "freq": "D",
        "manifest": None,
        "desc": "全球指数日线（20 指数 HSI/SPX/DJI/IXIC/N225 等，逐指数单文件）",
    },
    "ts_global_fut": {
        "date_col": "trade_date",
        "code_col": "ts_code",
        "freq": "D",
        # 2026-11-02 S3-01 B 组（task-30）：原 manifest=None → _scan_dir 置
        # not_applicable，qualify_probe 对 not_applicable 仍回退 tushare manifest，
        # 表不在其中即误判 manifest_missing。本表属 tdx 仓（store=data/tdx/ts_global_fut，
        # 由 WAREHOUSES 的 xtcloud 循环扫描），故声明 tdx/_manifest.json：
        # _scan_dir 以所属仓 mpath 为准（此值仅作非空断言/自文档）；rebuild 后
        # manifest_status 由 not_applicable→missing，可由 manifest_repair 补表级键。
        "manifest": os.path.join(HERE, "data", "tdx", "_manifest.json"),
        "desc": "外盘商品期货日线（GF_CL00Y/GC00Y/HG00Y 逐品种单文件，"
        "global_fut_sync 维护；2026-10-16 收编回 data/）",
    },
    "news": {"date_col": "datetime", "freq": "D", "desc": "新闻快讯（tushare）"},
    "major_news": {"date_col": "pub_time", "freq": "D", "desc": "大事提醒（tushare）"},
    "npr": {
        "date_col": "pubtime",
        "freq": "D",
        "desc": "停复牌（tushare，事件驱动节奏）",
    },
    "research_report": {"desc": "研报（tushare 兜底源）"},
    "top10_holders": {
        "layout": "by_period",
        "date_col": "ann_date",
        "primary_key": [
            "ts_code",
            "ann_date",
            "end_date",
            "holder_name",
            "holder_type",
        ],
        "freq": "Q",
        "desc": "十大股东（报告期分片，公告日查询）",
    },
    "top10_floatholders": {
        "layout": "by_period",
        "date_col": "ann_date",
        "primary_key": [
            "ts_code",
            "ann_date",
            "end_date",
            "holder_name",
            "holder_type",
        ],
        "freq": "Q",
        "desc": "十大流通股东（报告期分片）",
    },
    "top_list": {
        "date_col": "trade_date",
        "freq": "D",
        "desc": "龙虎榜每日明细（tushare 兜底源）",
    },
    "irm_qa_sh": {"freq": "D", "desc": "互动易问答-沪（逐码）"},
    "irm_qa_sz": {"freq": "D", "desc": "互动易问答-深（逐码）"},
    # ── tdx 仓（仅 desc；其余与 tdx_read 口径一致）──
    "ts_adj_factor": {
        "date_col": "trade_date",
        "freq": "D",
        "desc": "复权因子（tushare 直连，adj_factor_sync 维护，AdjFactorSync 18:10）",
    },
    "ts_daily": {"freq": "D", "desc": "A股日线行情（xtcloud，真源）"},
    "ts_stock_basic": {
        "primary_key": ["ts_code"],
        "desc": "A股基础信息（xtcloud，真源）",
    },
    "ts_daily_basic": {"freq": "D", "desc": "A股每日指标（xtcloud）"},
    "ts_hk_daily": {"freq": "D", "desc": "港股日线（xtcloud，真源）"},
    "ts_index_daily": {"freq": "D", "desc": "指数日线（xtcloud）"},
    "ts_index_basic": {"desc": "指数基础信息"},
    "ts_index_weight": {"desc": "指数成分权重"},
    "ts_etf_basic": {"desc": "ETF 基础信息"},
    "ts_fund_basic": {"desc": "基金基础信息"},
    "ts_fund_daily": {"freq": "D", "desc": "基金日线"},
    "ts_margin_detail": {"freq": "D", "desc": "融资融券明细"},
    "ts_moneyflow": {"freq": "D", "desc": "个股资金流向"},
    "ts_moneyflow_hsgt": {"freq": "D", "desc": "沪深港通资金"},
    "ts_top_list": {"freq": "D", "desc": "龙虎榜"},
    "ts_top_inst": {"freq": "D", "desc": "龙虎榜机构明细"},
    "ts_ths_daily": {"freq": "D", "desc": "同花顺板块日线"},
    "ts_ths_index": {"desc": "同花顺指数"},
    "ts_ths_member": {"desc": "同花顺板块成分"},
    "tdx_sector_daily": {"code_col": "code", "freq": "D", "desc": "通达信板块日线"},
    "tdx_sector_def": {"desc": "通达信板块定义"},
    "tdx_sector_member": {"desc": "通达信板块成分"},
    "tdx_sector_min": {
        "date_col": "trade_date",
        "code_col": "code",
        "primary_key": ["code", "trade_date", "trade_time"],
        "freq": "1min",
        "desc": "通达信板块分钟线",
    },
    "tdx_sector_min_tail": {
        "date_col": "trade_date",
        "code_col": "code",
        "primary_key": ["code", "trade_date", "trade_time"],
        "freq": "1min",
        "desc": "通达信板块分钟线增量尾部",
    },
    "us_legacy": {
        "source": "legacy",
        "priority": 2,
        "deprecated": True,
        "freq": "D",
        "manifest": None,
        "desc": "旧美股日线（逐股单文件，待 us_daily 收口后废弃）",
    },
}

# 信息说明不推定采集完成或业务覆盖。
for _name, _desc in {
    "ts_balancesheet": "A股资产负债表（xtcloud）",
    "ts_cashflow": "A股现金流量表（xtcloud）",
    "ts_fina_indicator": "A股财务指标（xtcloud）",
    "ts_income": "A股利润表（xtcloud）",
    "cyq_chips": "筹码分布（逐码，文件名提供证券身份）",
    "cyq_perf": "筹码胜率与成本指标",
    "index_weight": "指数成分及权重（成分身份见 con_code）",
    "irm_qa_sh_probe": "沪市互动问答单日探针（不代表全量覆盖）",
    "irm_qa_sz_probe": "深市互动问答单日探针（不代表全量覆盖）",
    "limit_list_d": "每日涨跌停明细",
    "stk_holdernumber": "股东户数",
    "stk_managers": "上市公司管理层信息",
    "sw_l1_member": "申万一级行业成分关系（静态集合）",
}.items():
    TABLE_DEFS.setdefault(_name, {})["desc"] = _desc
for _market, _label in (("hk", "港股"), ("us", "美股")):
    for _table, _label_table in (
        ("balancesheet", "资产负债表"),
        ("cashflow", "现金流量表"),
        ("fina_indicator", "财务指标"),
        ("income", "利润表"),
    ):
        TABLE_DEFS[_market + "_" + _table] = {
            "desc": _label + _label_table + "（Tushare）",
            "date_col": "end_date",
            "freq": "Q",
        }
TABLE_DEFS["ts_rt_min"] = {
    "desc": "实时分钟行情（xtcloud，按实际 time 字段核验）",
    "date_col": "time",
    "freq": "1min",
}
# ── freq 标签修正（2026-11-02 task-33 / S3-01 C 类）─────────────────────────
# 依据：docs/audit/2026-11-02_xtcloud停更排查.md §6「freq 标签修正清单」。
# 这些 xtcloud 表此前未在 TABLE_DEFS 登记 freq → `_scan_dir` 默认 freq=D（7d 阈值），
# 财报/预测/事件类正常发布节律被判 stale（qualify_probe stale_gt_7d）。
# 本批按真实发布节律改 Q/M/W，只改 freq，不动 date_col/layout/primary_key 等其它字段；
# 生效需下一次 build_catalog + registry_rebuild（本单不跑，见回执）。
_FREQ_FIX_20 = {
    # 财报/报告期类（Q）：最新业务日=报告期 end_date/ann_date，按季披露节奏；
    # _vip 为 xtcloud 财务 VIP 接口表，节律同主表，一并修正。
    "ts_income": "Q", "ts_income_vip": "Q",
    "ts_balancesheet": "Q", "ts_balancesheet_vip": "Q",
    "ts_cashflow": "Q", "ts_cashflow_vip": "Q",
    "ts_fina_indicator": "Q", "ts_fina_indicator_vip": "Q",
    "ts_fina_mainbz": "Q", "ts_fina_mainbz_vip": "Q",
    # 业绩预告/快报/分红/披露计划：事件驱动但按季窗口（Q）
    "ts_forecast": "Q", "ts_forecast_vip": "Q",
    "ts_express": "Q", "ts_express_vip": "Q",
    "ts_dividend": "Q", "ts_dividend_vip": "Q",
    "ts_disclosure_date": "Q",
    # 股权激励/股东户数：季度披露节奏（Q）
    "ts_stk_rewards": "Q", "ts_stk_holdernumber": "Q",
    # 指数周线：周频（W），catalog 原标 D 属错标（审计 §3 ts_index_weekly）
    "ts_index_weekly": "W",
}
for _freq_name, _freq_value in _FREQ_FIX_20.items():
    TABLE_DEFS.setdefault(_freq_name, {})["freq"] = _freq_value
# ── catalog v21 字段：24 张 A/B 类豁免口径修正（2026-11-02 task-41 / S3-01）──
# 依据：deliverables/2026-11-02_24张豁免口径方案_v1.md §六（24 张未声明豁免动态表口径方案）。
# 只增 date_col / static_no_date / freq 字段及注释，不动 layout/schema/priority/desc；
# date_col 均为该表 parquet footer 实测存在且可解析的列（已逐表核验，见方案 §三/§七）。
# 生效需下一次 build_catalog + registry_rebuild（本单不跑，回执给 Lead 命令）。
_CATALOG_V21_FIELDS = {
    # A1（10）：footer 业务日期列，rebuild+qualify 后预期 evidence_ok（lag 3~5d）
    "ts_cctv_news": {"date_col": "date"},
    "ts_major_news": {"date_col": "pub_time"},
    "ts_news": {"date_col": "datetime"},
    "ts_report_rc": {"date_col": "report_date"},
    "ts_stk_surv": {"date_col": "surv_date"},
    "ts_new_share": {"date_col": "ipo_date"},
    "ts_us_tbr": {"date_col": "date"},
    "ts_us_trycr": {"date_col": "date"},
    "ts_us_tycr": {"date_col": "date"},
    "ts_wz_index": {"date_col": "date"},
    # A2（2）：改后仍 partial 但 reason 更准（真实滞后，非口径缺失）
    "ts_cb_price_chg": {"date_col": "publish_date"},
    # ts_ths_member：_fetched_at 为**摄入时间戳代理**（非业务日期）——用于反映同步是否在跑；
    # 重跑历史也会让 latest 变新，读方勿当业务日期，语义已在方案 §八 风险项标注。
    "ts_ths_member": {"date_col": "_fetched_at"},
    # A3（1）：成分调整低频（in_date=最新成分纳入日），date_col 与 freq 同改；
    # D→M（40d 容忍）避免把"长期无调整"误判断流。
    "ts_index_member_all": {"date_col": "in_date", "freq": "M"},
    # A4（5）：宏观月/季表。**前置**：qualify_probe.norm_date 需支持 YYYYMM→月末、YYYYQn→季末；
    # 工具未改前补 date_col 会使 probe 从 no_date_column 变 no_latest_date（仍 partial，fail-visible）。
    "ts_cn_cpi": {"date_col": "month"},
    "ts_cn_m": {"date_col": "month"},
    "ts_cn_pmi": {"date_col": "MONTH"},   # footer 实际列名为大写 MONTH
    "ts_cn_ppi": {"date_col": "month"},
    "ts_cn_gdp": {"date_col": "quarter"},
    # B（3）：静态字典/基础信息表——catalog 显式声明 static_no_date（优于代码白名单硬编码）
    "ts_fx_obasic": {"static_no_date": True},
    "ts_hm_list": {"static_no_date": True},
    "ts_index_classify": {"static_no_date": True},
}
for _v21_name, _v21_fields in _CATALOG_V21_FIELDS.items():
    TABLE_DEFS.setdefault(_v21_name, {}).update(_v21_fields)
# ── catalog v24 字段：unknown 非数据侧治理（2026-11-02 task-47 / S3-01）──
# 依据：deliverables/2026-11-02_unknown非数据侧治理_回执.md（逐表核查）。
# ① 日历/未来期表：date_col 允许未来日期（交易日历/可转债上市日等）→ qualify_probe 不判 future_date，
#    catalog 以 future_ok=True 显式声明（payload date_policy.future_ok_tables 可见）；
# ② shibor：footer 实测日期列 `date`（值 YYYYMMDD，3 片）→ 补 date_col；
# ③ ts_sge_basic：footer 无可用业务日期列（trade_time 为交易时段文本、list_date 为上市日静态属性）
#    → 改登记 static_no_date=True（静态交易参数表，13 行），date_col 置 None（不再误用 trade_time）；
# ④ firm-level-climate-change-exposure：date_col=year 保持；qualify_probe 已支持 YYYY→年末归一 +
#    freq=Y 阈值（400d），rebuild+apply 后可判 evidence_ok。
_CATALOG_V24_FIELDS = {
    "shibor": {"date_col": "date", "freq": "D",
               "desc": "Shibor 隔夜/1w/2y（tushare，按日分片，footer 日期列 date）"},
    # ts_sge_basic：静态交易参数表（13 行），无新鲜度维度
    "ts_sge_basic": {"date_col": None, "date_status": "not_applicable",
                     "static_no_date": True, "freq": "D"},
    # 日历/未来期表（date_col 可含未来日期，属正常语义）
    "cb_basic": {"future_ok": True},
    "trade_cal": {"future_ok": True},
    "ts_trade_cal": {"future_ok": True},
    "ts_hk_tradecal": {"future_ok": True},
    "ts_us_tradecal": {"future_ok": True},
}
for _v24_name, _v24_fields in _CATALOG_V24_FIELDS.items():
    TABLE_DEFS.setdefault(_v24_name, {}).update(_v24_fields)
# ── 雪球仓（xueqiu，设计 §3.3：7 张 parquet 表）──
TABLE_DEFS.update(
    {
        "xq_quote_snap": {
            "date_col": "trade_date",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球收盘快照（quotec 免 token，缠论池）",
        },
        "xq_kline_d": {
            "date_col": "timestamp",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球前复权日线（逐码，交叉验证源）",
        },
        "xq_capital_flow": {
            "date_col": "trade_date",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球个股资金流",
        },
        "xq_northbound": {
            "date_col": "trade_date",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球北向持股（空日不落片）",
        },
        "xq_earningforecast": {
            "date_col": "ann_date",
            "code_col": "ts_code",
            "freq": "W",
            "desc": "券商盈利预测（雪球）",
        },
        "xq_hot_deal": {
            "date_col": "trade_date",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球热度交易榜（akshare 补充源）",
        },
        "xq_inner_trade": {
            "date_col": "trade_date",
            "code_col": "ts_code",
            "freq": "D",
            "desc": "雪球内部交易/高管增减持（akshare）",
        },
    }
)
# K-STOCK-398（10-15）：打板情绪周期底座。源limit_list_d 自算，不依赖 tushare 打板套餐。
TABLE_DEFS["ts_emotion_daily"] = {
    "desc": "打板情绪周期日频面板（涨停/炸板/最高板/封板率/晋级率/封单额）",
    "date_col": "trade_date",
    "freq": "D",
    "layout": "single",
}
TABLE_DEFS["ts_emotion_ladder"] = {
    "desc": "连板梯队明细（≥2板：连板数/炸板/封单额/首末封/流通市值）",
    "date_col": "trade_date",
    "freq": "D",
    "layout": "single",
}
# 快照/成分表（无业务日期列 → date_status=not_applicable，避免误算新鲜度）。
# 2026-11-02 task-41（catalog v21）：ts_ths_member 已移出本清单——改登记 date_col=_fetched_at
# 摄入时间代理（动态成分表，需可观测同步是否在跑；见 _CATALOG_V21_FIELDS）。
for _name in (
    "tdx_sector_def",
    "tdx_sector_member",
    "stock_company",
    "sw_classify_l1",
    "sw_l1_member",
):
    TABLE_DEFS[_name]["date_col"] = None
    TABLE_DEFS[_name]["date_status"] = "not_applicable"

# ── CnOpenData 静态历史包（WO-QT-20261021-088）──
# 6 表一次落地即终态（非按日更新）：freshness 停在包内末年（或无可测日期），
# _verify_status 因此判 verified_stale；desc 均写明覆盖区间与静态包属性。
# date_col/code_col 只登记列名，不改写源数据形态（fepu.stkcd 等整数保持整数）。
TABLE_DEFS.update(
    {
        "chinese-dictionary-of-financial-sentiment": {
            "layout": "part",
            "date_col": None,
            "code_col": None,
            "date_status": "not_applicable",
            "primary_key": [],
            "freq": "Y",
            # WO-QT-20261021-102：无时间维的**静态数据**（词表本身不带日期），
            # 与 tdx_sector_def/stock_company 等「快照表」不是一回事：快照表有
            # 采集时点语义、只是没登记日期列；词表永远没有时间维。显式打标 →
            # _verify_status 判 static_no_date（正常态，disk_health 不告警），
            # 不再借 verified 冒充「已验证有日期」。
            "static_no_date": True,
            "desc": "中文金融情感词典（姜富伟等 2020，正向 3338 + 负向 5890 共 "
            "9228 词，词表无时间维度；静态历史包，非按日更新）",
        },
        "firm-level-climate-change-exposure": {
            "layout": "part",
            "date_col": "year",
            "code_col": "isin",
            "primary_key": ["isin", "year", "quarter"],
            "freq": "Y",
            "desc": "公司层面气候变化暴露指数（OSF 2024Q4 版，81759 行，"
            "2001–2024；主表按年度 year 登记，同表另含季度面板 quarter"
            "（firmquarter，年度片无该列）；静态历史包，非按日更新）",
        },
        "fepu": {
            "layout": "part",
            "date_col": "year",
            "code_col": "stkcd",
            "primary_key": ["stkcd", "year"],
            "freq": "Y",
            "desc": "企业年报前瞻性信息披露（fepu 句子级/词级强度，A股 2006–2024，"
            "57044 行，(stkcd,year) 唯一；静态历史包，非按日更新）",
        },
        "czyx": {
            # WO-QT-20261021-101：主键 (prov, year, dim)。
            # 6 个分片是同一「省×年」网格上的 6 张维度表（收入/支出/债务/社保/
            # 公共服务/均等化），过去没有维度列可加入身份，只能留空主键换取
            # 「不去重、保留全部 1995 行」；代价是 (prov,year) 在分片间大量重复，
            # 消费方无从判断某行属于哪张指数表。
            # 现 ingest_open.py 对多工作表 Excel 按片写入 dim=工作表名，使
            # (prov,year,dim) 成为真唯一键（实测 1860 个非空键全唯一），主键因此
            # 可以声明：声明后 datamart 去重仍保留 1995 行（末片 part_0005 无
            # prov/year，键不全 → _normalize 的 ~complete 分支保留全部 135 行）。
            # WO-QT-20261021-106：增 prov_std（官方全称），prov 保留源值。
            "layout": "part",
            "date_col": "year",
            "code_col": "prov",
            "primary_key": ["prov", "year", "dim"],
            "freq": "Y",
            "desc": "中国财政可持续性指数（省级六维面板：收入/支出/债务/社保/"
            "公共服务/均等化，6 分片同省年网格，2008–2019，31 省；dim 列"
            "标识来源工作表；末片 part_0005 为宽表无 year 列；prov 已做"
            "空白归一（归一前 91 个取值 / 归一后各分片内 31 省），"
            "prov_std 为官方全称归一列（上海→上海市、内蒙古→内蒙古自治区；"
            "原 prov 保留源值，片 0000 用简称 / 片 0001–0004 用全称），"
            "跨表按省份 join 请用 prov_std；静态历史包，非按日更新）",
        },
        "yspl": {
            # WO-QT-20261021-106：增 prov_std（官方全称）；省份 保留源值（简称）。
            "layout": "part",
            "date_col": "年份",
            "code_col": "省份",
            "primary_key": ["省份", "年份"],
            "freq": "Y",
            "desc": "省级预算决算收支面板（四本预算：一般公共/政府性基金/国有资本/"
            "社会保险，决算+预算口径，31 省 1994–2017，744 行；省份 为源"
            "简称，prov_std 为官方全称归一列，跨表 join 请用 prov_std；"
            "静态历史包，非按日更新）",
        },
        "gjzfssfc": {
            # WO-QT-20261021-106：省份（简称）→ 增 prov_std；省份代码 是 11/12
            # 这类数字代码，保持原样，不做名称归一。
            "layout": "part",
            "date_col": "年",
            "code_col": "省份代码",
            "primary_key": ["省份代码", "年"],
            "freq": "Y",
            "desc": "国家政府支出财政分成（中央/地方/省本级/市本级各税种占比，"
            "31 省 1994–2014，651 行；省份代码 为数字代码（11/12…），"
            "省份 为源简称，prov_std 为官方全称归一列，跨表 join 请用 "
            "prov_std；静态历史包，非按日更新）",
        },
    }
)

# 财报主键保留同报告期的报表类型及披露版本；不存在的列在扫描时剔除。
for _tbl in ("income", "balancesheet", "cashflow", "fina_indicator"):
    for _name in (_tbl, "ts_" + _tbl):
        TABLE_DEFS.setdefault(_name, {})["primary_key"] = [
            "ts_code",
            "end_date",
            "ann_date",
            "f_ann_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        ]
for _tbl in ("anns_d", "research_report", "news", "major_news", "npr"):
    TABLE_DEFS[_tbl]["primary_key"] = []
# 高管事件表无时序唯一键：同日公告多名高管/一人多职都是独立事件行，
# 主键必须覆盖姓名+职务+任期，否则 dm.read 去重会折叠（57.5万→12万判例）。
# 不在 schema 的列扫描时自动剔除（_scan_dir L456 同款过滤）。
TABLE_DEFS.setdefault("stk_managers", {})["primary_key"] = [
    "ts_code",
    "name",
    "title",
    "ann_date",
    "begin_date",
    "end_date",
]

# §5.3 逻辑别名（resolves 按真源优先级排序）
ALIASES = {
    "daily": {
        "resolves": ["ts_daily", "daily"],
        "primary": "ts_daily",
        "note": "xtcloud 真源（2023-07 起）+ tushare 深历史（1990 起），"
        "零重叠 concat，read() 按(code,date)去重",
    },
    "stock_basic": {
        "resolves": ["ts_stock_basic", "stock_basic"],
        "primary": "ts_stock_basic",
        "merge_key": ["ts_code"],
        "note": "xtcloud 优先，tushare 补深度",
    },
    "hk_daily": {
        "resolves": ["ts_hk_daily"],
        "note": "tushare hk_daily 未建仓，仅 xtcloud",
    },
    "us_daily": {
        "resolves": ["us_daily", "us_legacy"],
        "primary": "us_daily",
        "note": "tushare 收口前降级旧美股（done 联动自动切换）",
    },
    "notice": {
        "resolves": ["stock_notice", "anns_d"],
        "primary": "stock_notice",
        "merge_key": [],
        "merge_mode": "fallback",
        "note": "公告：tdx 优先，tushare 补深度",
    },
    "report": {
        "resolves": ["stock_report", "research_report"],
        "primary": "stock_report",
        "merge_key": [],
        "merge_mode": "fallback",
        "note": "研报：tdx 优先，tushare 补深度",
    },
    "news": {
        "resolves": ["stock_news"],
        "note": "JEV 已接线情报总线（event_graph 经 "
        "/api/intel/bus since_id 水位读，2026-10-19）；"
        "premarket 报告聚合仍直读（created_at 窗口口径，待切）",
    },
    "index_daily": {"resolves": ["ts_index_daily"], "note": "指数日线（xtcloud）"},
    "moneyflow": {"resolves": ["ts_moneyflow"], "note": "个股资金流向（xtcloud）"},
    "margin": {"resolves": ["ts_margin_detail"], "note": "融资融券明细（xtcloud）"},
    "income": {
        "resolves": ["income", "ts_income"],
        "merge_key": ["ts_code", "end_date"],
        "note": "A股利润表：tushare 全字段优先，xtcloud 有限字段降级",
    },
    "balancesheet": {
        "resolves": ["balancesheet", "ts_balancesheet"],
        "merge_key": ["ts_code", "end_date"],
        "note": "A股资产负债表：tushare 全字段优先，xtcloud 降级",
    },
    "cashflow": {
        "resolves": ["cashflow", "ts_cashflow"],
        "merge_key": ["ts_code", "end_date"],
        "note": "A股现金流量表：tushare 全字段优先，xtcloud 降级",
    },
    "fina_indicator": {
        "resolves": ["fina_indicator", "ts_fina_indicator"],
        "merge_key": ["ts_code", "end_date"],
        "note": "A股财务指标：tushare 全字段优先，xtcloud 降级",
    },
}


def _infer_layout(files):
    for f in files:
        if re.fullmatch(r"part_\d+\.parquet", f):
            return "part"
    for f in files:
        if re.fullmatch(r"d_\d{8}\.parquet", f):
            return "by_date"
    for f in files:
        if re.fullmatch(r"p_\d{8}\.parquet", f):
            return "by_period"
    for f in files:
        if re.search(r"_\d{6}\.parquet$", f):
            return "by_code_month"
    return "by_code"


def _dcol(tbl, layout):
    definition = TABLE_DEFS.get(tbl, {})
    if "date_col" in definition:
        return definition["date_col"]
    if layout == "by_code_month":
        return "trade_time"
    if tbl in END_DATE_TABLES:
        return "end_date"
    return "trade_date"


def _ccol(tbl):
    return TABLE_DEFS.get(tbl, {}).get("code_col", "ts_code")


def _dmax(series):
    vals = [str(v)[:10].replace("-", "") for v in series.tolist()]
    vals = [v[:8] for v in vals if v[:8].isdigit()]
    return max(vals) if vals else None


def _rows_of(path):
    """parquet 行数（footer 快读不解数据，~0.1ms/文件）；失败返回 -1。"""
    try:
        from pyarrow.parquet import ParquetFile

        return ParquetFile(path).metadata.num_rows
    except Exception:
        return -1


def _freshness(store, layout, dcol, files):
    """本地最新日期（YYYYMMDD）；无日期列/空表返回 None。

    K99：by_date 布局空片回退——休市日/接口空返回时 pull 侧落 0 行空片
    防重拉（kg_bclass「0 行也落片」设计），文件名日期≠数据日期。纯按
    文件名取 max 会制造新鲜度幻觉（断流天天落空片 → freshness 天天=
    今天 → source_fresh 永远绿）。改为从新到旧读首个非空分片的真实
    最大日期；dcol 缺失/全空回退文件名口径保持兼容。

    K100：by_code/by_code_month 空壳失明修复——逐码布局 0 行空壳是
    常态（停牌/未披露/增量拉取无新数据的个体；实证 irm_qa_sz 348 空
    壳几乎全部聚集在 mtime 末端=最后一次增量批，stk_mins 811 空），原
    「取最新一个文件」实现被打瞎（7 表 fresh=None）。改为 footer 行数
    快筛（早停）跳空壳，读首个非空文件的 dcol；全空 → None（rating
    侧 files 兜底转为可见告警）。
    """
    if not files or not dcol:
        return None
    try:
        if layout == "by_date":
            dated = [f for f in files if re.search(r"d_(\d{8})", f)]
            if not dated:
                return None
            if dcol:
                for f in sorted(dated, reverse=True):
                    try:
                        df = pd.read_parquet(os.path.join(store, f))
                    except Exception:
                        continue  # 单文件损坏：看更旧的
                    if dcol not in df.columns:
                        break  # 列不存在：回退文件名口径
                    v = _dmax(df[dcol])
                    if v:
                        return v  # 最新非空分片的数据日期
                else:
                    return None  # 循环走完=全是空片
            return max(re.search(r"d_(\d{8})", f).group(1) for f in dated)
        if layout == "by_code_month":
            # K100：文件名（月）降序 footer 筛跳空壳，首个非空读 dcol
            cands = sorted(
                (f for f in files if re.search(r"_\d{6}\.parquet$", f)), reverse=True
            )
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue  # 空壳：停牌/未披露个体常态
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue  # 损坏或列缺失：看次新
                if v:
                    return v
            return None
        if layout == "by_code":
            # K100：mtime 降序 footer 筛跳空壳（聚集批可达数百个，
            # 实证 irm_qa_sz rank347 才见非空）。
            # K102：非空中取前 8 个读 dcol 取 max——「首个非空」有随机
            # 偏差（同批写入 mtime 平局，排序不稳定，实证 us_daily 取到
            # 停更票尾 09-30 而全表主力水位 10-02）；8 采样全停更概率≈0
            cands = sorted(
                files,
                key=lambda f: os.path.getmtime(os.path.join(store, f)),
                reverse=True,
            )
            seen = 0
            best = None
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue  # 空壳：增量拉取无新数据的个体
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue  # 损坏或列缺失：看次新
                if v and (not best or v > best):
                    best = v
                seen += 1
                if seen >= 8:
                    break
            return best
        # part：全分片只读日期列（分片数少，代价可忽略）
        # WO-QT-20261021-088：逐片容错——同一逻辑表的分片 schema 可以不一致
        # （实证 CnOpenData czyx 尾片 part_0005 是 2008..2018 宽表，无 year
        # 列），原列表推导一遇 ArrowInvalid 即抛到外层 → 整表 freshness=None，
        # 「水位不可测」被误判为 verified。改为跳过缺列/损坏分片，用水位可测
        # 的分片定日期；全部分片都测不到才回 None（语义与原来一致）。
        vals = []
        for f in files:
            try:
                vals.append(
                    pd.read_parquet(os.path.join(store, f), columns=[dcol])[dcol]
                )
            except Exception:
                continue
        if not vals:
            return None
        return _dmax(pd.concat(vals, ignore_index=True))
    except Exception:
        return None


def _verify_status(e, stale_before):
    """catalog 条目 verification_status 推导（2026-10-17 实装，结束全 unknown）。

    有序判定：schema_error > verified_external > placeholder_empty >
    manifest_missing > static_no_date > verified_stale > verified。依据全部
    来自本次重建实测信号（schema_errors/readiness_basis/rows/files/
    manifest_status/freshness/static_no_date），不引入额外 IO；
    stale_before=YYYYMMDD（7 天口径，与治理统计一致）。各写入点的
    'unknown' 仅为初始值，build() 统一覆盖。

    WO-QT-20261021-102 新增 static_no_date："无时间维的静态数据"
    （目前仅 chinese-dictionary-of-financial-sentiment 词表）。它排在
    schema_error/placeholder_empty/manifest_missing **之后**——真实故障
    （footer 损坏 / 空表 / manifest 缺登记）必须继续盖过它，否则新状态会
    变成把这些告警吞掉的洞；排在 verified_stale 之前——无日期列的表谈不上
    "日期陈旧"，落 verified 又会冒充「已验证且新鲜」。判定只看 TABLE_DEFS
    里的显式 opt-in 标记（条目字段 static_no_date），不做"date_col 为空即
    静态"的猜测：catalog 里 date_col=None 的 36 张表多为快照/成分/新闻表，
    一刀切会把它们的判定一起改掉。
    """
    if e.get("schema_errors"):
        return "schema_error"
    if e.get("readiness_basis") == "external_db":
        return "verified_external"
    if not ((e.get("rows") or 0) > 0 or (e.get("files") or 0) > 0):
        return "placeholder_empty"
    if e.get("manifest_status") == "missing":
        return "manifest_missing"
    if e.get("static_no_date"):
        return "static_no_date"
    f = str(e.get("freshness") or "")
    if f[:8].isdigit() and f[:8] < stale_before:
        return "verified_stale"
    return "verified"


def _scan_dir(source, root, mpath, tbl, mf, store=None):
    """扫单个表目录 → catalog 条目。store 可覆盖（us_legacy→data/us）。"""
    store = store or os.path.join(root, tbl)
    files = sorted(
        os.path.basename(f) for f in glob.glob(os.path.join(store, "*.parquet"))
    )
    d = TABLE_DEFS.get(tbl, {})
    layout = d.get("layout") or (_infer_layout(files) if files else "part")
    schema, freshness = [], None
    schema_inspected, schema_errors = 0, []
    if files:
        try:
            from pyarrow.parquet import ParquetFile

            declared = set(d.get("primary_key") or [])
            # 单片损坏仍继续检查其余footer；保留错误，不能标全面结构。
            for filename in files:
                try:
                    names = ParquetFile(
                        os.path.join(store, filename)
                    ).schema_arrow.names
                except Exception:
                    schema_errors.append(filename)
                    continue
                schema_inspected += 1
                schema.extend(c for c in names if c not in schema)
                if layout != "part" and declared.issubset(schema):
                    break
        except Exception:
            schema = []
            schema_errors.append(files[0])
    dcol, ccol = _dcol(tbl, layout), _ccol(tbl)
    if files and schema:
        if dcol is not None and dcol not in schema:
            dcol = d.get("date_col") or next(
                (
                    c
                    for c in (
                        "trade_date",
                        "cal_date",
                        "ann_date",
                        "end_date",
                        "trade_time",
                        "time",
                        "list_date",
                    )
                    if c in schema
                ),
                None,
            )
        ccol = (
            ccol
            if ccol in schema
            else next((c for c in ("ts_code", "code", "symbol") if c in schema), None)
        )
    freshness = _freshness(store, layout, dcol, files)
    ent = mf.get(tbl) or {}
    done = bool(ent.get("done")) if ent else bool(files)
    primary_key = d.get("primary_key", [c for c in (ccol, dcol) if c])
    return {
        "source": source,
        "store": store.replace("\\", "/"),
        "layout": layout,
        "schema": schema,
        "date_col": dcol,
        "code_col": ccol,
        "schema_scope": "all_footers"
        if layout == "part"
        and schema_inspected == len(files)
        and files
        and not schema_errors
        else "sampled"
        if schema_inspected
        else "unknown",
        "schema_files_inspected": schema_inspected,
        "schema_errors": schema_errors,
        "date_status": d.get("date_status", "registered" if dcol else "unknown"),
        # WO-QT-20261021-102：静态无日期数据的显式标记（默认 False），由
        # _verify_status 消费，决定 verification_status 是否判 static_no_date
        "static_no_date": bool(d.get("static_no_date", False)),
        # 2026-11-02 task-47：日历/未来期表显式标记（date_col 可含未来日期，qualify_probe 不判 future_date）
        "future_ok": bool(d.get("future_ok", False)),
        "primary_key": [c for c in primary_key if c in schema],
        "freq": d.get("freq", "D"),
        "priority": d.get("priority", 1),
        "manifest": (
            None
            if d.get("manifest", "unset") is None
            else (mpath or "").replace("\\", "/") or None
        ),
        "freshness": freshness,
        "rows": ent.get("rows") if ent else None,
        "files": len(files),
        "done": done,
        "readiness_basis": "manifest" if ent else "file_presence",
        "verification_status": "unknown",
        "manifest_status": (
            "not_applicable"
            if d.get("manifest", "unset") is None
            else "closed"
            if ent.get("done")
            else "open"
            if tbl in mf
            else "missing"
        ),
        "deprecated": d.get("deprecated", False),
        "desc": d.get("desc", ""),
    }


def _load_manifest(path):
    """读仓库/包清单 → {表名: 表级记录}；缺失或损坏返回 {}。

    tdx/tushare/xueqiu 的 _manifest.json 是扁平结构（顶层即表名），CnOpenData
    包是双层壳 {"version":1, "tables": {表名: 记录}}。此处统一摊平：带
    "tables" 子字典则取其内层，否则原样返回（其余仓行为不变）。不摊平会让
    mf.get(tbl) 恒为 None → rows/manifest_status 全失明（编造 manifest_missing）。
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("tables")
    return inner if isinstance(inner, dict) else raw


def _mark_static_done(tables, mf):
    """CnOpenData 静态包：补齐 done / readiness_basis（WO-QT-20261021-088）。

    包清单表级记录只有 rows/errors/fetched_at/parsed_at/files，没有增量仓的
    done 字段；其语义是「一次落地、解析无错即终态」。故以 errors 空 + 有数据
    判 done=True，并让 readiness_basis=manifest_status=closed 与登记口径一致。
    datamart._ready_one 对 manifest 内无该表键时回落 entry['done']，置真即
    让 ready()/read() 可达（无需改 datamart）。

    返回置真的表数。
    """
    marked = 0
    for name, e in tables.items():
        if e.get("source") != CNOD_SOURCE:
            continue
        rec = mf.get(name) or {}
        if rec.get("errors"):
            continue  # 解析留错：不认定终态
        if (rec.get("rows") or 0) <= 0 and (e.get("files") or 0) <= 0:
            continue  # 无行且无分片：保持未完成
        e["done"] = True
        e["readiness_basis"] = "manifest"
        e["manifest_status"] = "closed"
        marked += 1
    return marked


def _scan_sqlite(mf):
    """tdx_info.db 三表 → catalog 条目（layout=sqlite）。"""
    out = {}
    if not os.path.exists(INFO_DB):
        return out
    dcol = {
        "stock_notice": "notice_date",
        "stock_news": "pub_date",
        "stock_report": "pub_date",
    }
    fresh, schemas, rows = {}, {}, {}
    try:
        conn = sqlite3.connect(
            "file:" + INFO_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=10
        )
        for t, c in dcol.items():
            try:
                schemas[t] = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
                rows[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
                v = conn.execute("SELECT MAX(%s) FROM %s" % (c, t)).fetchone()  # nosec B608 - 值参数化/内部标识符（已抽查）
                s = str(v[0]) if v and v[0] else ""
                fresh[t] = s[:10].replace("-", "") if s[:4].isdigit() else None
            except sqlite3.OperationalError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/build_catalog.py", exc)
        conn.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/build_catalog.py", exc)
    for t in ("stock_notice", "stock_news", "stock_report"):
        ent = mf.get(t) or {}
        out[t] = {
            "source": "xtcloud",
            "store": INFO_DB.replace("\\", "/"),
            "layout": "sqlite",
            "schema": schemas.get(t, []),
            "date_col": dcol[t],
            "schema_scope": "sqlite_pragma" if schemas.get(t) else "unknown",
            "code_col": "ts_code",
            "freq": "D",
            "priority": 1,
            "primary_key": [],
            "manifest": WAREHOUSES[0][2].replace("\\", "/"),
            "freshness": fresh.get(t),
            "rows": rows.get(t),
            "files": 0,
            "done": bool(ent.get("done")),
            "deprecated": False,
            "desc": "tdx 信息库（公告/个股新闻/研报）",
            "readiness_basis": "manifest" if ent else "sqlite_table_presence",
            "verification_status": "unknown",
            "manifest_status": "closed"
            if ent.get("done")
            else "open"
            if t in mf
            else "missing",
        }
    return out


def _scan_xq_info_db(mf):
    """xueqiu_info.db 三表 → catalog 条目（layout=sqlite，仿 _scan_sqlite）。

    三表：xq_status/xq_hot_post（pk=status_id）、xq_comment
    （pk=comment_id，复合索引 symbol+created_at）；date_col=created_at，
    freshness=MAX(created_at)（设计 §3.3 ③）。
    """
    out = {}
    if not os.path.exists(XQ_INFO_DB):
        return out
    dcol = {
        "xq_status": "created_at",
        "xq_hot_post": "created_at",
        "xq_comment": "created_at",
    }
    desc = {
        "xq_status": "雪球分类帖流（L3 直爬，讨论热度原始层）",
        "xq_hot_post": "雪球全站热帖（L3 直爬，含 hot_rank）",
        "xq_comment": "雪球个股讨论帖（L3 直爬，池内活跃股）",
    }
    fresh, schemas, rows = {}, {}, {}
    try:
        conn = sqlite3.connect(
            "file:" + XQ_INFO_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=10
        )
        for t, c in dcol.items():
            try:
                schemas[t] = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
                rows[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
                v = conn.execute("SELECT MAX(%s) FROM %s" % (c, t)).fetchone()  # nosec B608 - 值参数化/内部标识符（已抽查）
                s = str(v[0]) if v and v[0] else ""
                fresh[t] = s[:10].replace("-", "") if s[:4].isdigit() else None
            except sqlite3.OperationalError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/build_catalog.py", exc)
        conn.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/build_catalog.py", exc)
    xq_mf = (os.path.join(HERE, "data", "xueqiu", "_manifest.json")).replace("\\", "/")
    for t in ("xq_status", "xq_hot_post", "xq_comment"):
        ent = mf.get(t) or {}
        out[t] = {
            "source": "xueqiu",
            "store": XQ_INFO_DB.replace("\\", "/"),
            "layout": "sqlite",
            "schema": schemas.get(t, []),
            "date_col": dcol[t],
            "schema_scope": "sqlite_pragma" if schemas.get(t) else "unknown",
            "code_col": "ts_code",
            "freq": "D",
            "priority": 1,
            "primary_key": [],
            "manifest": xq_mf,
            "freshness": fresh.get(t),
            "rows": rows.get(t),
            "files": 0,
            "done": bool(ent.get("done")),
            "deprecated": False,
            "desc": desc[t],
            "readiness_basis": "manifest" if ent else "sqlite_table_presence",
            "verification_status": "unknown",
            "manifest_status": (
                "closed" if ent.get("done") else "open" if t in mf else "missing"
            ),
        }
    return out


def _empty_entry(name, source, store, layout, desc=""):
    """空数据占位 entry（设计 §3.2：采集器首跑前目录/库不存在不 fail）。

    status='empty' 标记占位；done=False → datamart.ready()=False，
    读层自动跳过，注册本身可见可查。
    """
    return {
        "source": source,
        "store": str(store).replace("\\", "/"),
        "layout": layout,
        "schema": [],
        "date_col": None,
        "code_col": None,
        "schema_scope": "unknown",
        "schema_files_inspected": 0,
        "schema_errors": [],
        "date_status": "unknown",
        "primary_key": [],
        "freq": TABLE_DEFS.get(name, {}).get("freq", "D"),
        "priority": TABLE_DEFS.get(name, {}).get("priority", 1),
        "manifest": None,
        "freshness": None,
        "rows": None,
        "files": 0,
        "done": False,
        "deprecated": False,
        "desc": desc,
        "readiness_basis": "placeholder_empty",
        "verification_status": "unknown",
        "manifest_status": "missing",
        "status": "empty",
    }


def scan_parquet_dir(root, logical_name):
    """设计 §3.2 分支 1：雪球 parquet 表目录扫描（签名照设计）。

    目录存在且有 parquet → 复用 _scan_dir（读首个 parquet schema 推断）；
    目录缺失/空 → 注册占位 entry（status='empty'），不 fail（雪球采集器
    本机从未跑过，data/xueqiu 可能尚不存在——占位注册正好符合设计）。
    """
    if os.path.isdir(root) and glob.glob(os.path.join(root, "*.parquet")):
        parent = os.path.dirname(root)
        mf = {}
        mpath = os.path.join(parent, "_manifest.json")
        if os.path.exists(mpath):
            try:
                mf = json.load(open(mpath, encoding="utf-8"))
            except Exception:
                mf = {}
        ent = _scan_dir("xueqiu", parent, mpath, logical_name, mf)
        ent["status"] = "registered"
        return ent
    return _empty_entry(
        logical_name,
        "xueqiu",
        root,
        "parquet",
        TABLE_DEFS.get(logical_name, {}).get("desc", ""),
    )


def scan_sqlite_tables(db_path, table_pattern="xq_"):
    """设计 §3.2 分支 2：sqlite_master 枚举按前缀过滤（签名照设计）。

    每表一条 entry；db 存在时委托 _scan_xq_info_db（既有实现，store=db
    路径，与 datamart sqlite 读层路由兼容；设计原文 'path 记 db::表名'
    的表名信息由 layout=sqlite + 已知表名清单承载，读层可达优先）。
    db 缺失/空 → 三个已知舆情表各注册一条占位 entry，不 fail。
    """
    entries = {}
    if os.path.exists(db_path):
        entries.update(_scan_xq_info_db({}))
        for e in entries.values():
            e["status"] = "registered"
    known = {
        "xq_status": "雪球分类帖流（L3 直爬，讨论热度原始层）",
        "xq_hot_post": "雪球全站热帖（L3 直爬，含 hot_rank）",
        "xq_comment": "雪球个股讨论帖（L3 直爬，池内活跃股）",
    }
    for t, desc in known.items():
        if t.startswith(table_pattern) and t not in entries:
            entries[t] = _empty_entry(t, "xueqiu", db_path, "sqlite", desc)
    return entries


def scan_jsonl_glob(root, logical_name):
    """设计 §3.2 分支 3：*.jsonl glob 聚合为一逻辑表（签名照设计）。

    按首行 schema 推断；额外记录 file_count / total_lines；脏行跳过计数
    （设计 §8 风险表：guba 150 码文件无统一 schema）。symbol 不在行内，
    由读层从文件名（6 位股码）注入。无文件/全空 → 占位 entry。
    """
    files = sorted(glob.glob(os.path.join(root, "*.jsonl")))
    if not files:
        return {
            "guba_posts": _empty_entry(
                logical_name,
                "guba",
                root,
                "jsonl",
                "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）",
            )
        }
    schema, total_lines, bad_lines = [], 0, 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    total_lines += 1
                    if schema:  # schema 已推断出：只计数
                        continue
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        bad_lines += 1
                        continue
                    schema.extend(row.keys())  # 首有效行 schema 推断
        except OSError:
            continue
    if not total_lines:
        return {
            "guba_posts": _empty_entry(
                logical_name,
                "guba",
                root,
                "jsonl",
                "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）",
            )
        }
    ent = {
        "source": "guba",
        "store": root.replace("\\", "/"),
        "layout": "jsonl",
        "schema": schema,
        "date_col": "ts",
        "code_col": None,
        "schema_scope": "sampled",
        "schema_files_inspected": 1,
        "schema_errors": [],
        "date_status": "registered",
        "primary_key": ["post_id"],
        "freq": "D",
        "priority": 1,
        "manifest": None,
        "freshness": None,
        "rows": total_lines,
        "files": len(files),
        "done": True,
        "deprecated": False,
        "desc": "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）",
        "readiness_basis": "file_presence",
        "verification_status": "unknown",
        "manifest_status": "missing",
        "status": "registered",
        "file_count": len(files),
        "total_lines": total_lines,
        "bad_lines": bad_lines,
    }
    return {"guba_posts": ent}


def _register_intel_tables():
    """设计 §3.1：xq_* 10 表 + guba_posts 统一注册入口（占位容错）。"""
    tables = {}
    xq_root = os.path.join(HERE, "data", "xueqiu")
    for tbl in XQ_PARQUET_TABLES:
        tables[tbl] = scan_parquet_dir(os.path.join(xq_root, tbl), tbl)
    tables.update(scan_sqlite_tables(XQ_INFO_DB, "xq_"))
    tables.update(scan_jsonl_glob(GUBA_DIR, "guba_posts"))
    return tables


def _changelog_note(prev_tables, new_tables):
    """本次表集变化的溯源文案；无已知工单来源时返回 None（沿用历史文案）。

    WO-QT-20261021-088：CnOpenData 6 表首次接入时写明工单号与 freq 选择，
    审计时不必再从 added 反推动因。
    """
    added = set(new_tables) - set(prev_tables)
    cnod_added = [t for t in CNOD_TABLES if t in added]
    if cnod_added:
        return (
            "WO-QT-20261021-088 CnOpenData %d 表接入 catalog"
            "（freq=Y；静态历史包标 not_applicable/verified_stale）" % len(cnod_added)
        )
    return None


def _changelog(prev_tables, new_tables, version, path=None, note=None):
    """catalog_changelog 追加一条（注册/注销表集变化记录，设计 §3.2）。

    path 缺省跟随 OUT 同目录（生产=HERE/data/catalog_changelog.jsonl，
    行为不变）；测试注入 out_path 时随其目录落盘，避免污染生产审计日志
    （Bug：写死 HERE/data，warehouse fixture 触发 56 条垃圾记录）。

    note 缺省沿用历史文案（未传时行为与改造前逐字节一致）；按工单接入的
    表集填自述文案，便于审计时不必反查 added 推断原因。
    """
    added = sorted(set(new_tables) - set(prev_tables))
    removed = sorted(set(prev_tables) - set(new_tables))
    if not added and not removed:
        return None
    path = path or os.path.join(os.path.dirname(OUT), "catalog_changelog.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "version": version,
        "added": added,
        "removed": removed,
        "note": note
        or "kg-graph-storage-design §3 catalog 注册 xq_* 10 表 + guba_posts",
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def build(data_root=None, out_path=None):
    """扫描两仓 → catalog dict（并原子落盘）。测试可传自定义根目录。"""
    tables = {}
    for source, root, mpath in (
        WAREHOUSES
        if data_root is None
        else [
            (
                s,
                os.path.join(data_root, "data", n),
                os.path.join(data_root, "data", n, "_manifest.json"),
            )
            for s, n in (("xtcloud", "tdx"), ("tushare", "tushare"))
        ]
    ):
        mf = _load_manifest(mpath)  # cnopendata 双层壳在此摊平
        for tbl in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            # 点前缀=隐藏目录（.git 等 VCS 残留），误收会产出 placeholder
            # 垃圾表（2026-10-17 实证 data/tushare/.git 被收为 228 表之一）
            if not os.path.isdir(os.path.join(root, tbl)) or tbl.startswith(("_", ".")):
                continue
            tables[tbl] = _scan_dir(source, root, mpath, tbl, mf)
        if data_root is None and source == "xtcloud":  # tdx 信息库三表
            tables.update(_scan_sqlite(mf))
        if data_root is None and source == "xueqiu":  # 雪球舆情库三表
            tables.update(_scan_xq_info_db(mf))
        if data_root is None and source == CNOD_SOURCE:  # 静态历史包终态标记
            _mark_static_done(tables, mf)
    if data_root is None and os.path.isdir(LEGACY_US):
        tables["us_legacy"] = _scan_dir(
            "legacy", os.path.dirname(LEGACY_US), None, "us_legacy", {}, store=LEGACY_US
        )
    if data_root is None and os.path.isdir(CRYPTO_DIR):
        tables["crypto_daily"] = _scan_dir(
            "crypto",
            os.path.dirname(CRYPTO_DIR),
            None,
            "crypto_daily",
            {},
            store=CRYPTO_DIR,
        )
    # 设计 §3：xq_* 10 表 + guba_posts 注册（空数据占位，不 fail）
    prev_tables = {}
    prev_version = 0
    try:
        with open(OUT, encoding="utf-8") as f:
            _prev = json.load(f)
        prev_tables = set(_prev.get("tables") or {})
        prev_version = int(_prev.get("version") or 0)
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/build_catalog.py", exc)
    intel = _register_intel_tables()
    # 真数据优先：主扫描已产出的条目（xq_status/xq_hot_post 等有 sqlite 实表）
    # 不被 intel 占位注册覆盖（2026-10-28 manifest 收尾）。
    tables.update({k: v for k, v in intel.items() if k not in tables})
    tables.update(EXTERNAL_TABLES)  # 章程 §3：外部库表（PG 等）并入目录
    # 生命周期覆盖：planned/deprecated 写入 catalog，供 registry/freshness 消费
    _lc = _lifecycle_module()
    if _lc is not None:
        for _name, _e in tables.items():
            _status = _lc.status_for(_name)
            if _status != "active":
                _e["lifecycle"] = _status
                _e["lifecycle_reason"] = _lc.reason_for(_name)
    # 无源清单声明的表（如 guba_posts：无 manifest 概念）→ not_applicable，
    # 避免把「不适用」误报成 missing（2026-10-28 manifest 收尾）。
    for _e in tables.values():
        if not _e.get("manifest") and _e.get("manifest_status") in ("missing", "open"):
            _e["manifest_status"] = "not_applicable"
    # 验证体系：全表统一推导 verification_status（此前 222 表全 unknown，
    # catalog 仅有登记没有可信度判定——2026-10-17 治理补齐）
    stale_before = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    for e in tables.values():
        e["verification_status"] = _verify_status(e, stale_before)
    # freq 词表校验（软）：未登记值不阻断重建，但必须可见——静默接受拼写
    # 错误（如 "Annual"）会让下游 kg_source_rating 回落 Q 档预算而无人知。
    _bad_freq = sorted(
        {
            str(e.get("freq"))
            for e in tables.values()
            if str(e.get("freq")) not in FREQ_ENUM
        }
    )
    if _bad_freq:
        print(
            "[catalog] 警告：freq 不在枚举 %s 内：%s"
            % ("/".join(FREQ_ENUM), ", ".join(_bad_freq))
        )
    _override = os.environ.get("CATALOG_VERSION_OVERRIDE", "").strip()
    if _override.isdigit():
        version = int(_override)  # 显式版本（治理批次用，如 v13）
    else:
        version = prev_version + 1 if set(tables) != prev_tables else max(prev_version, 1)
    _changelog(
        prev_tables,
        tables,
        version,
        os.path.join(os.path.dirname(out_path or OUT), "catalog_changelog.jsonl"),
        note=_changelog_note(prev_tables, tables),
    )
    cat = {
        "version": version,
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tables": tables,
        "aliases": ALIASES,
    }
    path = out_path or OUT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return cat


def _summarize(cat):
    print(
        "%-18s %-8s %-14s %-6s %-10s %-8s %s"
        % ("table", "source", "layout", "done", "freshness", "files", "desc")
    )
    for t, e in sorted(cat["tables"].items()):
        print(
            "%-18s %-8s %-14s %-6s %-10s %-8d %s"
            % (
                t,
                e["source"],
                e["layout"],
                "Y" if e["done"] else "-",
                e["freshness"] or "—",
                e["files"],
                e["desc"][:24],
            )
        )
    print("\naliases:")
    for a, d in sorted(cat["aliases"].items()):
        print("  %-12s → %s" % (a, " > ".join(d["resolves"])))


# ===== v22（2026-11-02 S3-01）：A4 月/季宏表 freq 修正（D→M/Q），配合 qualify_probe 月末/季末归一 =====
_FREQ_FIX_V22 = {
    "ts_cn_cpi": "M",
    "ts_cn_m": "M",
    "ts_cn_ppi": "M",
    "ts_cn_pmi": "M",
    "ts_cn_gdp": "Q",
}
for _n, _f in _FREQ_FIX_V22.items():
    if _n in TABLE_DEFS:
        TABLE_DEFS[_n]["freq"] = _f
    else:
        TABLE_DEFS.setdefault(_n, {})["freq"] = _f


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "catalog_build", lambda: _summarize(build()))
