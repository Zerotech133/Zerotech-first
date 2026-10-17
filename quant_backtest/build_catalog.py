# -*- coding: utf-8 -*-
"""数据中台目录构建器：扫描 data/tdx + data/tushare 两仓 → data/catalog.json。

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

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "catalog.json")
WAREHOUSES = [  # (source, 数据根, manifest 路径)
    ("xtcloud", os.path.join(HERE, "data", "tdx"),
     os.path.join(HERE, "data", "tdx", "_manifest.json")),
    ("tushare", os.path.join(HERE, "data", "tushare"),
     os.path.join(HERE, "data", "tushare", "_manifest.json")),
    # 雪球仓（xueqiu-collector，设计 xueqiu-collector-design-2026-10-16 §3.3）
    ("xueqiu", os.path.join(HERE, "data", "xueqiu"),
     os.path.join(HERE, "data", "xueqiu", "_manifest.json")),
]
LEGACY_US = os.path.join(HERE, "data", "us")       # 旧美股逐股（deprecated）
CRYPTO_DIR = os.path.join(HERE, "data", "crypto")  # 加密货币日线
INFO_DB = os.path.join(HERE, "data", "tdx", "tdx_info.db")
XQ_INFO_DB = os.path.join(HERE, "data", "xueqiu", "xueqiu_info.db")  # 雪球舆情库
GUBA_DIR = os.path.join(HERE, "data", "guba")  # guba 150 码 jsonl（kg-graph-storage-design §3.1）
# 设计 §3.2：三个新扫描分支对应的雪球 parquet 表（采集器首跑前目录不存在 → 占位注册）
XQ_PARQUET_TABLES = ["xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                     "xq_northbound", "xq_earningforecast", "xq_hot_deal",
                     "xq_inner_trade"]

# 数据地基模块章程 §3（2026-10-16）：外部库表登记（代码即契约）。
# PG/Neo4j 等物理独立存储不搬数据，只在 catalog 登记元数据让读层可路由；
# 每次重建自动并入，不依赖手工编辑 JSON。v2_bar_daily 读路径=datamart
# layout "pg" → data_loader.load_table（直连 5432 zhice-db，失败 docker 兜底）。
EXTERNAL_TABLES = {
    "v2_bar_daily": {
        "source": "pg", "store": "pg://zhice-db:5446/zhice_os_v2/v2_bar_daily",
        "layout": "pg", "schema": ["symbol", "market", "trade_date", "open",
                                   "high", "low", "close", "volume",
                                   "turnover", "pre_close"],
        "date_col": "trade_date", "code_col": "symbol",
        "primary_key": ["symbol", "trade_date"],
        "freq": "D", "priority": 3, "manifest": None,
        "freshness": None, "rows": None, "files": 0,
        "done": True, "deprecated": False,
        "desc": "PG zhice-db 日线（zhice-os v2，多市场；symbol 为库内形态）",
        "readiness_basis": "external_db", "verification_status": "unknown",
        "manifest_status": "not_applicable", "status": "registered",
    },
}

# tdx_read 同款口径：财务类日期列 end_date
END_DATE_TABLES = {"ts_income", "ts_balancesheet", "ts_cashflow",
                   "ts_fina_indicator",
                   "income", "balancesheet", "cashflow", "fina_indicator"}
# 特例声明（未列出的表给默认值）。
TABLE_DEFS = {
    # ── tushare 仓 ──
    "anns_d":          {"date_col": "ann_date", "freq": "D",
                        "desc": "公告（按日分片，tushare 兜底源）"},
    "trade_cal":       {"date_col": "cal_date", "code_col": None,
                        "primary_key": ["exchange", "cal_date"],
                        "desc": "交易日历（SSE 全量）"},
    "stock_basic":     {"primary_key": ["ts_code"],
                        "desc": "A股基础信息（tushare）"},
    "stock_company":   {"code_col": "ts_code", "freq": "Q",
                        "desc": "A股上市公司详情（经营范围/高管/员工，图谱公司实体源）"},
    "income":          {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股利润表（全字段85列，逐码，图谱质量因子源）"},
    "balancesheet":    {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股资产负债表（全字段152列，逐码）"},
    "cashflow":        {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股现金流量表（全字段97列，逐码，FCFF/FCFE）"},
    "fina_indicator":  {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股财务指标（全字段108列，逐码，ROE/ROA/ROIC）"},
    "shibor_lpr":      {"date_col": "date", "freq": "M",
                        "desc": "LPR 利率 1y/5y（monetary_policy 收紧后的替代）"},
    "sf_month":        {"date_col": "month", "freq": "M",
                        "desc": "社融月度（增量/累计/存量，月份 6 位）"},
    "hk_basic":        {"desc": "港股基础信息"},
    "us_basic":        {"desc": "美股基础信息"},
    "us_daily":        {"freq": "D", "desc": "美股日线（逐码，建设中）"},
    "daily":           {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D",
                        "desc": "A股日线深历史（tushare 按日，1990→2023-07）"},
    "adj_factor":      {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D",
                        "desc": "A股复权因子（tushare 按日，回测前复权源）"},
    "cb_basic":        {"desc": "可转债基础"},
    "cb_daily":        {"freq": "D", "desc": "可转债日线（逐码）"},
    "bak_basic":       {"freq": "D", "desc": "备选基础信息（逐码）"},
    "stk_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "A股 1 分钟线（中证800成分，逐码月度）"},
    "idx_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "指数 1 分钟线（逐码月度）"},
    "sw_mins":         {"date_col": "trade_time", "freq": "1min",
                        "desc": "申万指数 1 分钟线（逐码月度）"},
    "etf_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "ETF 1 分钟线（逐码月度）"},
    "sw_classify_l1":  {"code_col": None, "desc": "申万一级分类"},
    "crypto_daily":    {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D", "manifest": None,
                        "desc": "加密货币日线（37 交易对，BTC/ETH 等，逐对单文件）"},
    "index_global":    {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D", "manifest": None,
                        "desc": "全球指数日线（20 指数 HSI/SPX/DJI/IXIC/N225 等，逐指数单文件）"},
    "ts_global_fut":   {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D", "manifest": None,
                        "desc": "外盘商品期货日线（GF_CL00Y/GC00Y/HG00Y 逐品种单文件，"
                                "global_fut_sync 维护；2026-10-16 收编回 data/）"},
    "news":            {"date_col": "datetime", "freq": "D",
                        "desc": "新闻快讯（tushare）"},
    "major_news":      {"date_col": "pub_time", "freq": "D",
                        "desc": "大事提醒（tushare）"},
    "npr":             {"date_col": "pubtime", "freq": "D",
                        "desc": "停复牌（tushare，事件驱动节奏）"},
    "research_report": {"desc": "研报（tushare 兜底源）"},
    "top10_holders":   {"layout": "by_period", "date_col": "ann_date",
                        "primary_key": ["ts_code", "ann_date", "end_date",
                                        "holder_name", "holder_type"],
                        "freq": "Q", "desc": "十大股东（报告期分片，公告日查询）"},
    "top10_floatholders": {"layout": "by_period", "date_col": "ann_date",
                          "primary_key": ["ts_code", "ann_date", "end_date",
                                          "holder_name", "holder_type"],
                          "freq": "Q", "desc": "十大流通股东（报告期分片）"},
    "top_list":        {"date_col": "trade_date", "freq": "D",
                        "desc": "龙虎榜每日明细（tushare 兜底源）"},
    "irm_qa_sh":       {"freq": "D", "desc": "互动易问答-沪（逐码）"},
    "irm_qa_sz":       {"freq": "D", "desc": "互动易问答-深（逐码）"},
    # ── tdx 仓（仅 desc；其余与 tdx_read 口径一致）──
    "ts_adj_factor":  {"date_col": "trade_date", "freq": "D",
                        "desc": "复权因子（tushare 直连，adj_factor_sync 维护，"
                                "AdjFactorSync 18:10）"},
    "ts_daily":        {"freq": "D", "desc": "A股日线行情（xtcloud，真源）"},
    "ts_stock_basic":  {"primary_key": ["ts_code"],
                        "desc": "A股基础信息（xtcloud，真源）"},
    "ts_daily_basic":  {"freq": "D", "desc": "A股每日指标（xtcloud）"},
    "ts_hk_daily":     {"freq": "D", "desc": "港股日线（xtcloud，真源）"},
    "ts_index_daily":  {"freq": "D", "desc": "指数日线（xtcloud）"},
    "ts_index_basic":  {"desc": "指数基础信息"},
    "ts_index_weight": {"desc": "指数成分权重"},
    "ts_etf_basic":    {"desc": "ETF 基础信息"},
    "ts_fund_basic":   {"desc": "基金基础信息"},
    "ts_fund_daily":   {"freq": "D", "desc": "基金日线"},
    "ts_margin_detail": {"freq": "D", "desc": "融资融券明细"},
    "ts_moneyflow":    {"freq": "D", "desc": "个股资金流向"},
    "ts_moneyflow_hsgt": {"freq": "D", "desc": "沪深港通资金"},
    "ts_top_list":     {"freq": "D", "desc": "龙虎榜"},
    "ts_top_inst":     {"freq": "D", "desc": "龙虎榜机构明细"},
    "ts_ths_daily":    {"freq": "D", "desc": "同花顺板块日线"},
    "ts_ths_index":    {"desc": "同花顺指数"},
    "ts_ths_member":   {"desc": "同花顺板块成分"},
    "tdx_sector_daily": {"code_col": "code", "freq": "D",
                         "desc": "通达信板块日线"},
    "tdx_sector_def":  {"desc": "通达信板块定义"},
    "tdx_sector_member": {"desc": "通达信板块成分"},
    "tdx_sector_min":  {"date_col": "trade_date", "code_col": "code",
                        "primary_key": ["code", "trade_date", "trade_time"],
                        "freq": "1min",
                        "desc": "通达信板块分钟线"},
    "tdx_sector_min_tail": {"date_col": "trade_date", "code_col": "code",
                            "primary_key": ["code", "trade_date", "trade_time"],
                            "freq": "1min", "desc": "通达信板块分钟线增量尾部"},
    "us_legacy":       {"source": "legacy", "priority": 2, "deprecated": True,
                        "freq": "D", "manifest": None,
                        "desc": "旧美股日线（逐股单文件，待 us_daily 收口后废弃）"},
}

# 信息说明不推定采集完成或业务覆盖。
for _name, _desc in {
    'ts_balancesheet': 'A股资产负债表（xtcloud）',
    'ts_cashflow': 'A股现金流量表（xtcloud）',
    'ts_fina_indicator': 'A股财务指标（xtcloud）',
    'ts_income': 'A股利润表（xtcloud）',
    'cyq_chips': '筹码分布（逐码，文件名提供证券身份）',
    'cyq_perf': '筹码胜率与成本指标',
    'index_weight': '指数成分及权重（成分身份见 con_code）',
    'irm_qa_sh_probe': '沪市互动问答单日探针（不代表全量覆盖）',
    'irm_qa_sz_probe': '深市互动问答单日探针（不代表全量覆盖）',
    'limit_list_d': '每日涨跌停明细',
    'stk_holdernumber': '股东户数',
    'stk_managers': '上市公司管理层信息',
    'sw_l1_member': '申万一级行业成分关系（静态集合）',
}.items():
    TABLE_DEFS.setdefault(_name, {})['desc'] = _desc
for _market, _label in (('hk', '港股'), ('us', '美股')):
    for _table, _label_table in (('balancesheet', '资产负债表'), ('cashflow', '现金流量表'),
                               ('fina_indicator', '财务指标'), ('income', '利润表')):
        TABLE_DEFS[_market + '_' + _table] = {
            'desc': _label + _label_table + '（Tushare）', 'date_col': 'end_date', 'freq': 'Q'}
TABLE_DEFS['ts_rt_min'] = {'desc': '实时分钟行情（xtcloud，按实际 time 字段核验）',
                          'date_col': 'time', 'freq': '1min'}
# ── 雪球仓（xueqiu，设计 §3.3：7 张 parquet 表）──
TABLE_DEFS.update({
    "xq_quote_snap":      {"date_col": "trade_date", "code_col": "ts_code",
                           "freq": "D",
                           "desc": "雪球收盘快照（quotec 免 token，缠论池）"},
    "xq_kline_d":         {"date_col": "timestamp", "code_col": "ts_code",
                           "freq": "D",
                           "desc": "雪球前复权日线（逐码，交叉验证源）"},
    "xq_capital_flow":    {"date_col": "trade_date", "code_col": "ts_code",
                           "freq": "D", "desc": "雪球个股资金流"},
    "xq_northbound":      {"date_col": "trade_date", "code_col": "ts_code",
                           "freq": "D", "desc": "雪球北向持股（空日不落片）"},
    "xq_earningforecast": {"date_col": "ann_date", "code_col": "ts_code",
                           "freq": "W", "desc": "券商盈利预测（雪球）"},
    "xq_hot_deal":        {"date_col": "trade_date", "code_col": "ts_code",
                           "freq": "D",
                           "desc": "雪球热度交易榜（akshare 补充源）"},
    "xq_inner_trade":     {"date_col": "trade_date", "code_col": "ts_code",
                           "freq": "D",
                           "desc": "雪球内部交易/高管增减持（akshare）"},
})
# K-STOCK-398（10-15）：打板情绪周期底座。源limit_list_d 自算，不依赖 tushare 打板套餐。
TABLE_DEFS['ts_emotion_daily'] = {
    'desc': '打板情绪周期日频面板（涨停/炸板/最高板/封板率/晋级率/封单额）',
    'date_col': 'trade_date', 'freq': 'D', 'layout': 'single'}
TABLE_DEFS['ts_emotion_ladder'] = {
    'desc': '连板梯队明细（≥2板：连板数/炸板/封单额/首末封/流通市值）',
    'date_col': 'trade_date', 'freq': 'D', 'layout': 'single'}
for _name in ('tdx_sector_def', 'tdx_sector_member', 'ts_ths_member',
              'stock_company', 'sw_classify_l1', 'sw_l1_member'):
    TABLE_DEFS[_name]['date_col'] = None
    TABLE_DEFS[_name]['date_status'] = 'not_applicable'

# 财报主键保留同报告期的报表类型及披露版本；不存在的列在扫描时剔除。
for _tbl in ("income", "balancesheet", "cashflow", "fina_indicator"):
    for _name in (_tbl, "ts_" + _tbl):
        TABLE_DEFS.setdefault(_name, {})["primary_key"] = [
            "ts_code", "end_date", "ann_date", "f_ann_date", "report_type",
            "comp_type", "end_type", "update_flag"]
for _tbl in ("anns_d", "research_report", "news", "major_news", "npr"):
    TABLE_DEFS[_tbl]["primary_key"] = []
# 高管事件表无时序唯一键：同日公告多名高管/一人多职都是独立事件行，
# 主键必须覆盖姓名+职务+任期，否则 dm.read 去重会折叠（57.5万→12万判例）。
# 不在 schema 的列扫描时自动剔除（_scan_dir L456 同款过滤）。
TABLE_DEFS.setdefault("stk_managers", {})["primary_key"] = [
    "ts_code", "name", "title", "ann_date", "begin_date", "end_date"]

# §5.3 逻辑别名（resolves 按真源优先级排序）
ALIASES = {
    "daily":       {"resolves": ["ts_daily", "daily"],
                    "primary": "ts_daily",
                    "note": "xtcloud 真源（2023-07 起）+ tushare 深历史（1990 起），"
                            "零重叠 concat，read() 按(code,date)去重"},
    "stock_basic": {"resolves": ["ts_stock_basic", "stock_basic"],
                    "primary": "ts_stock_basic",
                    "merge_key": ["ts_code"],
                    "note": "xtcloud 优先，tushare 补深度"},
    "hk_daily":    {"resolves": ["ts_hk_daily"],
                    "note": "tushare hk_daily 未建仓，仅 xtcloud"},
    "us_daily":    {"resolves": ["us_daily", "us_legacy"],
                    "primary": "us_daily",
                    "note": "tushare 收口前降级旧美股（done 联动自动切换）"},
    "notice":      {"resolves": ["stock_notice", "anns_d"],
                    "primary": "stock_notice",
                    "merge_key": [],
                    "merge_mode": "fallback",
                    "note": "公告：tdx 优先，tushare 补深度"},
    "report":      {"resolves": ["stock_report", "research_report"],
                    "primary": "stock_report",
                    "merge_key": [],
                    "merge_mode": "fallback",
                    "note": "研报：tdx 优先，tushare 补深度"},
    "news":        {"resolves": ["stock_news"],
                    "note": "JEV news.db 独立通道，读层 v1 未路由"},
    "index_daily": {"resolves": ["ts_index_daily"],
                    "note": "指数日线（xtcloud）"},
    "moneyflow":   {"resolves": ["ts_moneyflow"],
                    "note": "个股资金流向（xtcloud）"},
    "margin":      {"resolves": ["ts_margin_detail"],
                    "note": "融资融券明细（xtcloud）"},
    "income":      {"resolves": ["income", "ts_income"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股利润表：tushare 全字段优先，xtcloud 有限字段降级"},
    "balancesheet":{"resolves": ["balancesheet", "ts_balancesheet"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股资产负债表：tushare 全字段优先，xtcloud 降级"},
    "cashflow":    {"resolves": ["cashflow", "ts_cashflow"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股现金流量表：tushare 全字段优先，xtcloud 降级"},
    "fina_indicator":{"resolves": ["fina_indicator", "ts_fina_indicator"],
                      "merge_key": ["ts_code", "end_date"],
                      "note": "A股财务指标：tushare 全字段优先，xtcloud 降级"},
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
    if 'date_col' in definition:
        return definition['date_col']
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
                        continue          # 单文件损坏：看更旧的
                    if dcol not in df.columns:
                        break              # 列不存在：回退文件名口径
                    v = _dmax(df[dcol])
                    if v:
                        return v           # 最新非空分片的数据日期
                else:
                    return None            # 循环走完=全是空片
            return max(re.search(r"d_(\d{8})", f).group(1) for f in dated)
        if layout == "by_code_month":
            # K100：文件名（月）降序 footer 筛跳空壳，首个非空读 dcol
            cands = sorted((f for f in files
                            if re.search(r"_\d{6}\.parquet$", f)),
                           reverse=True)
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue          # 空壳：停牌/未披露个体常态
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue          # 损坏或列缺失：看次新
                if v:
                    return v
            return None
        if layout == "by_code":
            # K100：mtime 降序 footer 筛跳空壳（聚集批可达数百个，
            # 实证 irm_qa_sz rank347 才见非空）。
            # K102：非空中取前 8 个读 dcol 取 max——「首个非空」有随机
            # 偏差（同批写入 mtime 平局，排序不稳定，实证 us_daily 取到
            # 停更票尾 09-30 而全表主力水位 10-02）；8 采样全停更概率≈0
            cands = sorted(files, key=lambda f: os.path.getmtime(
                os.path.join(store, f)), reverse=True)
            seen = 0
            best = None
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue          # 空壳：增量拉取无新数据的个体
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue          # 损坏或列缺失：看次新
                if v and (not best or v > best):
                    best = v
                seen += 1
                if seen >= 8:
                    break
            return best
        # part：全分片只读日期列（分片数少，代价可忽略）
        vals = [pd.read_parquet(os.path.join(store, f), columns=[dcol])[dcol]
                for f in files]
        if not vals:
            return None
        return _dmax(pd.concat(vals, ignore_index=True))
    except Exception:
        return None


def _verify_status(e, stale_before):
    """catalog 条目 verification_status 推导（2026-10-17 实装，结束全 unknown）。

    有序判定：schema_error > verified_external > placeholder_empty >
    manifest_missing > verified_stale > verified。依据全部来自本次重建
    实测信号（schema_errors/readiness_basis/rows/files/manifest_status/
    freshness），不引入额外 IO；stale_before=YYYYMMDD（7 天口径，与
    治理统计一致）。各写入点的 'unknown' 仅为初始值，build() 统一覆盖。
    """
    if e.get("schema_errors"):
        return 'schema_error'
    if e.get("readiness_basis") == 'external_db':
        return 'verified_external'
    if not ((e.get("rows") or 0) > 0 or (e.get("files") or 0) > 0):
        return 'placeholder_empty'
    if e.get("manifest_status") == 'missing':
        return 'manifest_missing'
    f = str(e.get("freshness") or "")
    if f[:8].isdigit() and f[:8] < stale_before:
        return 'verified_stale'
    return 'verified'


def _scan_dir(source, root, mpath, tbl, mf, store=None):
    """扫单个表目录 → catalog 条目。store 可覆盖（us_legacy→data/us）。"""
    store = store or os.path.join(root, tbl)
    files = sorted(os.path.basename(f) for f in
                   glob.glob(os.path.join(store, "*.parquet")))
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
                    names = ParquetFile(os.path.join(store, filename)).schema_arrow.names
                except Exception:
                    schema_errors.append(filename)
                    continue
                schema_inspected += 1
                schema.extend(c for c in names if c not in schema)
                if layout != 'part' and declared.issubset(schema):
                    break
        except Exception:
            schema = []
            schema_errors.append(files[0])
    dcol, ccol = _dcol(tbl, layout), _ccol(tbl)
    if files and schema:
        if dcol is not None and dcol not in schema:
            dcol = d.get("date_col") or next(
                (c for c in ("trade_date", "cal_date", "ann_date",
                             "end_date", "trade_time", "time", "list_date")
                 if c in schema), None)
        ccol = ccol if ccol in schema else next(
            (c for c in ("ts_code", "code", "symbol") if c in schema), None)
    freshness = _freshness(store, layout, dcol, files)
    ent = mf.get(tbl) or {}
    done = bool(ent.get("done")) if ent else bool(files)
    primary_key = d.get("primary_key", [c for c in (ccol, dcol) if c])
    return {
        "source": source, "store": store.replace("\\", "/"), "layout": layout,
        "schema": schema, "date_col": dcol, "code_col": ccol,
        "schema_scope": 'all_footers' if layout == 'part' and schema_inspected == len(files) and files and not schema_errors else 'sampled' if schema_inspected else 'unknown',
        "schema_files_inspected": schema_inspected, "schema_errors": schema_errors,
        "date_status": d.get('date_status', 'registered' if dcol else 'unknown'),
        "primary_key": [c for c in primary_key if c in schema],
        "freq": d.get("freq", "D"),
        "priority": d.get("priority", 1),
        "manifest": (None if d.get("manifest", "unset") is None
                     else (mpath or "").replace("\\", "/") or None),
        "freshness": freshness,
        "rows": ent.get("rows") if ent else None,
        "files": len(files), "done": done,
        "readiness_basis": 'manifest' if ent else 'file_presence',
        "verification_status": 'unknown',
        "manifest_status": ('not_applicable' if d.get('manifest', 'unset') is None else
                            'closed' if ent.get('done') else 'open' if tbl in mf else 'missing'),
        "deprecated": d.get("deprecated", False),
        "desc": d.get("desc", ""),
    }


def _scan_sqlite(mf):
    """tdx_info.db 三表 → catalog 条目（layout=sqlite）。"""
    out = {}
    if not os.path.exists(INFO_DB):
        return out
    dcol = {"stock_notice": "notice_date", "stock_news": "pub_date",
            "stock_report": "pub_date"}
    fresh, schemas, rows = {}, {}, {}
    try:
        conn = sqlite3.connect('file:' + INFO_DB.replace('\\', '/') + '?mode=ro', uri=True, timeout=10)
        for t, c in dcol.items():
            try:
                schemas[t] = [r[1] for r in conn.execute('PRAGMA table_info(%s)' % t)]
                rows[t] = conn.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                v = conn.execute("SELECT MAX(%s) FROM %s" % (c, t)).fetchone()
                s = str(v[0]) if v and v[0] else ""
                fresh[t] = s[:10].replace("-", "") if s[:4].isdigit() else None
            except sqlite3.OperationalError:
                pass
        conn.close()
    except Exception:
        pass
    for t in ("stock_notice", "stock_news", "stock_report"):
        ent = mf.get(t) or {}
        out[t] = {
            "source": "xtcloud", "store": INFO_DB.replace("\\", "/"),
            "layout": "sqlite", "schema": schemas.get(t, []), "date_col": dcol[t],
            "schema_scope": 'sqlite_pragma' if schemas.get(t) else 'unknown',
            "code_col": "ts_code", "freq": "D", "priority": 1,
            "primary_key": [],
            "manifest": WAREHOUSES[0][2].replace("\\", "/"),
            "freshness": fresh.get(t), "rows": rows.get(t),
            "files": 0, "done": bool(ent.get("done")),
            "deprecated": False, "desc": "tdx 信息库（公告/个股新闻/研报）",
            "readiness_basis": 'manifest' if ent else 'sqlite_table_presence',
            "verification_status": 'unknown',
            "manifest_status": 'closed' if ent.get('done') else 'open' if t in mf else 'missing',
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
    dcol = {"xq_status": "created_at", "xq_hot_post": "created_at",
            "xq_comment": "created_at"}
    desc = {"xq_status": "雪球分类帖流（L3 直爬，讨论热度原始层）",
            "xq_hot_post": "雪球全站热帖（L3 直爬，含 hot_rank）",
            "xq_comment": "雪球个股讨论帖（L3 直爬，池内活跃股）"}
    fresh, schemas, rows = {}, {}, {}
    try:
        conn = sqlite3.connect(
            'file:' + XQ_INFO_DB.replace('\\', '/') + '?mode=ro',
            uri=True, timeout=10)
        for t, c in dcol.items():
            try:
                schemas[t] = [r[1] for r in
                              conn.execute('PRAGMA table_info(%s)' % t)]
                rows[t] = conn.execute(
                    'SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                v = conn.execute(
                    "SELECT MAX(%s) FROM %s" % (c, t)).fetchone()
                s = str(v[0]) if v and v[0] else ""
                fresh[t] = s[:10].replace("-", "") if s[:4].isdigit() \
                    else None
            except sqlite3.OperationalError:
                pass
        conn.close()
    except Exception:
        pass
    xq_mf = (os.path.join(HERE, "data", "xueqiu", "_manifest.json")
             ).replace("\\", "/")
    for t in ("xq_status", "xq_hot_post", "xq_comment"):
        ent = mf.get(t) or {}
        out[t] = {
            "source": "xueqiu", "store": XQ_INFO_DB.replace("\\", "/"),
            "layout": "sqlite", "schema": schemas.get(t, []),
            "date_col": dcol[t],
            "schema_scope": 'sqlite_pragma' if schemas.get(t) else 'unknown',
            "code_col": "ts_code", "freq": "D", "priority": 1,
            "primary_key": [], "manifest": xq_mf,
            "freshness": fresh.get(t), "rows": rows.get(t),
            "files": 0, "done": bool(ent.get("done")),
            "deprecated": False, "desc": desc[t],
            "readiness_basis": 'manifest' if ent else 'sqlite_table_presence',
            "verification_status": 'unknown',
            "manifest_status": ('closed' if ent.get('done') else
                                'open' if t in mf else 'missing'),
        }
    return out


def _empty_entry(name, source, store, layout, desc=""):
    """空数据占位 entry（设计 §3.2：采集器首跑前目录/库不存在不 fail）。

    status='empty' 标记占位；done=False → datamart.ready()=False，
    读层自动跳过，注册本身可见可查。
    """
    return {
        "source": source, "store": str(store).replace("\\", "/"),
        "layout": layout, "schema": [], "date_col": None, "code_col": None,
        "schema_scope": "unknown", "schema_files_inspected": 0,
        "schema_errors": [], "date_status": "unknown", "primary_key": [],
        "freq": TABLE_DEFS.get(name, {}).get("freq", "D"),
        "priority": TABLE_DEFS.get(name, {}).get("priority", 1),
        "manifest": None, "freshness": None, "rows": None, "files": 0,
        "done": False, "deprecated": False, "desc": desc,
        "readiness_basis": "placeholder_empty", "verification_status": "unknown",
        "manifest_status": "missing", "status": "empty",
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
    return _empty_entry(logical_name, "xueqiu", root, "parquet",
                        TABLE_DEFS.get(logical_name, {}).get("desc", ""))


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
    known = {"xq_status": "雪球分类帖流（L3 直爬，讨论热度原始层）",
             "xq_hot_post": "雪球全站热帖（L3 直爬，含 hot_rank）",
             "xq_comment": "雪球个股讨论帖（L3 直爬，池内活跃股）"}
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
        return {"guba_posts": _empty_entry(
            logical_name, "guba", root, "jsonl",
            "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）")}
    schema, total_lines, bad_lines = [], 0, 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    total_lines += 1
                    if schema:          # schema 已推断出：只计数
                        continue
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        bad_lines += 1
                        continue
                    schema.extend(row.keys())   # 首有效行 schema 推断
        except OSError:
            continue
    if not total_lines:
        return {"guba_posts": _empty_entry(
            logical_name, "guba", root, "jsonl",
            "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）")}
    ent = {
        "source": "guba", "store": root.replace("\\", "/"),
        "layout": "jsonl", "schema": schema, "date_col": "ts",
        "code_col": None, "schema_scope": "sampled",
        "schema_files_inspected": 1, "schema_errors": [],
        "date_status": "registered", "primary_key": ["post_id"],
        "freq": "D", "priority": 1, "manifest": None,
        "freshness": None, "rows": total_lines, "files": len(files),
        "done": True, "deprecated": False,
        "desc": "东方财富股吧帖（150 码轮转 jsonl，symbol=文件名）",
        "readiness_basis": "file_presence", "verification_status": "unknown",
        "manifest_status": "missing", "status": "registered",
        "file_count": len(files), "total_lines": total_lines,
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


def _changelog(prev_tables, new_tables, version, path=None):
    """catalog_changelog 追加一条（注册/注销表集变化记录，设计 §3.2）。

    path 缺省跟随 OUT 同目录（生产=HERE/data/catalog_changelog.jsonl，
    行为不变）；测试注入 out_path 时随其目录落盘，避免污染生产审计日志
    （Bug：写死 HERE/data，warehouse fixture 触发 56 条垃圾记录）。
    """
    added = sorted(set(new_tables) - set(prev_tables))
    removed = sorted(set(prev_tables) - set(new_tables))
    if not added and not removed:
        return None
    path = path or os.path.join(os.path.dirname(OUT),
                                "catalog_changelog.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "version": version, "added": added, "removed": removed,
        "note": "kg-graph-storage-design §3 catalog 注册 xq_* 10 表 + guba_posts",
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def build(data_root=None, out_path=None):
    """扫描两仓 → catalog dict（并原子落盘）。测试可传自定义根目录。"""
    tables = {}
    for source, root, mpath in (WAREHOUSES if data_root is None else
                                [(s, os.path.join(data_root, "data", n),
                                  os.path.join(data_root, "data", n,
                                               "_manifest.json"))
                                 for s, n in (("xtcloud", "tdx"),
                                              ("tushare", "tushare"))]):
        mf = {}
        if os.path.exists(mpath):
            try:
                mf = json.load(open(mpath, encoding="utf-8"))
            except Exception:
                mf = {}
        for tbl in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not os.path.isdir(os.path.join(root, tbl)) or \
                    tbl.startswith("_"):
                continue
            tables[tbl] = _scan_dir(source, root, mpath, tbl, mf)
        if data_root is None and source == "xtcloud":   # tdx 信息库三表
            tables.update(_scan_sqlite(mf))
        if data_root is None and source == "xueqiu":    # 雪球舆情库三表
            tables.update(_scan_xq_info_db(mf))
    if data_root is None and os.path.isdir(LEGACY_US):
        tables["us_legacy"] = _scan_dir(
            "legacy", os.path.dirname(LEGACY_US), None, "us_legacy", {},
            store=LEGACY_US)
    if data_root is None and os.path.isdir(CRYPTO_DIR):
        tables["crypto_daily"] = _scan_dir(
            "crypto", os.path.dirname(CRYPTO_DIR), None, "crypto_daily", {},
            store=CRYPTO_DIR)
    # 设计 §3：xq_* 10 表 + guba_posts 注册（空数据占位，不 fail）
    prev_tables = {}
    prev_version = 0
    try:
        with open(OUT, encoding="utf-8") as f:
            _prev = json.load(f)
        prev_tables = set(_prev.get("tables") or {})
        prev_version = int(_prev.get("version") or 0)
    except (OSError, ValueError):
        pass
    intel = _register_intel_tables()
    tables.update(intel)
    tables.update(EXTERNAL_TABLES)   # 章程 §3：外部库表（PG 等）并入目录
    # 验证体系：全表统一推导 verification_status（此前 222 表全 unknown，
    # catalog 仅有登记没有可信度判定——2026-10-17 治理补齐）
    stale_before = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    for e in tables.values():
        e["verification_status"] = _verify_status(e, stale_before)
    version = prev_version + 1 if set(tables) != prev_tables else max(prev_version, 1)
    _changelog(prev_tables, tables, version,
               os.path.join(os.path.dirname(out_path or OUT),
                            "catalog_changelog.jsonl"))
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
    print("%-18s %-8s %-14s %-6s %-10s %-8s %s"
          % ("table", "source", "layout", "done", "freshness", "files",
             "desc"))
    for t, e in sorted(cat["tables"].items()):
        print("%-18s %-8s %-14s %-6s %-10s %-8d %s"
              % (t, e["source"], e["layout"], "Y" if e["done"] else "-",
                 e["freshness"] or "—", e["files"], e["desc"][:24]))
    print("\naliases:")
    for a, d in sorted(cat["aliases"].items()):
        print("  %-12s → %s" % (a, " > ".join(d["resolves"])))


if __name__ == "__main__":
    from linreg import run_with_lineage
    run_with_lineage("kg_daily_sync", "catalog_build",
                     lambda: _summarize(build()))
